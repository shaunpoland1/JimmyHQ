"""How agents talk and hand work over: chat with the owner, the break room, the work
board, the Bench, and the list of what each agent has to do.

Everything here is mechanical. A function that will not do what it was asked raises
``Refused`` with the name of the rule and a sentence for the agent: second person, says
that nothing happened, gives the number, and says what to do instead. The tools turn it
into the runtime's refusal.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

COMMS = "village.comms."


class Refused(Exception):
    """A rule said no. ``frustrate`` is False for a village limit: the agent did
    nothing wrong by reaching it."""

    def __init__(self, rule: str, sentence: str, *, frustrate: bool = True,
                 args_logged: dict[str, Any] | None = None):
        super().__init__(sentence)
        self.rule = rule
        self.sentence = sentence
        self.frustrate = frustrate
        self.args_logged = args_logged


def limit(v, name: str) -> int:
    """A ``village.comms.*`` setting, read live."""
    return int(v.core.settings.get(COMMS + name))


def setting(v, key: str, default: Any) -> Any:
    """A setting another package declares. Until it does, the source's number."""
    try:
        return v.core.settings.get(key)
    except Exception:  # noqa: BLE001 - not declared in this process
        return default


def owner(v) -> str:
    """What to call the owner in a sentence."""
    name = (v.owner_name() or "").strip()
    return name or "the owner"


def actor(agent_id: str) -> str:
    return f"agent:{agent_id}"


# --- time ------------------------------------------------------------------------


def local(v, ts: float) -> datetime:
    """A moment (seconds) on the owner's clock."""
    zone = (v.tz() or "").strip()
    if zone:
        return datetime.fromtimestamp(ts, ZoneInfo(zone))
    return datetime.fromtimestamp(ts).astimezone()


def day_start(v, ts: float | None = None) -> float:
    """The start of the owner's calendar day that holds ``ts`` (now by default)."""
    d = local(v, v.clock.now() if ts is None else ts)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def clock_time(v, ts: float) -> str:
    return f"{local(v, ts):%H:%M}"


def weekday_date(v, ts: float) -> str:
    d = local(v, ts)
    return f"{d:%a} {d.day} {d:%b}"


def next_minute(v, ts: float) -> str:
    """The whole minute at or after ``ts``. A time shown rounded down is refused again
    for the seconds that are left of it."""
    d = local(v, ts)
    if d.second or d.microsecond:
        d = d.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return f"{d:%H:%M}"


def ms(ts: float) -> int:
    return round(ts * 1000)


# --- the log ---------------------------------------------------------------------


def newest(v, agent_id: str | None, kinds: list[str], n: int) -> list[Any]:
    """The newest ``n`` matching log events, oldest first."""
    try:
        return v.log.query(agent=agent_id, kinds=kinds, limit=n)
    except TypeError:  # a log without limit (the in-memory one)
        return v.log.query(agent=agent_id, kinds=kinds)[-n:] if n > 0 else []


# --- small files -----------------------------------------------------------------


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Write whole or not at all: a crash mid-write must not leave half a queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


QUEUES = "queues.json"


def queue(v, agent_id: str, name: str) -> list[dict[str, Any]]:
    """Held items of one kind ("handovers" or "findings"), oldest first."""
    items = load_json(v.agent_dir(agent_id) / QUEUES).get(name)
    return list(items) if isinstance(items, list) else []


def save_queue(v, agent_id: str, name: str, items: list[dict[str, Any]]) -> None:
    path = v.agent_dir(agent_id) / QUEUES
    data = load_json(path)
    data[name] = items
    save_json(path, data)
