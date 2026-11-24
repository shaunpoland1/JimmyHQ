"""The door's stored views: who the clients are, what they did, and what was said.

``DoorLens`` holds each client's permissions, the count of its calls per tool per UTC
day (those that worked, and every call that reached an app, whatever the app answered;
kept so a restart does not hand anyone a fresh day) and the newest receipts.
``MessagesLens`` holds the newest lines between outside clients and each agent. Both are
read by the web process; the village process reads the second to build an agent's view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

if TYPE_CHECKING:
    from jimmyhq.core import Core

RECENT_CALLS = 300
DAYS_KEPT = 2
THREAD_LINES = 60  # lines kept per agent, newest last
DAY_MS = 86_400_000


def day_of(ts_ms: int) -> int:
    """The UTC day a moment falls in, as a whole number of days."""
    return int(ts_ms) // DAY_MS


class DoorLens(Lens):
    name = "door"
    version = 1
    kinds = ("door.client.added", "door.client.changed", "door.client.rotated",
             "door.client.revoked", "door.called")

    def initial(self) -> dict[str, Any]:
        return {"clients": {}, "order": [], "counts": {}, "calls": [], "last": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        p, kind = event.payload, event.kind
        cid = p["client_id"]
        if kind == "door.client.added":
            state["clients"][cid] = {
                "id": cid, "name": p["name"], "about": p.get("about", ""),
                "tools": dict(p.get("tools") or {}), "options": dict(p.get("options") or {}),
                "created_ts": event.ts, "changed_ts": event.ts, "rotated_ts": None,
                "revoked_ts": None}
            state["order"].append(cid)
            return state
        if kind == "door.called":
            call = {"seq": event.seq, "ts": event.ts, "client_id": cid, "client": p["client"],
                    "tool": p["tool"], "ok": p["ok"], "rule": p.get("rule", ""),
                    "write": bool(p.get("write")), "in_bytes": p.get("in_bytes", 0),
                    "out_chars": p.get("out_chars", 0), "ms": p.get("ms", 0),
                    "created": p.get("created"), "app_id": p.get("app_id"),
                    "app_tool": p.get("app_tool"), "reached": bool(p.get("reached"))}
            state["calls"].append(call)
            del state["calls"][:-RECENT_CALLS]
            state["last"][cid] = call
            if p["ok"] or p.get("reached"):
                day = str(day_of(event.ts))
                days = state["counts"].setdefault(cid, {})
                tools = days.setdefault(day, {})
                tools[p["tool"]] = tools.get(p["tool"], 0) + 1
                for old in sorted(days, key=int)[:-DAYS_KEPT]:
                    del days[old]
            return state
        client = state["clients"].get(cid)
        if client is None:
            return state
        if kind == "door.client.changed":
            client.update(about=p.get("about", ""), tools=dict(p.get("tools") or {}),
                          options=dict(p.get("options") or {}), changed_ts=event.ts)
        elif kind == "door.client.rotated":
            client["rotated_ts"] = event.ts
        elif kind == "door.client.revoked":
            client["revoked_ts"] = event.ts
        return state


class MessagesLens(Lens):
    name = "door_messages"
    version = 1
    kinds = ("door.messaged", "door.replied")

    def initial(self) -> dict[str, Any]:
        return {"agents": {}, "replies": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        p = event.payload
        if event.kind == "door.replied":
            days = state["replies"].setdefault(p["agent_id"], {})
            day = str(day_of(event.ts))
            days[day] = days.get(day, 0) + 1
            for old in sorted(days, key=int)[:-DAYS_KEPT]:
                del days[old]
        lines = state["agents"].setdefault(p["agent_id"], [])
        lines.append({"id": event.id, "seq": event.seq, "ts": event.ts,
                      "dir": "in" if event.kind == "door.messaged" else "out",
                      "client_id": p["client_id"], "client": p["client"], "text": p["text"]})
        del lines[:-THREAD_LINES]
        return state


LENS = DoorLens()
MESSAGES = MessagesLens()


def read(core: Core) -> dict[str, Any]:
    return core.lenses.read(LENS)


def thread(core: Core, agent_id: str) -> list[dict[str, Any]]:
    """The newest lines between outside clients and one agent, oldest first."""
    return core.lenses.read(MESSAGES)["agents"].get(agent_id, [])


def replies_today(core: Core, agent_id: str, now_ms: int) -> int:
    """How many answers this agent has sent outside clients in the current UTC day."""
    days = core.lenses.read(MESSAGES)["replies"].get(agent_id, {})
    return days.get(str(day_of(now_ms)), 0)


def used_today(state: dict[str, Any], client_id: str, tool: str, now_ms: int) -> int:
    """How many calls of one tool count against this client's limit in the current UTC
    day: those that worked, and every one that reached an app."""
    return state["counts"].get(client_id, {}).get(str(day_of(now_ms)), {}).get(tool, 0)
