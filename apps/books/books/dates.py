"""Calendar dates: the ones the owner types, when a reminder is due next, and the dates
in a bank's file.

A date the owner types is a local calendar date kept as ``YYYY-MM-DD``. A reminder's
schedule is a ``Rule``. Each step is measured from the rule's own day of the month, not
from the date before it, so a bill on the 31st lands on 30 April and returns to 31 May
instead of creeping back a day every short month.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

EVERY = ("weekly", "biweekly", "twice_monthly", "monthly", "months", "yearly", "once")
EVERY_WORDS = {
    "weekly": "Every week", "biweekly": "Every two weeks",
    "twice_monthly": "Twice a month", "monthly": "Every month",
    "months": "Every few months", "yearly": "Every year", "once": "Once",
}
CSV_FORMATS = ("ymd", "mdy", "dmy")


def parse_day(text: object, what: str = "The date") -> date:
    """Rule ``a_typed_date_is_a_real_calendar_day``: ``YYYY-MM-DD`` as a date, or a
    sentence that says what to type."""
    raw = str(text or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValueError(f"{what} must be a date like 2026-03-31.")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{what}, {raw}, is not a day on the calendar.") from None


def last_day(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def add_months(d: date, n: int, day: int | None = None) -> date:
    """``d`` moved by ``n`` months, on ``day`` (else ``d``'s own day), held to the last day
    of a shorter month."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(day or d.day, last_day(y, m)))


@dataclass(frozen=True)
class Rule:
    """How often a reminder comes round. ``day`` is the day of the month it belongs on
    (for twice a month, the first of its two days; ``day2`` the second). ``n`` is the
    number of months for ``months``."""

    every: str
    day: int = 1
    day2: int = 15
    n: int = 1


def rule_problem(rule: Rule) -> str:
    """The sentence that says what is wrong with a schedule, or ""."""
    if rule.every not in EVERY:
        return "Choose how often it comes round."
    if rule.every in ("monthly", "months", "twice_monthly", "yearly") and not 1 <= rule.day <= 31:
        return "The day of the month must be from 1 to 31."
    if rule.every == "twice_monthly":
        if not 1 <= rule.day2 <= 31:
            return "The second day of the month must be from 1 to 31."
        if rule.day2 == rule.day:
            return "Twice a month needs two different days, such as the 1st and the 15th."
    if rule.every == "months" and not 1 <= rule.n <= 120:
        return "Every few months must be from 1 to 120 months."
    return ""


def rule_from(every: str, first_due: date, *, day2: int | None = None,
              n: int | None = None) -> Rule:
    """The schedule a reminder starting on ``first_due`` keeps. The day of the month comes
    from that first date; twice a month takes its second day from ``day2``."""
    if every == "twice_monthly":
        other = int(day2 or 0)
        low, high = sorted((first_due.day, other))
        return Rule(every, day=low, day2=high)
    return Rule(every, day=first_due.day, n=int(n or 1))


def next_due(rule: Rule, due: date) -> date | None:
    """Rule ``a_reminder_moves_to_its_next_date``: the date after ``due``, or None for one
    that comes only once."""
    if rule.every == "once":
        return None
    if rule.every == "weekly":
        return due + timedelta(days=7)
    if rule.every == "biweekly":
        return due + timedelta(days=14)  # the same weekday, always
    if rule.every == "twice_monthly":
        days = sorted((rule.day, rule.day2))
        for months in (0, 1, 2):
            base = add_months(date(due.year, due.month, 1), months, 1)
            for d in days:
                candidate = date(base.year, base.month, min(d, last_day(base.year, base.month)))
                if candidate > due:
                    return candidate
        return None  # not reached: a month always has a later day within two months
    step = {"monthly": 1, "months": rule.n, "yearly": 12}[rule.every]
    return add_months(due, step, rule.day)


def occurrences(rule: Rule, first: date, start: date, end: date, most: int = 1000) -> list[date]:
    """Every date from ``first`` on that falls within ``start``..``end``, in order."""
    out: list[date] = []
    cur: date | None = first
    while cur is not None and cur <= end and len(out) < most:
        if cur >= start:
            out.append(cur)
        cur = next_due(rule, cur)
    return out


# --- dates in a bank's file -------------------------------------------------------------------

_YMD = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_OTHER = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$")


def _clean(text: str) -> str:
    raw = str(text or "").strip()
    return raw.split(" ")[0].split("T")[0]  # "2026-03-01 00:00:00" and ISO times


def detect_format(values: list[str]) -> str | None:
    """Rule ``a_files_date_format_is_detected``: ``ymd``, ``mdy`` or ``dmy`` when the dates
    say which; ``"ask"`` when every one reads both ways (such as 3/4/2026); None when they
    are not dates in one of these formats."""
    seen = [_clean(v) for v in values if str(v or "").strip()]
    # A stray line (a total, a note) does not spoil the rest: it is listed as unread. Only
    # a column that is mostly not dates is refused.
    dated = [v for v in seen if _YMD.match(v) or _OTHER.match(v)]
    seen = dated if len(dated) * 2 > len(seen) else []
    if not seen:
        return None
    if all(_YMD.match(v) for v in seen):
        return "ymd"
    parts = [_OTHER.match(v) for v in seen]
    if not all(parts):
        return None
    first_big = any(int(m.group(1)) > 12 for m in parts if m)
    second_big = any(int(m.group(2)) > 12 for m in parts if m)
    if first_big and second_big:
        return None
    if first_big:
        return "dmy"
    if second_big:
        return "mdy"
    return "ask"


def parse_file_date(text: str, fmt: str) -> date:
    """One date from a file in format ``fmt``. A two-digit year is read as 20xx."""
    raw = _clean(text)
    try:
        if fmt == "ymd":
            m = _YMD.match(raw)
            if m:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif fmt in ("mdy", "dmy"):
            m = _OTHER.match(raw)
            if m:
                a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                y = y + 2000 if y < 100 else y
                month, day = (a, b) if fmt == "mdy" else (b, a)
                return date(y, month, day)
    except ValueError:
        pass
    raise ValueError(f"\"{text}\" is not a date Books can read.")
