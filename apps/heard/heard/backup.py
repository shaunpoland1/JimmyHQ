"""Everything out as JSON, and the same shape back in.

A file the owner saves holds about a megabyte at most, so "everything" is divided into
parts, each a whole file of its own. A conversation too long for a piece is divided into
pieces that share its ``key``; reading them back puts them together. The page sends what it
reads back a few conversations at a time, since one call may carry only about 256 KB.

Importing is safe to repeat: a conversation with a key already kept gets only the lines it
lacks, and a to-do already there is not added twice. What comes in is checked as
strictly as what comes in at the door.
"""

from __future__ import annotations

import json
import re
from typing import Any

from heard import clean, shapes
from heard.context import Ctx
from heard.store import Line

FORMAT = 1
PART_BYTES = 900_000  # a part stays well under what one save takes
PIECE_BYTES = 120_000  # one conversation's piece stays well under what one call takes
MOST_PER_CALL = 100  # conversations in one import call
SOURCES = ("omi", "live", "generic", "import")
_UID = re.compile(r"^[0-9a-f]{20}$")


def _size(value: Any) -> int:
    """Bytes the value takes as JSON text in a file (what a save and a call are limited by)."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 2


def _conversation(ctx: Ctx, row: dict[str, Any]) -> list[dict[str, Any]]:
    """One conversation as one or more pieces, each under ``PIECE_BYTES``."""
    store = ctx.store
    head = {
        "key": row["key"], "scope": row["scope"], "source": row["source"],
        "title": row["title"], "overview": row["overview"], "category": row["category"],
        "recorder": row["recorder"], "summary": row["summary"],
        "started_ms": row["started_ms"], "ended_ms": row["ended_ms"],
        "todos": [{"text": t["text"], "quote": t["quote"], "status": t["status"],
                   "source": t["source"]}
                  for t in store.all("SELECT text, quote, status, source FROM todo "
                                     "WHERE conv_id=? ORDER BY id", (row["id"],))],
    }
    pieces: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    size = _size(head)
    for s in store.all("SELECT uid, speaker, is_user, text, offset_ms, start_ms, end_ms "
                       "FROM segment WHERE conv_id=? ORDER BY start_ms, id", (row["id"],)):
        line = {**s, "is_user": bool(s["is_user"])}
        cost = _size(line)
        if current and size + cost > PIECE_BYTES:
            pieces.append({**(head if not pieces else {"key": head["key"]}),
                           "segments": current})
            current, size = [], _size({"key": head["key"]})
        current.append(line)
        size += cost
    pieces.append({**(head if not pieces else {"key": head["key"]}), "segments": current})
    return pieces


def _parts(ctx: Ctx) -> list[list[dict[str, Any]]]:
    parts: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for row in ctx.store.all("SELECT * FROM conversation ORDER BY started_ms, id"):
        for piece in _conversation(ctx, row):
            cost = _size(piece)
            if current and size + cost > PART_BYTES:
                parts.append(current)
                current, size = [], 0
            current.append(piece)
            size += cost
    if current:
        parts.append(current)
    return parts


def export_parts(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """How "everything" divides into files."""
    parts = _parts(ctx)
    return {"parts": [{"index": i, "pieces": len(p)} for i, p in enumerate(parts)],
            "total": ctx.store.count()}


def export_part(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    parts = _parts(ctx)
    index = args.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(parts):
        raise ValueError("There is no such part to save. Press Export everything again. "
                         "Nothing was changed.")
    text = json.dumps({"app": "heard", "format": FORMAT, "kind": "everything",
                       "exported_ms": ctx.now(), "part": index + 1, "parts": len(parts),
                       "conversations": parts[index]}, ensure_ascii=False)
    return {"name": f"heard-all-{index + 1}-of-{len(parts)}", "text": text,
            "count": len(parts[index])}


# --- coming back in ------------------------------------------------------------------------


def _int(value: Any, low: int = 0, high: int = 10**14) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _lines(raw: Any, scope: str, ceiling: int) -> list[Line] | None:
    """The lines of a piece, checked as strictly as at the door: no time is kept later than
    ``ceiling`` (now, and a little slack), so a file cannot make a conversation look newer
    than it is."""
    if not isinstance(raw, list) or len(raw) > shapes.MAX_SEGMENTS:
        return None
    out: list[Line] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        text = clean.line(item.get("text"), shapes.MAX_TEXT)
        start = _int(item.get("start_ms"))
        if not text or start is None:
            return None
        end = _int(item.get("end_ms"))
        offset = _int(item.get("offset_ms", 0)) or 0
        uid = item.get("uid")
        uid = uid if isinstance(uid, str) and _UID.match(uid) else shapes.make_uid(
            scope, offset, text)
        start = min(start, ceiling)
        end = min(end, ceiling) if end is not None and end >= start else start
        out.append(Line(uid, clean.line(item.get("speaker"), shapes.MAX_NAME) or "Speaker",
                        item.get("is_user") is True, text, offset, start, end))
    return out


def _one(ctx: Ctx, raw: Any) -> dict[str, int] | str:
    """Keep one piece of a conversation. Returns counts, or the sentence for why not."""
    if not isinstance(raw, dict):
        return "An item in the file is not a conversation."
    key = clean.line(raw.get("key"), shapes.MAX_ID + 20)
    if not key:
        return "A conversation in the file has no key."
    store = ctx.store
    have = store.by_key(key)
    scope = clean.line(raw.get("scope"), shapes.MAX_ID + 20) or key
    now = ctx.now()
    lines = _lines(raw.get("segments", []), have["scope"] if have else scope,
                   now + ctx.slack_ms())
    if lines is None:
        return f"The lines of {key} could not be read."
    source = raw.get("source") if raw.get("source") in SOURCES else "import"
    added = 0
    if have is None:
        first = min((line.start_ms for line in lines), default=_int(raw.get("started_ms")) or 0)
        conv_id = store.insert("conversation", {
            "key": key, "scope": scope, "source": source,
            "title": clean.line(raw.get("title"), shapes.MAX_TITLE),
            "overview": clean.block(raw.get("overview"), shapes.MAX_OVERVIEW),
            "category": clean.line(raw.get("category"), shapes.MAX_CATEGORY),
            "recorder": clean.line(raw.get("recorder"), shapes.MAX_ID),
            "summary": clean.block(raw.get("summary"), 12_000), "started_ms": first,
            "ended_ms": first, "last_seen_ms": min(_int(raw.get("ended_ms")) or first, now),
            "created_ms": now}, commit=False)
        scope_in_use = scope
    else:
        conv_id, scope_in_use = have["id"], have["scope"]
    added = store.add_segments(conv_id, scope_in_use, lines)
    store.fit_times(conv_id)
    for todo in raw.get("todos", []) if isinstance(raw.get("todos"), list) else []:
        if not isinstance(todo, dict):
            continue
        text = clean.line(todo.get("text"), 200)
        status = "accepted" if todo.get("status") == "accepted" else "suggested"
        if text and not store.one("SELECT 1 FROM todo WHERE conv_id=? AND text=?",
                                  (conv_id, text)):
            # Where a to-do came from is kept (a file from before this said nothing: "import").
            origin = todo.get("source") if todo.get("source") in ("sender", "model") else "import"
            store.insert("todo", {"conv_id": conv_id, "text": text, "status": status,
                                  "quote": clean.line(todo.get("quote"), 400),
                                  "source": origin, "created_ms": now}, commit=False)
    return {"made": 0 if have else 1, "lines": added}


def import_batch(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Keep the conversations in one call's worth of an exported file."""
    raw = args.get("conversations")
    if not isinstance(raw, list) or len(raw) > MOST_PER_CALL:
        raise ValueError(f"Send at most {MOST_PER_CALL} conversations in one go, as a list. "
                         "Nothing was kept.")
    made = lines = 0
    skipped: list[str] = []
    for item in raw:
        done = _one(ctx, item)
        if isinstance(done, str):
            skipped.append(done)
        else:
            made += done["made"]
            lines += done["lines"]
    ctx.store.conn.commit()
    return {"conversations": made, "lines": lines, "skipped": skipped[:10],
            "skipped_count": len(skipped)}
