"""Repeat rules: reading an iCalendar RRULE, finding the next round, saying it in words.

Everything here works in whole calendar dates. A round belongs to a day, not an
instant, so time zones and clock changes cannot move it: the caller decides what
"today" is and this module only counts days.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from functools import lru_cache

from dateutil.rrule import rrulestr

_MAX_RULE = 200
_FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_TOO_OFTEN = ("HOURLY", "MINUTELY", "SECONDLY")
_ALLOWED = ("FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH",
            "BYSETPOS", "BYYEARDAY", "BYWEEKNO", "WKST")
_TIMED = ("BYHOUR", "BYMINUTE", "BYSECOND")
_DAYS = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday",
         "FR": "Friday", "SA": "Saturday", "SU": "Sunday"}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_POSITIONS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last"}


class RecurError(ValueError):
    """The repeat rule cannot be used. The message says why, for the person who typed it."""


def _parts(rule: str) -> dict[str, str]:
    text = rule.strip().upper().removeprefix("RRULE:")
    if not text or len(text) > _MAX_RULE:
        raise RecurError("The repeat rule is empty or too long. "
                         "Use something like FREQ=WEEKLY;BYDAY=TH.")
    parts: dict[str, str] = {}
    for piece in text.split(";"):
        key, sep, value = piece.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value or not re.fullmatch(r"[A-Z0-9,+\-]+", value):
            raise RecurError("The repeat rule could not be read. "
                             "Use something like FREQ=WEEKLY;BYDAY=TH.")
        if key in _TIMED:
            raise RecurError("A task can repeat at most once a day, so the repeat rule "
                             f"cannot use {key}. Set a due time on the task instead.")
        if key not in _ALLOWED:
            raise RecurError(f"The repeat rule uses {key}, which tasks do not support.")
        if key in parts:
            raise RecurError(f"The repeat rule gives {key} more than once.")
        parts[key] = value
    freq = parts.get("FREQ")
    if freq in _TOO_OFTEN:
        raise RecurError("A task can repeat at most once a day. Use FREQ=DAILY, WEEKLY, "
                         "MONTHLY or YEARLY.")
    if freq not in _FREQS:
        raise RecurError("The repeat rule must start with FREQ=DAILY, WEEKLY, MONTHLY "
                         "or YEARLY.")
    for key, top in (("INTERVAL", 1000), ("COUNT", 10000)):
        if key in parts and not (parts[key].isdigit() and 1 <= int(parts[key]) <= top):
            raise RecurError(f"{key} in the repeat rule must be a whole number "
                             f"from 1 to {top}.")
    if "COUNT" in parts and "UNTIL" in parts:
        raise RecurError("The repeat rule gives both COUNT and UNTIL. Use one of them to "
                         "say when it ends.")
    if "UNTIL" in parts:
        try:
            # Only the day matters. A time or a Z after it is accepted and dropped.
            parts["UNTIL"] = datetime.strptime(parts["UNTIL"][:8], "%Y%m%d").strftime("%Y%m%d")  # noqa: DTZ007
        except ValueError:
            raise RecurError("UNTIL in the repeat rule must be a date written as "
                             "YYYYMMDD.") from None
    return parts


def _rule(rule: str, anchor: date):
    parts = dict(_parts(rule))
    if "UNTIL" in parts:
        parts["UNTIL"] += "T235959"  # the last day is included
    text = ";".join(f"{k}={v}" for k, v in parts.items())
    try:
        return rrulestr(text, dtstart=datetime.combine(anchor, time.min), cache=False)
    except Exception:  # noqa: BLE001 - dateutil raises several types for a bad rule
        raise RecurError("The repeat rule could not be read. "
                         "Use something like FREQ=WEEKLY;BYDAY=TH.") from None


def canonical(rule: str) -> str:
    """The rule in the one form that is stored: upper case, no prefix, UNTIL as a date.

    Raises RecurError if it cannot be read or would repeat more than once a day.
    """
    return ";".join(f"{k}={v}" for k, v in _parts(rule).items())


def on_or_after(rule: str, anchor: date, day: date) -> date | None:
    """The first round on or after ``day``, counting rounds from ``anchor``."""
    return _find(rule, anchor, day, inclusive=True)


def next_after(rule: str, anchor: date, day: date) -> date | None:
    """The first round strictly after ``day``, or None when the rule has run out."""
    return _find(rule, anchor, day, inclusive=False)


def _find(rule: str, anchor: date, day: date, *, inclusive: bool) -> date | None:
    try:
        for moment in _rule(rule, anchor):
            found = moment.date()
            if found > day or (inclusive and found == day):
                return found
    except RecurError:
        raise
    except Exception:  # noqa: BLE001 - e.g. INTERVAL walking past the last year there is
        return None
    return None


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _every(interval: int, unit: str) -> str:
    if interval == 1:
        return f"every {unit}"
    if interval == 2:
        return f"every other {unit}"
    return f"every {interval} {unit}s"


@lru_cache(maxsize=512)
def describe(rule: str) -> str:
    """A short human reading, such as "every Thursday". Falls back to the rule itself."""
    try:
        parts = _parts(rule)
    except RecurError:
        return rule
    text = _describe(parts)
    if text is None:
        return rule
    if "COUNT" in parts:
        n = int(parts["COUNT"])
        text += ", once" if n == 1 else f", {n} times"
    if "UNTIL" in parts:
        u = parts["UNTIL"]
        text += f", until {u[:4]}-{u[4:6]}-{u[6:]}"
    return text


def _describe(parts: dict[str, str]) -> str | None:
    freq, interval = parts["FREQ"], int(parts.get("INTERVAL", "1"))
    extra = set(parts) - {"FREQ", "INTERVAL", "COUNT", "UNTIL", "WKST"}
    days = list(dict.fromkeys(parts["BYDAY"].split(","))) if "BYDAY" in parts else []
    plain_days = all(d in _DAYS for d in days)
    weekdays = set(days) == {"MO", "TU", "WE", "TH", "FR"}

    if freq == "DAILY":
        if not extra:
            return _every(interval, "day")
        if extra == {"BYDAY"} and weekdays and interval == 1:
            return "every weekday"
        return None
    if freq == "WEEKLY":
        if not extra:
            return _every(interval, "week")
        if extra != {"BYDAY"} or not plain_days:
            return None
        if len(days) == 7 and interval == 1:
            return "every day"
        if weekdays:
            return "every weekday" if interval == 1 else f"{_every(interval, 'week')} on weekdays"
        names = _join([_DAYS[d] for d in days])
        if interval == 1:
            return f"every {names}"
        if interval == 2 and len(days) == 1:
            return f"every other {names}"
        return f"{_every(interval, 'week')} on {names}"
    if freq == "MONTHLY":
        lead = "monthly" if interval == 1 else _every(interval, "month")
        if not extra:
            return _every(interval, "month")
        if extra == {"BYMONTHDAY"}:
            try:
                nums = [int(x) for x in parts["BYMONTHDAY"].split(",")]
            except ValueError:
                return None
            if any(n == 0 or n < -1 or n > 31 for n in nums):
                return None
            return f"{lead} on the " + _join(
                ["last day" if n == -1 else _ordinal(n) for n in nums])
        if extra == {"BYDAY"} and len(days) == 1:
            match = re.fullmatch(r"([+-]?\d)(MO|TU|WE|TH|FR|SA|SU)", days[0])
            if match and int(match.group(1)) in _POSITIONS:
                return f"{lead} on the {_POSITIONS[int(match.group(1))]} {_DAYS[match.group(2)]}"
        return None
    if not extra:
        return _every(interval, "year")
    if extra == {"BYMONTH", "BYMONTHDAY"}:
        month, day = parts["BYMONTH"], parts["BYMONTHDAY"]
        if month.isdigit() and day.isdigit() and 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{_every(interval, 'year')} on {int(day)} {_MONTHS[int(month) - 1]}"
    return None
