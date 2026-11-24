"""The agent event log, as the world sees it.

Everything else (mood words, reports, the divergence check) is derived from it. The
world only needs the small interface in ``EventLog``. ``MemoryLog`` keeps events in a
list, for tests and the simulator; the durable store lives outside this package.

Times are float seconds since the epoch, like the clock's.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Event:
    id: str
    ts: float
    kind: str
    agent: str | None
    data: dict[str, Any] = field(default_factory=dict)


class EventLog(Protocol):
    def append(self, kind: str, agent: str | None, ts: float, **data: Any) -> Event: ...

    def get(self, event_id: str) -> Event | None: ...

    def query(self, agent: str | None = None, kinds: Iterable[str] | None = None,
              since: float | None = None, until: float | None = None) -> list[Event]:
        """Matching events, oldest first. ``since`` and ``until`` are inclusive."""
        ...

    def last(self, agent: str | None = None,
             kinds: Iterable[str] | None = None) -> Event | None: ...

    def count(self, agent: str | None = None, kinds: Iterable[str] | None = None,
              since: float | None = None, until: float | None = None) -> int: ...


class MemoryLog:
    """An event log held in memory. Nothing is written anywhere."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._by_id: dict[str, Event] = {}
        self._n = 0

    def append(self, kind: str, agent: str | None, ts: float, **data: Any) -> Event:
        self._n += 1
        e = Event(id=f"evt-{self._n}", ts=ts, kind=kind, agent=agent, data=data)
        self.events.append(e)
        self._by_id[e.id] = e
        return e

    def get(self, event_id: str) -> Event | None:
        return self._by_id.get(event_id)

    def query(self, agent: str | None = None, kinds: Iterable[str] | None = None,
              since: float | None = None, until: float | None = None) -> list[Event]:
        ks = set(kinds) if kinds else None
        out = []
        # Walk backwards when 'since' is set: recent windows are the common case.
        seq = reversed(self.events) if since is not None else iter(self.events)
        for e in seq:
            if since is not None and e.ts < since:
                break
            if until is not None and e.ts > until:
                continue
            if agent is not None and e.agent != agent:
                continue
            if ks is not None and e.kind not in ks:
                continue
            out.append(e)
        if since is not None:
            out.reverse()
        return out

    def last(self, agent: str | None = None,
             kinds: Iterable[str] | None = None) -> Event | None:
        ks = set(kinds) if kinds else None
        for e in reversed(self.events):
            if (agent is None or e.agent == agent) and (ks is None or e.kind in ks):
                return e
        return None

    def count(self, agent: str | None = None, kinds: Iterable[str] | None = None,
              since: float | None = None, until: float | None = None) -> int:
        return len(self.query(agent, kinds, since, until))
