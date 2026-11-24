"""Tools that act on the agent itself: its diary, what it wants to remember, and rest."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.comms import newest, owner
from jimmyhq.village.tools.seams import WEO, done, refuse, text

REMEMBER_CITES = 3  # a note to remember cites this many of the agent's latest actions
SECTIONS = ["self", "skills", "tools", "wants", "moments", "facts", "story"]
REST_DEFAULT_MIN = 60


async def write_diary(ctx: Any, args: dict[str, Any]) -> Any:
    from jimmyhq.village.memory import diary

    v, said = ctx.v, text(args, "text")
    diary.append(v.agent_dir(ctx.agent_id), v.clock.now(), said, tz=v.tz())
    return done(f"wrote {len(said)} characters in your diary", {"text": said})


def last_actions(v, agent_id: str, n: int = REMEMBER_CITES) -> list[str]:
    """Rule ``remember_cites``: a thing to remember is a candidate, tied to what the
    agent was doing when it noted it. Sleep decides what stays."""
    return [e.id for e in newest(v, agent_id, ["action"], n)]


async def remember(ctx: Any, args: dict[str, Any]) -> Any:
    from jimmyhq.village.memory.store import MemoryStore

    v = ctx.v
    section, said = text(args, "section"), text(args, "text")
    MemoryStore(v.agent_dir(ctx.agent_id)).add_candidate(
        section, said, last_actions(v, ctx.agent_id), v.clock.now())
    return done("noted; sleep will decide whether it stays", {"section": section, "text": said})


def rest_refusal(why: str) -> tuple[str, str]:
    """Which rule the body's answer is, and the sentence for the agent.

    ``rest_on_task``: not while on a task. ``rest_daytime``: not early in the day with
    energy to spare."""
    rule = "rest_on_task" if why.startswith("you're on ") else "rest_daytime"
    return rule, f"Nothing happened, you are not resting: {why}."


async def rest(ctx: Any, args: dict[str, Any]) -> Any:
    v, agent_id = ctx.v, ctx.agent_id
    asked = args.get("minutes") or REST_DEFAULT_MIN
    try:
        asked = float(asked)
    except (TypeError, ValueError):
        asked = REST_DEFAULT_MIN
    why = v.world.rest(agent_id, asked)
    if why:
        rule, sentence = rest_refusal(why)
        refuse(rule, sentence, args_logged={"minutes": asked})
    until = v.world.agents[agent_id].rest_until or v.clock.now()
    mins = round((until - v.clock.now()) / 60)
    early = int(v.world.S("sleep.early_night_hour"))
    return done(f"resting for {mins} minutes: no actions until then. From {early:02d}:00 "
                f"resting becomes an early night. A message from {owner(v)} still reaches you",
                {"minutes": mins})


SPECS = [
    {"name": "write_diary", "kind": "self", "modes": WEO, "run": write_diary,
     "description": "Write in your diary, in your own words.",
     "params": {"text": {"type": "string"}}, "required": ["text"]},
    {"name": "remember", "kind": "self", "modes": WEO, "run": remember,
     "description": "Note something worth remembering. Sleep decides what stays.",
     "params": {"section": {"type": "string", "enum": SECTIONS},
                "text": {"type": "string"}},
     "required": ["section", "text"]},
    {"name": "rest", "kind": "self", "modes": WEO, "run": rest,
     "description": "Stop for a while: no actions, and your energy comes back. For the "
                    "evening when the real list is empty, or when you're tired. Not while "
                    "you're on a task (set it aside first). From the early-night hour, "
                    "resting becomes going to bed. A message from {owner} still reaches "
                    "you.",
     "params": {"minutes": {"type": "integer",
                            "description": "How long, 20 to 180 (default 60)"}},
     "required": []},
]

RULES = [
    ("remember_cites", None, last_actions),
    ("rest_daytime", "village.sleep.chosen_rest_from_hour", rest_refusal),
    ("rest_on_task", None, rest_refusal),
]
