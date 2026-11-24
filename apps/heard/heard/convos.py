"""Reading conversations, and the housekeeping on them: rename a speaker, delete one,
search, save one as text, and the retention that deletes old ones."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from heard import clean
from heard.context import Ctx

HEADING_CHARS = 70
NAME_MAX = 60
DAY_MS = 86_400_000
CONFIRM = "DELETE"


def need(ctx: Ctx, conv_id: Any) -> dict[str, Any]:
    row = ctx.store.conversation(conv_id)
    if row is None:
        raise ValueError("That conversation is gone, so nothing was changed. Go back to the "
                         "list and open another.")
    return row


def _heading(ctx: Ctx, row: dict[str, Any]) -> str:
    if row["title"]:
        return row["title"]
    first = ctx.store.one("SELECT text FROM segment WHERE conv_id=? ORDER BY start_ms, id "
                          "LIMIT 1", (row["id"],))
    if first is None:
        return "An empty conversation"
    text = first["text"]
    if len(text) <= HEADING_CHARS:
        return text
    return text[:HEADING_CHARS].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def _speakers(ctx: Ctx, conv_id: int) -> list[dict[str, Any]]:
    return ctx.store.all(
        "SELECT speaker AS name, MAX(is_user) AS is_user, COUNT(*) AS lines, "
        "SUM(words) AS words FROM segment WHERE conv_id=? GROUP BY speaker "
        "ORDER BY SUM(words) DESC, speaker", (conv_id,))


def _is_open(ctx: Ctx, row: dict[str, Any]) -> bool:
    """Still being added to: it is stitched from pieces and the quiet gap has not passed."""
    return row["source"] != "omi" and row["last_seen_ms"] >= ctx.now() - ctx.gap_ms()


def _summary_row(ctx: Ctx, row: dict[str, Any]) -> dict[str, Any]:
    names = [s["name"] for s in _speakers(ctx, row["id"])]
    totals = ctx.store.one("SELECT COUNT(*) AS lines, COALESCE(SUM(words), 0) AS words "
                           "FROM segment WHERE conv_id=?", (row["id"],)) or {}
    return {
        "id": row["id"], "heading": _heading(ctx, row), "day": ctx.day_of(row["started_ms"]),
        "started_ms": row["started_ms"], "ended_ms": row["ended_ms"],
        "seconds": max(0, (row["ended_ms"] - row["started_ms"]) // 1000),
        "lines": totals.get("lines", 0), "words": totals.get("words", 0),
        "speakers": names[:4], "speaker_count": len(names),
        "has_summary": bool(row["summary"]), "open": _is_open(ctx, row),
        "category": row["category"],
    }


def listing(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Conversations, newest first, a page at a time. ``before`` is the ``next`` of the
    page before: ``{ms, id}``."""
    size = int(ctx.cfg().page_size)
    before = args.get("before")
    if isinstance(before, dict) and isinstance(before.get("ms"), int) \
            and isinstance(before.get("id"), int):
        rows = ctx.store.all(
            "SELECT * FROM conversation WHERE (started_ms, id) < (?, ?) "
            "ORDER BY started_ms DESC, id DESC LIMIT ?", (before["ms"], before["id"], size + 1))
    else:
        rows = ctx.store.all("SELECT * FROM conversation ORDER BY started_ms DESC, id DESC "
                             "LIMIT ?", (size + 1,))
    more = len(rows) > size
    rows = rows[:size]
    return {"conversations": [_summary_row(ctx, r) for r in rows],
            "next": {"ms": rows[-1]["started_ms"], "id": rows[-1]["id"]} if more else None}


def get(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    row = need(ctx, args.get("id"))
    store = ctx.store
    lines = store.all("SELECT id, speaker, is_user, text, start_ms, end_ms FROM segment "
                      "WHERE conv_id=? ORDER BY start_ms, id", (row["id"],))
    for line in lines:
        line["is_user"] = bool(line["is_user"])
    todos = store.all("SELECT id, text, quote, status, source, task_id FROM todo "
                      "WHERE conv_id=? ORDER BY id", (row["id"],))
    return {
        **_summary_row(ctx, row), "source": row["source"], "title": row["title"],
        "overview": row["overview"], "summary": row["summary"], "recorder": row["recorder"],
        "summary_ms": row["summary_ms"], "segments": lines,
        "speaker_list": _speakers(ctx, row["id"]),
        "todos": [{**t, "task_id": bool(t["task_id"])} for t in todos],
    }


def rename_speaker(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Change one speaker's name on every line of one conversation. A name that another
    speaker of the conversation already has joins the two."""
    row = need(ctx, args.get("id"))
    old = args.get("from")
    new = clean.line(args.get("to"), NAME_MAX)
    if not isinstance(old, str) or not old:
        raise ValueError("Choose which speaker to rename. Nothing was changed.")
    if not new:
        raise ValueError("Type the name to use. Nothing was changed.")
    found = ctx.store.one("SELECT COUNT(*) AS n FROM segment WHERE conv_id=? AND speaker=?",
                          (row["id"], old))
    if not found or not found["n"]:
        raise ValueError("That speaker is not in this conversation, so nothing was changed. "
                         "Open it again.")
    cur = ctx.store.conn.execute("UPDATE segment SET speaker=? WHERE conv_id=? AND speaker=?",
                                 (new, row["id"], old))
    ctx.store.conn.commit()
    return {"changed": cur.rowcount,
            "said": f"Renamed {old} to {new} on {cur.rowcount} "
                    f"{'line' if cur.rowcount == 1 else 'lines'} of this conversation."}


def delete(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Delete one conversation for good: its lines, its to-dos and its place in the search
    index are removed and overwritten."""
    row = need(ctx, args.get("id"))
    ctx.jobs.forget(f"summary:{row['id']}")
    ctx.jobs.forget(f"todos:{row['id']}")
    ctx.store.delete_conversation(row["id"])
    return {"said": "Deleted for good."}


def delete_all(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    if args.get("typed") != CONFIRM:
        raise ValueError(f"Type {CONFIRM} to delete everything. Nothing was deleted.")
    ctx.jobs.forget()
    count = ctx.store.forget_everything()
    return {"deleted": count,
            "said": f"Deleted {count} {'conversation' if count == 1 else 'conversations'} "
                    "and the tally of refused messages, for good."}


def trim(ctx: Ctx) -> str:
    """Rule ``old_conversations_are_deleted_after_the_retention_days``: the job."""
    days = int(ctx.cfg().keep_days)
    if days <= 0:
        return "Keeping everything."
    gone = ctx.store.trim(ctx.now() - days * DAY_MS)
    return f"Deleted {gone} {'conversation' if gone == 1 else 'conversations'} older than " \
           f"{days} days."


def search(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    query = clean.line(args.get("query"), 200)
    limit = int(ctx.cfg().search_limit)
    found = ctx.store.search(query, limit)
    for hit in found:
        hit["day"] = ctx.day_of(hit["started_ms"])
        hit["heading"] = hit["title"] or _heading(ctx, {"id": hit["conv_id"], "title": ""})
    return {"query": query, "results": found, "limit": limit, "full": len(found) >= limit,
            "fast": ctx.store.has_fts}


def as_text(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """One conversation as Markdown, to save as a file."""
    row = need(ctx, args.get("id"))
    zone = ctx.zone()
    out = [f"# {_heading(ctx, row)}", "",
           f"{datetime.fromtimestamp(row['started_ms'] / 1000, zone):%Y-%m-%d %H:%M}", ""]
    if row["summary"]:
        out += ["## Summary", "", row["summary"], ""]
    out += ["## Transcript", ""]
    for line in ctx.store.all("SELECT speaker, text FROM segment WHERE conv_id=? "
                              "ORDER BY start_ms, id", (row["id"],)):
        out.append(f"**{line['speaker']}:** {line['text']}")
        out.append("")
    return {"name": f"heard-{ctx.day_of(row['started_ms'])}-{row['id']}",
            "text": "\n".join(out).rstrip() + "\n"}
