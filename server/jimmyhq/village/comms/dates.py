"""A calendar check on what an agent tells the owner.

A model miscounts days with the right date in front of it: it will call a date sixteen
days off "three days out", and that reaches the Bench as a fact. Counting days is the
calendar's job. So before a finding or a hand-over goes, any "N days out / away / left /
in N days" in it is checked against the dates it names. A count that fits none of them
is sent back once, with the real numbers. The wording stays the agent's own: it fixes the
line and sends again, and an unchanged second try goes.
"""

from __future__ import annotations

import re
from datetime import date

from jimmyhq.village.comms import local, owner, weekday_date

AGAIN_AFTER_S = 30 * 60  # the same text is questioned at most once in this long

_MONTHS = {m: n for n, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
     ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
     ("oct", "october"), ("nov", "november"), ("dec", "december")], 1) for m in names}
_M = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE = re.compile(
    rf"\b(?:({_M})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?|(\d{{1,2}})(?:st|nd|rd|th)?\s+({_M})\b\.?)"
    r"(?:,?\s+(20\d\d))?", re.I)
_WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
_N = r"(\d{1,3}|" + "|".join(_WORDS) + r")"
_CLAIM = re.compile(
    rf"\b{_N}\s+days?\s+(?:out|away|left|to go|from (?:now|today))\b"
    rf"|\bin\s+{_N}\s+days?\b(?!\s+of\b)", re.I)


def named(text: str, today: date) -> list[tuple[date, int]]:
    """The calendar dates a text names, each with its distance in days from today. A date
    with no year is the nearest one: this year's, unless that is more than two months
    back."""
    out: list[tuple[date, int]] = []
    for m in _DATE.finditer(text or ""):
        month = _MONTHS[(m.group(1) or m.group(4)).lower()]
        day = int(m.group(2) or m.group(3))
        try:
            if m.group(5):
                d = date(int(m.group(5)), month, day)
            else:
                d = date(today.year, month, day)
                if (today - d).days > 60:
                    d = date(today.year + 1, month, day)
        except ValueError:
            continue
        if (d, (d - today).days) not in out:
            out.append((d, (d - today).days))
    return out


def _say(d: date, days: int) -> str:
    when = "today" if days == 0 else "tomorrow" if days == 1 else \
        f"{days} days from today" if days > 0 else f"{-days} day{'s' if days != -1 else ''} ago"
    return f"{d:%a} {d.day} {d:%b} is {when}"


def slip(text: str, today: date, to: str = "the owner") -> str | None:
    """Rule ``date_slip``: None when the text's day counts fit its dates (or it makes
    none); otherwise what to tell the agent, with the real numbers."""
    claims = []
    for m in _CLAIM.finditer(text or ""):
        raw = (m.group(1) or m.group(2)).lower()
        claims.append((int(raw) if raw.isdigit() else _WORDS[raw], m.group(0)))
    ahead = [(d, n) for d, n in named(text, today) if n >= 0]
    if not claims or not ahead:
        return None
    off = [phrase for n, phrase in claims if all(abs(n - days) > 1 for _, days in ahead)]
    if not off:
        return None
    return (f"Nothing was sent yet: before this goes to {to}, check the days. Today is "
            f"{today:%a} {today.day} {today:%b}. By the calendar: "
            + "; ".join(_say(d, n) for d, n in ahead[:4])
            + f". Your text says \"{off[0]}\". Fix that line and send it again (if it is right "
            "as it stands, send it again unchanged and it goes).")


def check(v, agent_id: str, tool: str, title: str, text: str) -> str | None:
    """The sentence to refuse with, or None. Asked once per title in half an hour: after
    that the wording is the agent's call."""
    now = v.clock.now()
    why = slip(text, local(v, now).date(), owner(v))
    if not why:
        return None
    short = title[:200]
    for e in v.log.query(agent=agent_id, kinds=["date_slip"], since=now - AGAIN_AFTER_S):
        if e.data.get("tool") == tool and e.data.get("title") == short:
            return None
    v.log.append("date_slip", agent_id, now, tool=tool, title=short)
    return why


__all__ = ["AGAIN_AFTER_S", "check", "named", "slip", "weekday_date"]
