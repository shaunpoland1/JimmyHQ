"""When a check is due, and when Refresh now may be used. Pure: no clock, no store.

All times are unix milliseconds, UTC; the owner's time zone is used only to say when
"every day at 06:00" falls.

- After a good check, the next is due ``interval`` later, or at the next daily time,
  whichever comes first.
- After a failed check (no clear answer), nothing was changed and the next try is due
  ``retry`` minutes later, doubling with each failure in a row up to ``longest``.
- Refresh now is refused until ``gap`` minutes after the last request that was sent.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, tzinfo

MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR

DEFAULT_CLOCK = "06:00"
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})\Z")


def read_clock(text: object, default: str = DEFAULT_CLOCK) -> tuple[tuple[int, int] | None, str]:
    """The daily time as ``(hour, minute)``, or None for "no daily check", with a sentence
    when what the owner typed could not be read (``default`` is used then). A time with
    nothing in it, or "off", means no daily check."""
    said = str(text or "").strip().lower()
    if said in ("", "off", "none", "never"):
        return None, ""
    found = _CLOCK.match(said)
    if found and int(found.group(1)) < 24 and int(found.group(2)) < 60:
        return (int(found.group(1)), int(found.group(2))), ""
    fallback = _CLOCK.match(default)
    assert fallback is not None  # noqa: S101 - the default is ours
    return ((int(fallback.group(1)), int(fallback.group(2))),
            f"\"Also check every day at\" says \"{str(text).strip()[:20]}\", which is not a "
            f"time such as 06:00, so Packages uses {default} until you correct it.")


def next_daily(after_ms: int, clock: tuple[int, int], zone: tzinfo) -> int:
    """The first moment after ``after_ms`` that the clock in ``zone`` reads ``clock``."""
    local = datetime.fromtimestamp(after_ms / 1000, zone)
    day = local.date()
    for _ in range(3):  # a day with no such time (a clock change) moves on to the next
        found = datetime.combine(day, time(*clock), tzinfo=zone)
        if int(found.timestamp() * 1000) > after_ms:
            return int(found.timestamp() * 1000)
        day += timedelta(days=1)
    return after_ms + DAY  # unreachable in practice


def retry_wait_ms(fails: int, retry_minutes: int, longest_minutes: int) -> int:
    """How long to wait after the ``fails``-th failure in a row."""
    longest = max(retry_minutes, longest_minutes)
    minutes = min(longest, retry_minutes * 2 ** max(0, fails - 1))
    return int(minutes * MINUTE)


def due_ms(*, last_ok: int | None, last_try: int | None, fails: int, interval_hours: int,
           clock: tuple[int, int] | None, retry_minutes: int, longest_minutes: int,
           zone: tzinfo) -> int:
    """When the next check is due. 0 means now: nothing was ever checked."""
    if fails > 0 and last_try is not None:
        return last_try + retry_wait_ms(fails, retry_minutes, longest_minutes)
    if last_ok is None:
        return 0
    soonest = last_ok + interval_hours * HOUR
    if clock is not None:
        soonest = min(soonest, next_daily(last_ok, clock, zone))
    return soonest


def refresh_wait_ms(now: int, last_sent: int | None, gap_minutes: int) -> int:
    """How much longer Refresh now must wait: 0 when it may go."""
    if last_sent is None or gap_minutes <= 0:
        return 0
    return max(0, last_sent + gap_minutes * MINUTE - now)


def span(ms: int) -> str:
    """A wait in words, rounded up: "less than a minute", "7 minutes", "2 hours"."""
    if ms <= 0:
        return "no time"
    if ms <= MINUTE:
        return "less than a minute"
    minutes = -(-ms // MINUTE)
    if minutes < 120:
        return f"{minutes} minutes"
    hours = -(-minutes // 60)
    if hours < 48:
        return f"{hours} hours"
    return f"{-(-hours // 24)} days"
