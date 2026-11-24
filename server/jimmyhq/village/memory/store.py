"""Private agent memory: seven capped sections, fading and reinforcement, and a source
on every line. ``story`` is kept apart from ``facts`` so something an agent imagined
about its own life never reaches a piece of research.

During the day an agent only proposes memories (candidates). The night decides what
stays, and every rule about what may enter, stay or be pushed out is in this file.

    <agent folder>/memory/memory.json       the lines
    <agent folder>/memory/candidates.jsonl  what the agent asked to remember since it slept
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from jimmyhq.village.world.state import save_json

SECTIONS = ("self", "skills", "tools", "wants", "moments", "facts", "story")
PROMPT_SECTIONS = ("self", "story", "wants", "skills", "tools")
CAP = 24
HALF_LIFE_DAYS = {"self": 42, "story": 42}
DEFAULT_HALF_LIFE = 21
DROP_BELOW = 0.02
SAME_LINE = 0.8  # word overlap at which two lines count as saying the same thing
WORD_CEILING = 4  # a content word in this many lines of a section counts as crowded
# A line is "more of the same" only when several of its words are crowded. Rejecting on
# any one crowded word throws out true lines for sharing a single common word.
CROWDED_SHARE = 0.2
# How much a line matters. A core line starts strong, fades three times slower, is not
# held back by the crowded-word rule, and in a full section can be pushed out only by
# another core line. Only a few per section can be core, or the word means nothing.
WEIGHTS = {"core": 0.9, "useful": 0.5, "routine": 0.35}
CORE_HALF_LIFE_FACTOR = 3
CORE_PER_SECTION = 6
# A nap may push out only a few older lines to make room. What it could not fit is still
# in the day's record, and the night, which sees the whole day, decides.
NAP_EVICTIONS = 3
# A line confirmed again within this many days holds its place against a new ordinary
# line; otherwise a full section pushes lines out oldest first, however often confirmed.
PROVEN_DAYS = 14
REINFORCE_STEP = 0.15
TEXT_CHARS = 400
CANDIDATE_CHARS = 500
EVIDENCE_KEPT = 10

# Where a line came from (see ``provenance``). A self-reported line is the agent's own
# account, backed by nothing in the record.
SELF_REPORTED = "self-reported"
SOURCES = ("owner", "seen", "village", "web:unconfirmed", SELF_REPORTED)
# Who the agent is, its own life and what it wants are its own account by nature, so the
# limits on self-reported lines apply everywhere else.
OWN_ACCOUNT_SECTIONS = ("self", "story", "wants")
SELF_NOTE = " (my own account, not checked)"

STOP = set("the a an and or but of to in on at for with is was are were be been it its this "
           "that i my me you your we our they them he she his her as by from not no so if "
           "then than".split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']{4,}", text.lower()) if w not in STOP}


def jaccard(a: str, b: str) -> float:
    x, y = words(a), words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def limited(item: dict[str, Any]) -> bool:
    """Is this a self-reported line outside the agent's own-account sections? Such a
    line cannot be core, and ranks as routine when a full section needs room."""
    return (item.get("source") == SELF_REPORTED
            and item.get("section") not in OWN_ACCOUNT_SECTIONS)


def standing(item: dict[str, Any], day: str) -> float:
    """How well a line holds its place when a full section needs room: its strength, or
    an ordinary new line's strength if it was confirmed again lately. A limited
    self-reported line never stands above routine."""
    if limited(item):
        return min(item["strength"], WEIGHTS["routine"])
    if item.get("confirmed"):
        try:
            age = (date.fromisoformat(day[:10])
                   - date.fromisoformat(str(item.get("last_confirmed"))[:10])).days
            if age <= PROVEN_DAYS:
                return max(item["strength"], WEIGHTS["useful"])
        except ValueError:
            pass
    return item["strength"]


def crowded(texts: list[str], text: str) -> bool:
    """Is too much of a section already about this? True when a fifth of the line's
    content words (at least two) each appear in ``WORD_CEILING`` or more other lines."""
    counts = Counter(w for t in texts for w in words(t))
    mine = words(text)
    return sum(counts[w] >= WORD_CEILING for w in mine) >= max(2, round(len(mine) * CROWDED_SHARE))


def shown(item: dict[str, Any]) -> str:
    """A line as the agent reads it. One it only has its own word for says so."""
    return item["text"] + (SELF_NOTE if limited(item) else "")


def to_ms(ts: float) -> int:
    """Unix milliseconds, as stored, from the world's clock, which counts seconds."""
    return round(ts * 1000)


class MemoryStore:
    """One agent's memory. ``agent_dir`` is the agent's folder; ``cap`` is the most
    lines a section holds."""

    def __init__(self, agent_dir: Path | str, cap: int = CAP):
        self.dir = Path(agent_dir) / "memory"
        self.path = self.dir / "memory.json"
        self.cand_path = self.dir / "candidates.jsonl"
        self.cap = int(cap)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.items: list[dict[str, Any]] = data.get("items", [])
        self.next_id: int = data.get("next_id", 1)
        self.keep: set[int] = set()  # never pushed out to make room (set per pass)
        self.evict_budget: int | None = None  # None: no limit

    # --- reading --------------------------------------------------------------

    def section(self, name: str) -> list[dict[str, Any]]:
        return [i for i in self.items if i["section"] == name]

    def get(self, item_id: int) -> dict[str, Any] | None:
        return next((i for i in self.items if i["id"] == item_id), None)

    def render_for_prompt(self, sections: tuple[str, ...] = PROMPT_SECTIONS) -> str:
        """Memory as the agent reads it awake. Lines are in id order, so the text is
        stable from beat to beat and a model can reuse what it has already read."""
        out = []
        for s in sections:
            items = sorted(self.section(s), key=lambda i: i["id"])
            if items:
                out.append(f"## {s}\n" + "\n".join(f"- {shown(i)}" for i in items))
        return "\n\n".join(out)

    def locked_name(self) -> str | None:
        """The name the agent settled on, once it has locked."""
        for i in self.section("story"):
            if i.get("locked") and i.get("kind") == "name":
                return i["text"].split(":", 1)[-1].strip() or None
        return None

    def candidates(self) -> list[dict[str, Any]]:
        if not self.cand_path.exists():
            return []
        out = []
        for line in self.cand_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a half-written last line is not worth losing the rest for
        return out

    # --- writing --------------------------------------------------------------

    def add_candidate(self, section: str, text: str, evidence: list[str], ts: float) -> None:
        """Note something the agent asked to remember. The night decides if it stays.
        ``ts`` is the world's clock (seconds); the row stores unix milliseconds."""
        self.dir.mkdir(parents=True, exist_ok=True)
        row = {"ts": to_ms(ts), "section": section, "text": str(text)[:CANDIDATE_CHARS],
               "evidence": [str(x) for x in evidence or []]}
        with self.cand_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def clear_candidates(self) -> None:
        self.cand_path.unlink(missing_ok=True)

    def save(self) -> None:
        save_json(self.path, {"items": self.items, "next_id": self.next_id})

    def _core_count(self, section: str) -> int:
        return sum(1 for i in self.section(section) if i.get("weight") == "core")

    def _crowded(self, section: str, text: str, ignore_id: int | None = None) -> bool:
        return crowded([i["text"] for i in self.section(section) if i["id"] != ignore_id],
                       text)

    def add(self, section: str, text: str, source: str, evidence: list[str], day: str,
            consolidated_by: str, strength: float | None = None, kind: str | None = None,
            weight: str | None = None) -> str:
        """Add one line and say what happened. A near-duplicate reinforces the line it
        repeats. A full section pushes out its weakest line that is not settled, core or
        protected: always for a new core line, otherwise only if the new one is stronger."""
        if section not in SECTIONS:
            return f"rejected: unknown section {section}"
        text = text.strip()
        if not text:
            return "rejected: a memory line needs text"
        weight = weight if weight in WEIGHTS else "useful"
        new = {"section": section, "source": source}
        if weight == "core" and (limited(new) or self._core_count(section) >= CORE_PER_SECTION):
            weight = "useful"  # it still goes in, as an ordinary line
        if strength is None:
            strength = WEIGHTS[weight]
        for i in self.section(section):
            if jaccard(i["text"], text) >= SAME_LINE:
                if weight == "core":
                    self._make_core(i)
                return self.reinforce(i["id"], day, evidence, source=source)
        if weight != "core" and self._crowded(section, text):
            return "rejected: too much of the section is already about this"
        if len(self.section(section)) >= self.cap:
            weakest = min((i for i in self.section(section)
                           if not i.get("locked") and i.get("weight") != "core"
                           and i["id"] not in self.keep),
                          key=lambda i: standing(i, day), default=None)
            entering = standing({"section": section, "source": source, "strength": strength},
                                day)
            if weakest is None or (weight != "core" and standing(weakest, day) >= entering):
                return "rejected: section full of stronger memories"
            if self.evict_budget is not None:
                if self.evict_budget <= 0:
                    return ("rejected: section full; a nap does not push out more older "
                            "memories, the night decides")
                self.evict_budget -= 1
            self.items.remove(weakest)
        item = {"id": self.next_id, "section": section, "text": text[:TEXT_CHARS],
                "strength": strength, "created": day, "last_confirmed": day,
                "source": source, "evidence": list(evidence)[-EVIDENCE_KEPT:], "locked": False,
                "consolidated_by": consolidated_by, "weight": weight}
        if kind:
            item["kind"] = kind
        self.items.append(item)
        self.next_id += 1
        return f"added #{item['id']}"

    def _make_core(self, i: dict[str, Any]) -> None:
        if i.get("weight") == "core" or limited(i):
            return
        if self._core_count(i["section"]) >= CORE_PER_SECTION:
            return
        i["weight"] = "core"
        i["strength"] = max(i["strength"], WEIGHTS["core"])

    def replace(self, item_id: int, text: str, day: str, evidence: list[str],
                consolidated_by: str, weight: str | None = None,
                source: str | None = None) -> str:
        """Rewrite a line. The night revises; it does not only append. ``source`` is where
        the new wording comes from; the rewritten line carries it."""
        i = self.get(item_id)
        if not i:
            return f"rejected: no memory #{item_id}"
        if i.get("locked"):
            return f"rejected: #{item_id} is settled"
        text = text.strip()
        if not text:
            return f"rejected: the new text for #{item_id} is empty"
        # A rewrite that now says the same as another line merges into it, so two
        # rewrites to the same words never leave a duplicate.
        twin = next((o for o in self.section(i["section"])
                     if o["id"] != item_id and jaccard(o["text"], text) >= SAME_LINE), None)
        if twin is not None:
            was_core = i.get("weight") == "core" or weight == "core"
            self.reinforce(twin["id"], day, evidence, source=source)
            self.items.remove(i)
            if was_core:
                self._make_core(twin)
            return f"merged #{item_id} into #{twin['id']}"
        before = dict(i)
        if source is not None:
            i["source"] = source
            if limited(i) and i.get("weight") == "core":
                i["weight"] = "useful"
                i["strength"] = min(i["strength"], WEIGHTS["useful"])
        if weight == "core":
            self._make_core(i)
        elif weight in WEIGHTS and i.get("weight") != "core":
            i["weight"] = weight
        if i.get("weight") != "core" and self._crowded(i["section"], text, ignore_id=item_id):
            i.clear()
            i.update(before)  # a refused rewrite leaves the line exactly as it was
            return "rejected: too much of the section is already about this"
        i.update(text=text[:TEXT_CHARS], last_confirmed=day, consolidated_by=consolidated_by,
                 evidence=(i.get("evidence", []) + list(evidence))[-EVIDENCE_KEPT:])
        return f"replaced #{item_id}"

    def reinforce(self, item_id: int, day: str, evidence: list[str] | None = None,
                  source: str | None = None) -> str:
        """Confirm a line again. Evidence from the record promotes a self-reported one."""
        i = self.get(item_id)
        if not i:
            return f"rejected: no memory #{item_id}"
        i["strength"] = min(1.0, i["strength"] + REINFORCE_STEP)
        i["confirmed"] = i.get("confirmed", 0) + 1
        i["last_confirmed"] = day
        if evidence:
            i["evidence"] = (i.get("evidence", []) + list(evidence))[-EVIDENCE_KEPT:]
        if (source and source != SELF_REPORTED
                and i.get("source", SELF_REPORTED) not in SOURCES[:4]):
            i["source"] = source
        return f"reinforced #{item_id}"

    def drop(self, item_id: int) -> str:
        i = self.get(item_id)
        if not i:
            return f"rejected: no memory #{item_id}"
        if i.get("locked"):
            return f"rejected: #{item_id} is settled"
        self.items.remove(i)
        return f"dropped #{item_id}"

    def lock(self, item_id: int) -> str:
        i = self.get(item_id)
        if not i:
            return f"rejected: no memory #{item_id}"
        i["locked"] = True
        return f"locked #{item_id}"

    def decay(self, days: float, only: set[int] | None = None) -> list[int]:
        """Strength halves every half-life, and unlocked lines under the floor are
        forgotten. ``only`` limits it to those ids: a memory made tonight has not aged."""
        dropped = []
        for i in list(self.items):
            if i.get("locked") or (only is not None and i["id"] not in only):
                continue
            half_life = HALF_LIFE_DAYS.get(i["section"], DEFAULT_HALF_LIFE)
            if i.get("weight") == "core":
                half_life *= CORE_HALF_LIFE_FACTOR
            i["strength"] = round(i["strength"] * 0.5 ** (days / half_life), 4)
            if i["strength"] < DROP_BELOW:
                self.items.remove(i)
                dropped.append(i["id"])
        return dropped
