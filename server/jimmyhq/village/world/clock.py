"""The village clock.

Time is float seconds since the epoch. The time zone is a name passed in (the runtime
supplies the owner's), never fixed in code; an empty name means this computer's own
zone. ``SimClock`` runs as fast as the simulator steps it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> float: ...

    def local_time(self) -> datetime: ...


def _local(ts: float, tz: str) -> datetime:
    if tz:
        return datetime.fromtimestamp(ts, ZoneInfo(tz))
    return datetime.fromtimestamp(ts, UTC).astimezone()


class RealClock:
    """Wall-clock time. ``tz`` may be a name, or a function that returns the current
    name, so a changed setting takes effect without a restart."""

    def __init__(self, tz: str | Callable[[], str] = ""):
        self._tz = tz

    def now(self) -> float:
        return time.time()

    def local_time(self) -> datetime:
        tz = self._tz() if callable(self._tz) else self._tz
        return _local(self.now(), tz or "")


class SimClock:
    def __init__(self, start: datetime | float, tz: str = "UTC"):
        self._t = start.timestamp() if isinstance(start, datetime) else float(start)
        self.tz = tz

    def now(self) -> float:
        return self._t

    def advance(self, minutes: float) -> None:
        self._t += minutes * 60.0

    def set(self, ts: float) -> None:
        self._t = ts

    def local_time(self) -> datetime:
        return _local(self._t, self.tz)


def local_hour(clock: Clock) -> float:
    d = clock.local_time()
    return d.hour + d.minute / 60.0
