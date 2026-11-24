"""The words an agent lives by: the frame it reads every beat, and the village docs
copied into its files.

The texts are package data in ``docs/`` next to this file. They are generic: ``{name}``
is the agent and ``{owner}`` the person it works with. ``{capabilities}`` marks the
place where a tool that not every agent has gets its paragraph (see
``CAPABILITY_TEXT``): an agent without the capability reads nothing there.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from jimmyhq.village.memory.diary import local_time

DOCS_DIR = Path(__file__).resolve().parent / "docs"
FRAME = "frame.md"
CHANGES = "changes.md"
ONBOARDING = "onboarding.yaml"
COPIED = ("README.md", "how-things-work.md", "reviewing.md")  # the agent's to annotate
FOLDER = "village"  # under the agent's home/files
NEWS_KIND = "village_news"
NEWS_HEADS = 3
THE_OWNER = "the owner"

HOW = "how-things-work.md"
# capability -> doc file -> the shipped text added for an agent that has it.
CAPABILITY_DOCS = {"web": {FRAME: "web.frame.md", HOW: "web.how-things-work.md"},
                   "hands": {FRAME: "hands.frame.md", HOW: "hands.how-things-work.md"},
                   "browser": {FRAME: "browser.frame.md", HOW: "browser.how-things-work.md"}}

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}
_ISO = re.compile(r"^## (\d{4})-(\d{2})-(\d{2})\b")
_WORDS = re.compile(r"^## (\d{1,2}) ([A-Za-z]+) (\d{4})\b")
_SENTENCE_START = re.compile(r"(^|[.!?:]\**\s+|\*\*|^[ \t]*[-#]+[ \t]+)\{owner\}", re.M)


def read(name: str) -> str:
    """One shipped text, with plain line endings."""
    return (DOCS_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def _capability_text() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {FRAME: {}, HOW: {}}
    for capability, files in CAPABILITY_DOCS.items():
        for doc, name in files.items():
            out[doc][capability] = read(name).strip()
    return out


# doc file -> capability -> the text added for an agent that has it. The frame's is the
# standing instruction read every beat; the other is a section of the agent's own copy.
CAPABILITY_TEXT: dict[str, dict[str, str]] = _capability_text()


def fill(text: str, name: str, owner: str, capabilities: tuple[str, ...] | list[str] = (),
         doc: str = "") -> str:
    """Put the agent's name, the owner's name and any capability paragraphs into a
    text. Only the three known placeholders are touched; other braces are left alone."""
    owner = (owner or "").strip() or THE_OWNER
    extra = "\n\n".join(t for c in capabilities
                         if (t := CAPABILITY_TEXT.get(doc, {}).get(c, ""))).strip()
    text = text.replace("{capabilities}\n", f"\n{extra}\n\n" if extra else "\n")
    text = text.replace("{capabilities}", extra)
    if owner[:1].islower():
        text = _SENTENCE_START.sub(lambda m: m.group(1) + owner[:1].upper() + owner[1:], text)
    return text.replace("{owner}", owner).replace("{name}", name)


def frame(name: str, owner: str, capabilities: tuple[str, ...] | list[str] = ()) -> str:
    """The frame: where the agent is, what its body is, and how to act."""
    return fill(read(FRAME), name, owner, capabilities, doc=FRAME).strip()


# --- changes -----------------------------------------------------------------


def entry_date(entry: str) -> date | None:
    """The day a changes entry is dated: ``## 2026-03-09`` or ``## 9 March 2026, ...``."""
    try:
        if m := _ISO.match(entry):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _WORDS.match(entry)
        if m and m.group(2).lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        pass
    return None


def changes_since(text: str, born: date | None) -> str:
    """The changes doc as an agent born on ``born`` should see it: only the entries from
    its birth day on. A new agent should not inherit the history of what was fixed
    before it arrived; a lesson about a fault it never met is noise. An entry whose
    date cannot be read is kept."""
    text = text.replace("\r\n", "\n")
    if born is None:
        return text
    head, _, rest = text.partition("\n## ")
    intro = (head.rstrip() + "\n\nOnly changes since you arrived are listed. How things work "
             "now is in `how-things-work.md`.\n")
    keep = []
    for entry in (("## " + rest).split("\n## ") if rest else []):
        entry = entry if entry.startswith("## ") else "## " + entry
        when = entry_date(entry)
        if when is None or when >= born:
            keep.append(entry.rstrip("\n"))
    return intro + ("\n" + "\n\n".join(keep) + "\n" if keep
                    else "\nNothing has changed since you arrived.\n")


def news(text: str, limit: int = 5000) -> str:
    """The newest entries of a changes doc, cut at an entry boundary. Empty when there
    are no entries."""
    start = text.find("\n## ")
    if start < 0:
        return ""
    body = text[start + 1:]
    if len(body) <= limit:
        return body.strip()
    cut = body.rfind("\n## ", 0, limit)
    return body[:cut if cut > 0 else limit].strip()


def born_day(v: Any, agent_id: str) -> date | None:
    born = v.log.query(agent=agent_id, kinds=["born"])
    return local_time(born[0].ts, v.tz()).date() if born else None


def changes_for(v: Any, agent_id: str) -> str:
    """The shipped changes doc as this agent should see it. The night reads this, not
    the copy in the agent's files, which the agent could have written over."""
    try:
        text = read(CHANGES)
    except OSError:
        return ""
    return changes_since(text, born_day(v, agent_id))


# --- the agent's copy --------------------------------------------------------


def docs_dir(v: Any, agent_id: str) -> Path:
    return Path(v.agent_dir(agent_id)) / "home" / "files" / FOLDER


def capabilities_of(v: Any, agent_id: str) -> list[str]:
    """The capabilities whose sections this agent's docs carry: those the owner gave
    it, and the integrations one once the owner switched an action on for it."""
    out = [c for c in (v.cfg(agent_id) or {}).get("capabilities") or () if c != "hands"]
    try:
        from jimmyhq.village.capabilities import has_hands

        if has_hands(v.core, agent_id):
            out.append("hands")
    except Exception:  # noqa: BLE001 - docs must be written whatever the integrations do
        pass
    return out


def settle_in(v: Any, agent_id: str) -> list[str]:
    """Give the agent its copy of the village docs and say which files were written.

    A doc the agent already has is left alone: it is theirs and they may have written
    in it. ``changes.md`` belongs to the village and is kept current, so an agent can
    learn that something it struggled with has been fixed; when it changes, a
    ``village_news`` event is logged so the agent is told there is news."""
    dest = docs_dir(v, agent_id)
    dest.mkdir(parents=True, exist_ok=True)
    owner = v.owner_name()
    written = []
    for name in COPIED:
        target = dest / name
        if not target.exists():
            text = fill(read(name), agent_id, owner, capabilities_of(v, agent_id), doc=name)
            target.write_text(text, encoding="utf-8", newline="\n")
            written.append(name)
    mine = dest / CHANGES
    new = fill(changes_for(v, agent_id), agent_id, owner)
    old = mine.read_text(encoding="utf-8").replace("\r\n", "\n") if mine.exists() else None
    if new and old != new:
        mine.write_text(new, encoding="utf-8", newline="\n")
        written.append(CHANGES)
        # A first copy with no entries is not news; a changed one, or one with entries, is.
        if old is not None or news(new):
            v.log.append(NEWS_KIND, agent_id, v.clock.now(), file=f"{FOLDER}/{CHANGES}")
    written += capability_sections(v, agent_id)
    return written


def capability_sections(v: Any, agent_id: str) -> list[str]:
    """Add to the agent's own copy of how-things-work the section for each capability
    it holds and has no section for yet: one the owner gave it after it settled in. The
    section goes at the end and nothing the agent wrote is touched. Returns the file's
    name when something was added."""
    target = docs_dir(v, agent_id) / HOW
    if not target.exists():
        return []
    text = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    added = False
    for capability in capabilities_of(v, agent_id):
        section = CAPABILITY_TEXT.get(HOW, {}).get(capability, "")
        if section and section.splitlines()[0] not in text:
            text = text.rstrip("\n") + "\n\n" + fill(section, agent_id, v.owner_name()) + "\n"
            added = True
    if added:
        target.write_text(text, encoding="utf-8", newline="\n")
    return [HOW] if added else []


def news_heads(v: Any, agent_id: str, limit: int = NEWS_HEADS) -> list[str]:
    """The bold first sentences of the newest dated entry in the agent's changes doc:
    what the news line names, so the agent knows what changed before it opens the file."""
    try:
        text = (docs_dir(v, agent_id) / CHANGES).read_text(encoding="utf-8")
    except OSError:
        return []
    parts = text.replace("\r\n", "\n").split("\n## ")
    if len(parts) < 2:
        return []
    heads = re.findall(r"^- \*\*(.+?)\*\*", parts[1], flags=re.M | re.S)[:limit]
    return [" ".join(h.split()) for h in heads]


# --- onboarding --------------------------------------------------------------


def parse_onboarding(text: str) -> list[dict[str, str]]:
    """Read the onboarding list. The file uses a small, fixed subset of YAML: a ``tasks:``
    list whose items are flat ``key: value`` pairs, values optionally in double quotes."""
    tasks: list[dict[str, str]] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#") or line == "tasks:":
            continue
        new = line.startswith("- ")
        key, sep, value = line[2:].partition(":") if new else line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('\\"', '"')
        if new:
            tasks.append({})
        if tasks:
            tasks[-1][key.strip()] = value
    return [t for t in tasks if t.get("id") and t.get("title")]


def onboarding(has_coworkers: bool = False) -> list[dict[str, str]]:
    """The tasks a new agent starts with."""
    return [t for t in parse_onboarding(read(ONBOARDING))
            if t.get("needs") != "coworkers" or has_coworkers]
