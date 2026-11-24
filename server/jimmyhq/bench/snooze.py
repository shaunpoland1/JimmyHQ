"""The quick snooze choices, worked out on the server.

They follow the owner's settings (how many hours "later today" is, the hour a
morning starts, the first day of the week) and the owner's time zone, so the
clock and zone of whatever browser shows the Bench play no part.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from jimmyhq.core import Core

_HOUR_MS = 60 * 60 * 1000
_WEEKDAY = {"monday": 0, "saturday": 5, "sunday": 6}  # datetime.weekday() numbers


def zone_of(core: Core) -> tzinfo | None:
    """The owner's time zone, or None for this computer's own."""
    name = core.settings.get("owner.timezone").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (KeyError, ValueError, OSError):
            pass  # a name this computer does not know falls back to its own zone
    return None


def zone_name(core: Core) -> str:
    """The IANA name of the owner's zone, or "local" for this computer's own."""
    return getattr(zone_of(core), "key", None) or "local"


def _wall(ms: int, zone: tzinfo | None) -> datetime:
    """The owner's wall clock at a moment, as a naive date and time."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(zone).replace(tzinfo=None)


def _moment(wall: datetime, zone: tzinfo | None) -> int:
    """The moment the owner's wall clock shows ``wall``, in unix milliseconds."""
    # A naive datetime's timestamp() reads it as this computer's local time.
    aware = wall.replace(tzinfo=zone) if zone else wall
    return int(aware.timestamp() * 1000)


def choices(core: Core, now: int) -> list[dict[str, Any]]:
    """The choices offered at ``now``: a few hours on, rounded up to the hour
    (only while that is still today), tomorrow morning, and the morning the
    owner's next week starts."""
    zone = zone_of(core)
    later_hours = core.settings.get("bench.snooze_later_hours")
    morning = time(core.settings.get("bench.snooze_morning_hour"))
    week_start = _WEEKDAY.get(core.settings.get("owner.week_starts"), 0)
    today = _wall(now, zone).date()
    offered: list[dict[str, Any]] = []

    later = _wall(now + later_hours * _HOUR_MS, zone)
    if later.minute or later.second or later.microsecond:
        later = later.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if later.date() == today:
        offered.append({"id": "later", "label": "Later today", "until": _moment(later, zone)})

    tomorrow = datetime.combine(today + timedelta(days=1), morning)
    offered.append({"id": "tomorrow", "label": "Tomorrow morning",
                    "until": _moment(tomorrow, zone)})

    ahead = (week_start - today.weekday()) % 7 or 7
    week = datetime.combine(today + timedelta(days=ahead), morning)
    offered.append({"id": "week", "label": "Next week", "until": _moment(week, zone)})
    # A clock change can fold a choice back onto now; never offer the past.
    return [c for c in offered if c["until"] > now]
