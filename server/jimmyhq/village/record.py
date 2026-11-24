"""The Village's record on the Tape. Every ``Tape.print`` the village makes is here.

The web process prints what the owner does (a chat line, a priority, a configuration);
the village process prints what happened to an agent (born, retired, named, slept,
restored) and the agent's own chat lines. Each function returns the printed event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jimmyhq.core import Core
    from jimmyhq.tape.registry import Event

OWNER = "owner"
AGENT = "agent"
SYSTEM = "system"


def subject(agent_id: str) -> str:
    return f"agent:{agent_id}"


def priority_subject(priority_id: str) -> str:
    return f"priority:{priority_id}"


def _print(core: Core, kind: str, agent_id: str, payload: dict[str, Any], actor: str,
           cause: str | None = None) -> Event:
    return core.tape.print(kind, {"agent_id": agent_id, **payload}, actor=actor,
                           subject=subject(agent_id), cause=cause)


def born(core: Core, agent_id: str, seed: int, role: str = "", *, actor: str = SYSTEM,
         cause: str | None = None) -> Event:
    return _print(core, "village.born", agent_id, {"seed": int(seed), "role": role}, actor,
                  cause)


def retired(core: Core, agent_id: str, note: str = "", *, actor: str = SYSTEM,
            cause: str | None = None) -> Event:
    return _print(core, "village.retired", agent_id, {"note": note}, actor, cause)


def named(core: Core, agent_id: str, name: str) -> Event:
    return _print(core, "village.named", agent_id, {"name": name}, subject(agent_id))


def configured(core: Core, agent_id: str, config: dict[str, Any], *, actor: str = OWNER,
               cause: str | None = None) -> Event:
    """``config`` holds ``role``, ``beat``, ``night``, ``local_only``, ``capabilities``."""
    return _print(core, "village.configured", agent_id, dict(config), actor, cause)


def prioritized(core: Core, priority: dict[str, Any], *, actor: str = OWNER,
                cause: str | None = None) -> Event:
    """Print a priority as it now stands (the whole of it, not a change)."""
    return core.tape.print("village.prioritized", dict(priority), actor=actor, cause=cause,
                           subject=priority_subject(priority["priority_id"]))


def said(core: Core, agent_id: str, sender: str, text: str, urgent: bool = False) -> Event:
    """One chat line. ``sender`` is ``"owner"``; anything else means the agent spoke."""
    from_owner = sender == OWNER
    return _print(core, "village.said", agent_id,
                  {"sender": OWNER if from_owner else AGENT, "text": text,
                   "urgent": bool(urgent)},
                  OWNER if from_owner else subject(agent_id))


def slept(core: Core, agent_id: str, *, day: str, by: str, ops: int, rejected: int,
          snapshot: int | None, notes: str = "") -> Event:
    return _print(core, "village.slept", agent_id,
                  {"day": day, "by": by, "ops": int(ops), "rejected": int(rejected),
                   "snapshot": snapshot, "notes": notes[:300]}, SYSTEM)


def restored(core: Core, agent_id: str, *, to: int, undo: int, paths: list[str],
             note: str = "", actor: str = SYSTEM, cause: str | None = None) -> Event:
    return _print(core, "village.restored", agent_id,
                  {"to": int(to), "undo": int(undo), "paths": list(paths), "note": note},
                  actor, cause)
