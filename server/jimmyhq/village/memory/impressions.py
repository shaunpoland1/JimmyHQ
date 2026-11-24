"""Impressions: an agent's own take on each coworker and on the owner.

They are private (no agent reads another's; sharing one means saying it aloud), change
only through real meetings, may be wrong, and are shown to the agent only while it is
dealing with that party.

    <agent folder>/impressions/<who>.md
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_CHARS = 1500
OWNER = "owner"
# A model that ignores the schema may put the words under another name.
TEXT_KEYS = ("text", "impression", "content", "summary", "description")


def safe_name(about: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", str(about).lower())[:40] or "unknown"


def _path(agent_dir: Path, about: str) -> Path:
    return Path(agent_dir) / "impressions" / f"{safe_name(about)}.md"


def read(agent_dir: Path, about: str) -> str:
    path = _path(agent_dir, about)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write(agent_dir: Path, about: str, text: str) -> str:
    path = _path(agent_dir, about)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip()[:MAX_CHARS] + "\n", encoding="utf-8", newline="\n")
    return f"impression of {safe_name(about)} rewritten"


def known(agent_dir: Path) -> list[str]:
    folder = Path(agent_dir) / "impressions"
    return sorted(p.stem for p in folder.glob("*.md")) if folder.exists() else []


def about_of(imp: Any) -> str:
    return str(imp.get("about", "")).strip().lower() if isinstance(imp, dict) else ""


def text_of(imp: Any) -> str:
    """The words of one impression as the model returned it, or "" if there are none."""
    if not isinstance(imp, dict):
        return ""
    return next((imp[k].strip() for k in TEXT_KEYS
                 if isinstance(imp.get(k), str) and imp[k].strip()), "")


def empty_about(imps: list[Any], met: set[str]) -> list[str]:
    """Parties the agent met whose impression came back with no words. These are asked
    for once more: an empty impression never replaces a real one."""
    out = []
    for imp in imps or []:
        about = about_of(imp)
        if about in met and not text_of(imp) and about not in out:
            out.append(about)
    return out


def apply(agent_dir: Path, imps: list[Any], met: set[str]) -> list[str]:
    """Rewrite impressions, only of parties the agent really met since it last slept,
    and only with words. Returns what happened to each."""
    out = []
    for imp in imps or []:
        about = about_of(imp)
        if about not in met:
            out.append(f"rejected impression of {about or '?'}: no meeting with them today")
            continue
        text = text_of(imp)
        if not text:
            out.append(f"rejected impression of {about}: no text")
            continue
        out.append(write(agent_dir, about, text))
    return out
