"""The Village lens: what the Village screen reads instead of the Tape.

It holds the roster (each agent's name, role, configuration and last night), the
priorities, and the newest chat lines per agent. An agent's vitals are not here: they
change every beat and are read from its ``state.json``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

if TYPE_CHECKING:
    from jimmyhq.core import Core

CHAT_LINES = 200  # chat lines kept per agent, newest last
KINDS = ("village.born", "village.retired", "village.named", "village.configured",
         "village.prioritized", "village.said", "village.slept", "village.restored")
DEFAULT_CONFIG: dict[str, Any] = {
    "role": "", "beat": {"role": "default"}, "night": {"role": "smart"},
    "local_only": False, "capabilities": [],
}


def model_choice(value: Any, fallback_role: str) -> dict[str, str]:
    """A model choice with its empty fields dropped: a role, or a provider's model."""
    value = value if isinstance(value, dict) else {}
    if value.get("provider_id") and value.get("model"):
        return {"provider_id": value["provider_id"], "model": value["model"]}
    return {"role": value.get("role") or fallback_role}


def clean_config(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": payload.get("role") or "",
        "beat": model_choice(payload.get("beat"), "default"),
        "night": model_choice(payload.get("night"), "smart"),
        "local_only": bool(payload.get("local_only")),
        "capabilities": list(payload.get("capabilities") or []),
    }


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "priority"


def priority_id(title: str, taken: set[str]) -> str:
    """A new priority's id, made from its title and never one already taken."""
    base = f"p-{slug(title)}"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def _blank(agent_id: str) -> dict[str, Any]:
    return {"id": agent_id, "name": None, "role": "", "seed": None, "born_ts": None,
            "retired_ts": None, "retire_note": "", "config": clean_config({}),
            "last_night": None, "last_restore": None}


class VillageLens(Lens):
    name = "village"
    version = 1
    kinds = KINDS

    def initial(self) -> dict[str, Any]:
        return {"agents": {}, "order": [], "priorities": {}, "priority_order": [],
                "chat": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        p, kind = event.payload, event.kind
        if kind == "village.prioritized":
            pid = p["priority_id"]
            if pid not in state["priorities"]:
                state["priority_order"].append(pid)
            created = state["priorities"].get(pid, {}).get("created_ts", event.ts)
            state["priorities"][pid] = {
                "id": pid, "title": p["title"], "why": p.get("why", ""),
                "details": p.get("details", ""), "agents": list(p.get("agents") or []),
                "active": bool(p.get("active", True)), "suggested_by": p.get("suggested_by"),
                "created_ts": created, "updated_ts": event.ts}
            return state
        agent_id = p["agent_id"]
        agent = state["agents"].get(agent_id)
        if agent is None:
            # An event about an agent the lens has not met still makes a row, so a
            # line printed out of order is never lost.
            agent = state["agents"][agent_id] = _blank(agent_id)
            state["order"].append(agent_id)
        if kind == "village.born":
            agent.update(seed=p["seed"], born_ts=event.ts, role=p.get("role", ""))
            agent["config"]["role"] = p.get("role", "")
        elif kind == "village.retired":
            agent.update(retired_ts=event.ts, retire_note=p.get("note", ""))
        elif kind == "village.named":
            agent["name"] = p["name"]
        elif kind == "village.configured":
            agent["config"] = clean_config(p)
            agent["role"] = agent["config"]["role"]
        elif kind == "village.said":
            lines = state["chat"].setdefault(agent_id, [])
            lines.append({"seq": event.seq, "id": event.id, "ts": event.ts,
                          "sender": p["sender"], "text": p["text"],
                          "urgent": bool(p.get("urgent"))})
            del lines[:-CHAT_LINES]
        elif kind == "village.slept":
            agent["last_night"] = {"ts": event.ts, "day": p["day"], "by": p["by"],
                                   "ops": p["ops"], "rejected": p.get("rejected", 0),
                                   "snapshot": p.get("snapshot"), "notes": p.get("notes", "")}
        elif kind == "village.restored":
            agent["last_restore"] = {"ts": event.ts, "to": p["to"], "undo": p["undo"],
                                     "paths": list(p.get("paths") or []),
                                     "note": p.get("note", "")}
        return state


LENS = VillageLens()


def read(core: Core) -> dict[str, Any]:
    """The lens state, caught up to the end of the Tape."""
    return core.lenses.read(LENS)


def living(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The agents who have not retired, in the order they arrived."""
    return [state["agents"][i] for i in state["order"]
            if state["agents"][i]["retired_ts"] is None]


def config(core: Core, agent_id: str) -> dict[str, Any]:
    """One agent's configuration, or the defaults if it is unknown."""
    agent = read(core)["agents"].get(agent_id)
    return agent["config"] if agent else clean_config({})


def priorities(state: dict[str, Any], agent_id: str | None = None,
               active_only: bool = False) -> list[dict[str, Any]]:
    """Priorities in the order they were first set; for one agent if ``agent_id``."""
    out = [state["priorities"][i] for i in state["priority_order"]]
    if active_only:
        out = [p for p in out if p["active"]]
    if agent_id is not None:
        out = [p for p in out if not p["agents"] or agent_id in p["agents"]]
    return out


def unanswered(lines: list[dict[str, Any]]) -> int:
    """How many lines the agent has written since the owner last wrote: what is waiting
    for the owner to read or answer."""
    count = 0
    for line in reversed(lines):
        if line["sender"] == "owner":
            break
        count += 1
    return count
