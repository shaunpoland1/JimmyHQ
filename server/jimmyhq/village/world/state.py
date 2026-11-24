"""Save and restore bodies, so a restart never resets an agent. A body that starts
fresh after every restart loses its sense of the time of day and of how tired it is.
The event log is the record; this is only the current state, written after each beat.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .agent import Agent
from .guards import RepeatGuard

STATE_VERSION = 1
HISTORY_KEEP = 8  # task history entries kept per task (the resume note only needs the last few)


def dump_agent(a: Agent) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "id": a.id,
        "temperament": a.temperament,
        "needs": a.needs,
        "mode": a.mode,
        "asleep": a.asleep,
        "sleep_kind": a.sleep_kind,
        "nap_until": a.nap_until,
        "napped_on": a.napped_on,
        "rest_until": a.rest_until,
        "current_task": a.current_task,
        "parked": a.parked,
        "waiting": a.waiting,
        "task_history": {k: v[-HISTORY_KEEP:] for k, v in a.task_history.items()},
        "last_wake_evt": a.last_wake_evt,
        "last_completion_evt": a.last_completion_evt,
        "last_message_evt": a.last_message_evt,
        "social_cooldown_until": a.social_cooldown_until,
        "guard": {"window": a.guard.window, "before": list(a.guard.before),
                  "after": list(a.guard.after)},
    }


def load_agent(d: dict[str, Any]) -> Agent:
    g = d["guard"]
    guard = RepeatGuard(g["window"])
    guard.before = deque((tuple(x) for x in g["before"]), maxlen=g["window"])
    guard.after = deque((tuple(x) for x in g["after"]), maxlen=g["window"])
    hist: defaultdict[str, list] = defaultdict(list)
    hist.update(d.get("task_history", {}))
    return Agent(
        id=d["id"], temperament=d["temperament"], needs=d["needs"], guard=guard,
        mode=d["mode"], asleep=d["asleep"], sleep_kind=d["sleep_kind"],
        nap_until=d["nap_until"], napped_on=d["napped_on"], current_task=d["current_task"],
        parked=d["parked"], waiting=d["waiting"], task_history=hist,
        last_wake_evt=d["last_wake_evt"], last_completion_evt=d["last_completion_evt"],
        last_message_evt=d["last_message_evt"],
        social_cooldown_until=d.get("social_cooldown_until"), rest_until=d.get("rest_until"),
    )


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic write: a crash mid-write never leaves a half state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(json.dumps(data, indent=1, sort_keys=True).encode("utf-8"))
    for attempt in range(5):  # on Windows a scanner can hold the file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_bytes().decode("utf-8"))
