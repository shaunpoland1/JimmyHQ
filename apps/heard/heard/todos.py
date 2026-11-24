"""Deciding on a suggested to-do: the owner accepts it (perhaps with the words edited), or
declines it. Accepting is kept in Heard; with the setting on it is also added to the
owner's tasks, with a title and nothing more (a private app's tasks carry no notes)."""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError

from heard import clean
from heard.ask import TODO_MAX
from heard.context import Ctx

log = logging.getLogger("heard")
DECLINED_MOST = 200  # lines remembered for one conversation


def _todo(ctx: Ctx, todo_id: Any) -> dict[str, Any]:
    row = ctx.store.one("SELECT * FROM todo WHERE id=?", (todo_id,)) \
        if isinstance(todo_id, int) and not isinstance(todo_id, bool) else None
    if row is None:
        raise ValueError("That to-do is already dealt with. Nothing was changed.")
    return row


def accept(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    row = _todo(ctx, args.get("id"))
    if row["status"] == "accepted":
        raise ValueError("That to-do is already accepted. Nothing was changed.")
    text = clean.line(args.get("text", row["text"]), TODO_MAX)
    if not text:
        raise ValueError("Type the words to keep, or decline this one. Nothing was changed.")
    task_id, said = "", "Kept in this conversation's to-dos."
    if ctx.cfg().file_tasks:
        try:
            made = ctx.app.tasks.create(text)
            task_id = str(made.get("id") or "filed")
            said = "Kept here and added to your tasks."
        except CallError as error:
            log.info("could not add a task: %s", error.code)
            said = ("Kept here. It could not be added to your tasks, so add it there yourself "
                    "if you want it: " + error.message)
    ctx.store.update("todo", row["id"], {"text": text, "status": "accepted",
                                         "task_id": task_id})
    return {"said": said, "task": bool(task_id)}


def decline(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Drop a suggestion for good. A short line (what it said, not the quote) is kept so a
    new question does not suggest it again; it goes with the conversation."""
    row = _todo(ctx, args.get("id"))
    store = ctx.store
    store.conn.execute("INSERT OR IGNORE INTO declined (conv_id, line) VALUES (?, ?)",
                       (row["conv_id"], clean.key(row["text"])))
    store.conn.execute("DELETE FROM declined WHERE conv_id=? AND rowid NOT IN "
                       "(SELECT rowid FROM declined WHERE conv_id=? ORDER BY rowid DESC "
                       "LIMIT ?)", (row["conv_id"], row["conv_id"], DECLINED_MOST))
    store.private.purge("todo", "id=?", (row["id"],))
    return {"said": "Set aside. It will not be suggested again for this conversation."}


def remove(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Delete a to-do for good. A task already added to the owner's tasks is theirs and is
    left there."""
    row = _todo(ctx, args.get("id"))
    ctx.store.private.purge("todo", "id=?", (row["id"],))
    return {"said": "Removed from this conversation."}
