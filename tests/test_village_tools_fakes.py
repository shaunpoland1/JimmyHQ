"""What the tools and comms tests share: a small ``Village`` on a simulated clock, and
one step of the act stage. The beat loop's ``Tool``, ``ToolCtx``, ``Done`` and
``Refusal``, the diary, the memory store and the Tape record are the real ones."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jimmyhq import bench, village
from jimmyhq.village.world import SimClock, World
from jimmyhq.village.world.temperament import roll

TZ = "Europe/Paris"
OWNER = "Sam"


from jimmyhq.village.catalog import Done, ToolCtx  # noqa: E402
from jimmyhq.village.rules import Refusal  # noqa: E402


def diary_lines(v, agent_id: str) -> list[dict]:
    """Today's diary entries as the real diary reads them back: ``{time, dream, text}``."""
    from jimmyhq.village.memory import diary

    day = diary.local_time(v.clock.now(), v.tz()).date().isoformat()
    return diary.entries(v.agent_dir(agent_id), day)


def candidates(v, agent_id: str) -> list[dict]:
    path = v.agent_dir(agent_id) / "memory" / "candidates.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


# --- the village -----------------------------------------------------------------------


class FakeVillage:
    """The runtime object, as the contract describes it: a real Core, a real world on a
    simulated clock, and the real event log."""

    def __init__(self, core, hour: int = 10, agents=("ada", "bo")):
        self.core = core
        self.log = village.log(core)
        start = datetime.now(ZoneInfo(TZ)).replace(hour=hour, minute=0, second=0, microsecond=0)
        self.clock = SimClock(start, TZ)
        self.world = World(village.setting_getter(core), self.log, self.clock)
        self.woken: list[str] = []
        self.shared_dir = core.config.data / "village" / "shared"
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        rng = random.Random(1)
        for agent_id in agents:
            t = roll(rng)
            t["frustration_tolerance"] = 1.0
            t["sleep_offset_minutes"] = 0
            self.world.add_agent(agent_id, t)
            self.agent_dir(agent_id).mkdir(parents=True, exist_ok=True)

    def agent_dir(self, agent_id: str) -> Path:
        return self.core.config.data / "village" / "agents" / agent_id

    def cfg(self, agent_id: str) -> dict:
        return {"capabilities": []}

    def owner_name(self) -> str:
        return OWNER

    def tz(self) -> str:
        return TZ

    def wake(self, agent_id: str) -> None:
        self.woken.append(agent_id)

    # helpers for tests
    def later(self, minutes: float) -> None:
        self.clock.advance(minutes)

    def home(self, agent_id: str) -> Path:
        return self.agent_dir(agent_id) / "home" / "files"


def spec(name: str) -> dict:
    from jimmyhq.village import tools

    return next(s for s in tools.specs() if s["name"] == name)


def call(v, agent_id: str, tool: str, args: dict, mode: str = "work") -> Any:
    """One step of the act stage, as the beat loop will do it: run the handler, then tell
    the world. Returns the ``Done``; a ``Refusal`` is recorded and raised."""
    a = v.world.agents[agent_id]
    ctx = ToolCtx(v=v, agent_id=agent_id, agent=a, mode=mode, task=a.current_task,
                  now=v.clock.now())
    try:
        done = asyncio.run(spec(tool)["run"](ctx, args))
    except Refusal as e:
        v.world.refuse(agent_id, tool, e.args_logged if e.args_logged is not None else args,
                       e.sentence, ctx.task, frustrate=e.frustrate)
        raise
    assert isinstance(done, Done)
    done.event = v.world.act(agent_id, tool, done.args_logged, None, done.result, True, ctx.task,
                             done.category)
    return done


def refused(v, agent_id: str, tool: str, args: dict, mode: str = "work") -> Refusal:
    try:
        call(v, agent_id, tool, args, mode)
    except Refusal as e:
        return e
    raise AssertionError(f"{tool} was not refused")


def decide(v, card_id: str, action: str, **more: Any):
    """The owner clicks, and the village process applies the Tape event it tails."""
    from jimmyhq.bench import cards
    from jimmyhq.village.comms import benchlink

    cards.decide(v.core, card_id, action=action, **more)
    event = v.core.tape.latest(limit=1, kind="bench.decided")[0]
    return benchlink.apply_decision(v, event), event


def card_on_bench(v, card_id: str) -> dict:
    return bench.get_card(v.core, card_id)


def say_owner(v, agent_id: str, text: str, urgent: bool = False):
    """The owner writes in chat: the web prints it, the village mirrors it."""
    from jimmyhq.village.comms import chat

    event = v.core.tape.print("village.said", {"agent_id": agent_id, "sender": "owner",
                                               "text": text, "urgent": urgent},
                              actor="owner", subject=f"agent:{agent_id}")
    return chat.mirror(v, event)


def prioritize(v, title: str, why: str = "", agents=()) -> str:
    """The owner sets a standing priority, as the web process records it."""
    from jimmyhq.village import lens, record

    pid = lens.priority_id(title, set(lens.read(v.core)["priorities"]))
    record.prioritized(v.core, {"priority_id": pid, "title": title, "why": why, "details": "",
                                "agents": list(agents), "active": True, "suggested_by": None})
    return pid


# --- the fixture every test file shares --------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture
def v(core):
    from jimmyhq.village.comms import benchlink

    made = FakeVillage(core)
    yield made
    benchlink.close(made)
