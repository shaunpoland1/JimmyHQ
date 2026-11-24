"""Fakes for the village beat tests: a scripted model, a few tools, the other packages'
seams and a simulated clock. Nothing here touches a network or a real model."""

from __future__ import annotations

import random
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from jimmyhq.models import ModelError
from jimmyhq.models.shapes import Reply, ToolCall
from jimmyhq.village.catalog import Done, Tool
from jimmyhq.village.comms import board, breakroom, chat
from jimmyhq.village.rules import Refusal
from jimmyhq.village.runtime import Village
from jimmyhq.village.world import MemoryLog, SimClock
from jimmyhq.village.world.temperament import roll

TZ = "UTC"
ME = "ada"


# ------------------------------------------------------------------ the model
def reply(text: str = "", calls: tuple = (), output_tokens: int = 20) -> Reply:
    return Reply(text=text, provider_id="fake", provider_type="openai_compatible",
                 model="model-a", input_tokens=100, output_tokens=output_tokens,
                 duration_ms=5, fallback_from=None, fallback_reason=None, local=True,
                 tool_calls=tuple(calls))


def call(name: str, _id: str = "call-1", _tokens: int = 20, **arguments) -> Reply:
    """A reply that calls one tool."""
    arguments.setdefault("thought", "I will do this.")
    return reply(calls=(ToolCall(id=_id, name=name, arguments=arguments),),
                 output_tokens=_tokens)


def fails(kind: str) -> ModelError:
    return ModelError(kind, f"The model call failed ({kind}).")


class FakeBrain:
    """A scripted ``Brain``. Each item is a Reply, an exception to raise, or a function
    of (messages, options) returning either. ``calls`` records what was asked."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def chat(self, messages, **options):
        self.calls.append({"messages": list(messages), **options})
        if not self.script:
            raise AssertionError("the model was called more often than the script allows")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, Exception):
            item = item(messages, options)
        if isinstance(item, Exception):
            raise item
        return item


# ------------------------------------------------------------------ the tools
def tool(name, run, modes="WEO", kind="self", params=None, required=(), capability=None):
    return Tool(name=name, kind=kind, modes=modes, description=f"Test tool {name}.",
                params=params or {}, required=tuple(required), run=run, capability=capability)


def some_tools() -> list[Tool]:
    text = {"type": "string"}

    async def write_file(ctx, args):
        path = ctx.v.files_dir(ctx.agent_id) / args["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(args["content"].encode("utf-8"))
        return Done(f"wrote {len(args['content'])} characters to {args['path']}",
                    args_logged={"path": args["path"]}, category=ctx.mode)

    async def read_file(ctx, args):
        path = ctx.v.files_dir(ctx.agent_id) / args["path"]
        if not path.is_file():
            return Done(f"there is no file called {args['path']}", category=ctx.mode, ok=False)
        body = path.read_bytes().decode("utf-8")
        return Done(body[int(args.get("start") or 0):][:30000], category=ctx.mode)

    async def list_files(ctx, args):
        return Done("listed", category=ctx.mode)

    async def note(ctx, args):
        return Done(args["text"])

    async def say(ctx, args):
        ctx.v.log.append("breakroom", ctx.agent_id, ctx.now, text=args["text"])
        return Done("said in the break room")

    async def limit(ctx, args):
        raise Refusal("day_limit", "You have sent 10 messages today, which is the most "
                      "allowed, so nothing was sent. Write it in a file instead.",
                      frustrate=False)

    async def wrong(ctx, args):
        raise Refusal("not_known", "T9 is not on your list, so nothing happened. Use a "
                      "reference from the list.", args_logged={"task": "T9"})

    async def focus_on(ctx, args):
        ctx.v.world.choose_task(ctx.agent_id, args["task"])
        return Done(f"now working on {args['task']}")

    async def boom(ctx, args):
        raise RuntimeError("the tool broke")

    return [
        tool("write_file", write_file, kind="files",
             params={"path": text, "content": text, "replace": {"type": "boolean"}},
             required=("path", "content")),
        tool("read_file", read_file, kind="files",
             params={"path": text, "start": {"type": "integer"}}, required=("path",)),
        tool("list_files", list_files, kind="files", params={"path": text}),
        tool("write_diary", note, params={"text": text}, required=("text",)),
        tool("say", say, modes="S", kind="social", params={"text": text}, required=("text",)),
        tool("message_owner", limit, params={"text": text}, required=("text",)),
        tool("finish_task", wrong, modes="W", params={"task": text}, required=("task",)),
        tool("focus_on", focus_on, modes="W", params={"task": text}, required=("task",)),
        tool("post_to_board", boom, modes="W",
             params={"kind": {"type": "string", "enum": ["need_help", "good_at"]},
                     "text": text, "tags": {"type": "array"}}, required=("kind", "text")),
        tool("open_page", note, capability="desktop", params={"url": text, "text": text},
             required=("url",)),
    ]


# ------------------------------------------------------------------ the seams
# The owner chat, the break room and the work board are the real ones: they only read
# and write the village log.


def status_seam():
    def coworkers_text(v, agent_id):
        others = [o for o in v.world.agents if o != agent_id]
        if not others:
            return ""
        return ("# Your coworkers right now\n"
                + "\n".join(f"- {o.title()}: working" for o in others) + "\n\n")

    return SimpleNamespace(coworkers_text=coworkers_text)


class Box:
    """A seam whose answers a test sets: ``Box(items=[...])`` answers ``items(v, id)``
    with that list. ``calls`` records which functions were asked."""

    def __init__(self, **answers):
        self._answers = answers
        self.calls: list[str] = []

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._answers:
            raise AttributeError(name)

        def fn(v, *args, **kw):
            self.calls.append(name)
            return self._answers[name]

        return fn


def item(ref, title, status="open", source="task"):
    return SimpleNamespace(ref=ref, title=title, status=status, source=source)


class Memory:
    """Stands in for the memory store module."""

    text = ""
    name = None

    @classmethod
    def MemoryStore(cls, agent_dir):  # noqa: N802 - the seam's own name
        return SimpleNamespace(render_for_prompt=lambda: cls.text,
                               locked_name=lambda: cls.name)


def seams(**over) -> dict:
    out = {
        "tools": SimpleNamespace(ALL=some_tools()),
        "comms.chat": chat,
        "comms.breakroom": breakroom,
        "comms.board": board,
        "comms.status": status_seam(),
        "comms.tasklist": Box(items=[item("P1", "Write the newsletter", "ongoing", "priority"),
                                     item("T1", "Check the links")], standing={"P1"}),
        "comms.benchlink": Box(view=[], waiting=[], cards=[]),
        "comms.work": Box(flush=None, queued=[], review_wait=[]),
        "comms.findings": Box(flush=None, queued=[]),
    }
    out.update(over)
    return out


# ---------------------------------------------------------------- the village
def temperament(seed: int = 1) -> dict:
    t = roll(random.Random(seed))
    t["frustration_tolerance"] = 1.0
    t["sleep_offset_minutes"] = 0
    t["voice"] = {"temperature": 0.7, "top_p": 0.8}
    return t


def make_village(core, *script, hour=10, agents=(ME,), **over) -> Village:
    """A village on a simulated clock (Monday 9 November 2026, ``hour`` o'clock) with a
    scripted model, the fake tools and the fake seams. Nothing real is reached."""
    clock = SimClock(datetime(2026, 11, 9, hour, 0, tzinfo=ZoneInfo(TZ)), TZ)
    fakes = seams(**over)
    fakes.setdefault("brain", FakeBrain(*script))
    v = Village(core, clock=clock, log=MemoryLog(), fakes=fakes, only_fakes=True)
    for i, agent_id in enumerate(agents):
        v.world.add_agent(agent_id, temperament(i + 1))
    return v


def step(v: Village, minutes: float, times: int = 1) -> None:
    """Let time pass in the world."""
    for _ in range(times):
        v.clock.advance(minutes)
        v.world.advance(minutes)


def kinds(v: Village, *wanted: str, agent: str = ME) -> list:
    return v.log.query(agent=agent, kinds=list(wanted))


def owner_says(v: Village, text: str, urgent: bool = False, agent: str = ME):
    data = {"sender": "owner", "text": text}
    if urgent:
        data["urgent"] = True
    return v.log.append("chat", agent, v.clock.now(), **data)
