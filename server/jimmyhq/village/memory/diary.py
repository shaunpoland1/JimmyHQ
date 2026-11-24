"""The diary: one markdown file a day, in the agent's own words. A dream is marked as
one and is never given back to the night that sorts memory.

    <agent folder>/diary/YYYY-MM-DD.md

Times are float seconds since the epoch, like the world's clock. The day and the time
of an entry are written in the zone named by ``tz`` (empty: this computer's own).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HEADER = re.compile(r"^## (\d{2}:\d{2})( \(dream\))?$", re.M)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def local_time(ts: float, tz: str = "") -> datetime:
    """A time in the named zone, or in this computer's zone when no name is given."""
    if tz:
        return datetime.fromtimestamp(ts, ZoneInfo(tz))
    return datetime.fromtimestamp(ts, UTC).astimezone()


def valid_day(day: str) -> bool:
    return bool(_DAY.fullmatch(day or ""))


def _file(agent_dir: Path, day: str) -> Path:
    if not valid_day(day):
        raise ValueError(f"not a day: {day!r}")
    return Path(agent_dir) / "diary" / f"{day}.md"


def append(agent_dir: Path, ts: float, text: str, dream: bool = False, tz: str = "") -> Path:
    """Add an entry to the day ``ts`` falls on and return the file it went to."""
    when = local_time(ts, tz)
    path = _file(agent_dir, when.date().isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"## {when:%H:%M}{' (dream)' if dream else ''}\n{text.strip()}\n\n")
    return path


def entries(agent_dir: Path, day: str, include_dreams: bool = True) -> list[dict]:
    """The day's entries, oldest first: ``{time, dream, text}``."""
    if not valid_day(day):
        return []
    path = _file(agent_dir, day)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    out = []
    marks = list(HEADER.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        dream = bool(m.group(2))
        if dream and not include_dreams:
            continue
        out.append({"time": m.group(1), "dream": dream, "text": text[m.end():end].strip()})
    return out


def days(agent_dir: Path) -> list[str]:
    """Every day that has a diary file, oldest first."""
    folder = Path(agent_dir) / "diary"
    if not folder.exists():
        return []
    return sorted(p.stem for p in folder.glob("*.md") if valid_day(p.stem))
