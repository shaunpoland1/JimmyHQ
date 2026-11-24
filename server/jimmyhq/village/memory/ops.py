"""The sleeping mind's answer: its shape, how it is read, and how it is applied.

The model proposes operations; the store decides. Nothing here trusts the answer:
ids are parsed leniently, evidence is checked against the log, a tools line must match
what the tools really did, and a pass may not drop a line it has just rewritten.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from jimmyhq.village.memory import provenance
from jimmyhq.village.memory.store import NAP_EVICTIONS, SECTIONS, MemoryStore

MAX_OPS = 60
MAX_EVIDENCE = 6
MAX_HEARD = 20
MAX_IMPRESSIONS = 6
NAME_LOCK_NIGHTS = 3

_IMPRESSION = {
    "type": "object",
    "properties": {"about": {"type": "string"}, "text": {"type": "string", "minLength": 1}},
    "required": ["about", "text"],
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operations": {"type": "array", "maxItems": MAX_OPS, "items": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["add", "replace", "reinforce", "drop"]},
                "id": {"type": "integer"},
                "section": {"type": "string", "enum": list(SECTIONS)},
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": ["name", "gender", "other"]},
                "weight": {"type": "string", "enum": ["core", "useful", "routine"]},
                "evidence": {"type": "array", "maxItems": MAX_EVIDENCE,
                             "items": {"type": "string"}},
            },
            "required": ["op"]}},
        "impressions": {"type": "array", "maxItems": MAX_IMPRESSIONS, "items": _IMPRESSION},
        "heard": {"type": "array", "maxItems": MAX_HEARD, "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["operations"],
}
IMPRESSIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"impressions": {"type": "array", "maxItems": MAX_IMPRESSIONS,
                                   "items": _IMPRESSION}},
    "required": ["impressions"],
}


def parse_answer(content: str) -> dict[str, Any]:
    """The answer as JSON, tolerating a code fence or stray words around it."""
    text = re.sub(r"^```(json)?|```$", "", (content or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        try:
            return json.JSONDecoder().raw_decode(text[start:])[0]
        except json.JSONDecodeError:
            salvaged = salvage_operations(text[start:])
            if salvaged is None:
                raise
            return salvaged


def whole_items(text: str, key: str) -> list[Any]:
    """The objects that came back whole in the list under ``key``, read one at a time
    from where the list opens until the first one that is not complete."""
    opened = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if opened is None:
        return []
    decoder, at, found = json.JSONDecoder(), opened.end(), []
    while True:
        while at < len(text) and text[at] in " \t\r\n,":
            at += 1
        if at >= len(text) or text[at] != "{":
            return found
        try:
            item, at = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            return found
        found.append(item)


def salvage_operations(text: str) -> dict[str, Any] | None:
    """From an answer cut off part-way, keep every operation that came back whole, so
    the night sorts what it could instead of nothing. Impressions that came back whole
    are kept too. None when not one whole operation is there."""
    operations = whole_items(text or "", "operations")
    if not operations:
        return None
    out: dict[str, Any] = {"operations": operations, "salvaged": True}
    impressions = whole_items(text, "impressions")
    if impressions:
        out["impressions"] = impressions
    return out


def mem_id(value: Any) -> int:
    """A memory id as a model writes it: 6, "6" or "#6". Memory is shown with "#6", and
    a model copies what it sees."""
    return int(str(value).strip().lstrip("#").strip())


def unique_ops(ops: list[Any]) -> list[Any]:
    """Each operation once. A weak model repeats itself; once is enough."""
    seen: dict[str, Any] = {}
    for op in ops:
        seen.setdefault(json.dumps(op, sort_keys=True, default=str), op)
    return list(seen.values())


def name_of(text: str) -> str:
    """The name in a story line written as "Name: Wren"."""
    return text.split(":", 1)[-1].strip()


@dataclass
class Applied:
    results: list[str]
    name_nights: dict[str, list[str]] = field(default_factory=dict)
    locked_names: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> int:
        return sum(1 for r in self.results if r.startswith("rejected"))

    def all_rejected(self, n_ops: int) -> bool:
        return bool(n_ops) and all(r.startswith("rejected") for r in self.results[:n_ops])


def _lower(value: Any) -> str | None:
    return value.strip().lower() or None if isinstance(value, str) else None


def apply_ops(log: Any, agent_id: str, store: MemoryStore, ops: list[Any], day: str,
              by: str, *, nap: bool = False, lock_nights: int = NAME_LOCK_NIGHTS,
              news_shown: bool = False,
              name_nights: dict[str, list[str]] | None = None) -> Applied:
    """Apply the operations to the store (in memory; the caller saves) and say what
    happened to each. ``name_nights`` is on which nights each name has come up; the
    updated record comes back in the result."""
    results: list[str] = []
    nights = {k: list(v) for k, v in (name_nights or {}).items()}
    store.keep = provenance.owner_sourced(log, agent_id, store.items)
    store.evict_budget = NAP_EVICTIONS if nap else None
    # Lines this pass rewrote or reinforced. A later "drop" of one contradicts the pass
    # itself, and has cost an agent the plan it had just written for the next morning.
    kept: set[int] = set()
    try:
        for op in ops[:MAX_OPS]:
            if not isinstance(op, dict):
                results.append("rejected: malformed op (not an object)")
                continue
            results.append(_apply_one(log, agent_id, store, op, day, by, news_shown,
                                      nights, kept))
        # A name settles only after it has come up on several different nights.
        locked = []
        for item in store.section("story"):
            if item.get("kind") == "name" and not item.get("locked"):
                name = name_of(item["text"])
                if name and len(nights.get(name, [])) >= lock_nights:
                    results.append(store.lock(item["id"]))
                    locked.append(name)
    finally:
        store.keep, store.evict_budget = set(), None
    return Applied(results, nights, locked)


def _apply_one(log: Any, agent_id: str, store: MemoryStore, op: dict[str, Any], day: str,
               by: str, news_shown: bool, nights: dict[str, list[str]],
               kept: set[int]) -> str:
    what = _lower(op.get("op")) or ""
    section = _lower(op.get("section"))
    line_kind = _lower(op.get("kind"))
    text = op["text"].strip() if isinstance(op.get("text"), str) else ""
    weight = _lower(op.get("weight"))
    raw = op.get("evidence") if isinstance(op.get("evidence"), list) else []
    cited = [str(x) for x in raw][:MAX_EVIDENCE]
    events = provenance.known_events(log, agent_id, cited)
    evidence = [e.id for e in events]
    from_changes = news_shown and provenance.CHANGES in {x.strip().lower() for x in cited}
    try:
        if what not in ("add", "replace", "reinforce", "drop"):
            return f"rejected: unknown op {what or '?'}"
        item = None if what == "add" else store.get(mem_id(op["id"]))
        if what != "add" and item is None:
            return f"rejected: no memory #{mem_id(op['id'])}"
        # A rewrite is judged by the section the line is in, whatever the op says.
        where = section if what == "add" else item["section"]
        if what in ("add", "replace") and where == "tools":
            ok, why = provenance.verify_tool_lesson(agent_id, text, events)
            if not ok:
                return f"rejected tool lesson ({why}): {text[:80]}"
        source = provenance.classify(agent_id, where, text or (item or {}).get("text", ""),
                                     events, from_changes)
        if what == "add":
            if line_kind == "name" and name_of(text):
                nights[name_of(text)] = sorted(set(nights.get(name_of(text), [])) | {day})
            return store.add(where or "", text, source, evidence, day, by, weight=weight,
                             kind=line_kind if line_kind in ("name", "gender") else None)
        if what == "replace":
            kept.add(item["id"])
            return store.replace(item["id"], text, day, evidence, by, weight=weight,
                                 source=source)
        if what == "reinforce":
            kept.add(item["id"])
            return store.reinforce(item["id"], day, evidence, source=source)
        if item["id"] in kept:
            return (f"rejected drop #{item['id']}: this pass already rewrote or "
                    "reinforced it")
        return store.drop(item["id"])
    except (KeyError, ValueError, TypeError) as exc:
        return f"rejected: malformed op ({type(exc).__name__}: {exc})"
