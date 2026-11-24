"""What Heard's own page may ask (``hq.app.call``). Each call returns plain JSON.

The page shows every string as text, never as HTML. A refusal is a sentence the page puts on
its status line. Nothing here reaches the owner's record, the Bench, Memory or the network:
the only calls out are a question to a model on the owner's own computer (``ask``), and,
only when the owner switched it on and accepted a to-do, one task (``todos``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from heard import ask, backup, convos, shapes, todos
from heard.context import Ctx


def overview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    cfg, store = ctx.cfg(), ctx.store
    refused = [{**r, "words": shapes.REFUSALS.get(r["kind"], "The message could not be kept.")}
               for r in store.refused_tally()]
    return {
        "today": ctx.day_of(ctx.now()), "timezone": getattr(ctx.zone(), "key", "") or "",
        "connected": store.get_meta("first_seen_ms") is not None,
        "last_heard_ms": store.get_meta("last_heard_ms"), "conversations": store.count(),
        "refused": refused, "gap_minutes": int(cfg.gap_minutes), "keep_days": int(cfg.keep_days),
        "only_recorder": bool(str(cfg.only_uid).strip()),
        "model_reads": bool(cfg.model_reads), "file_tasks": bool(cfg.file_tasks),
        "fast_search": store.has_fts, "page_size": int(cfg.page_size),
    }


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    def bind(fn: Callable[[Ctx, dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
        return lambda args: fn(ctx, args or {})

    return {name: bind(fn) for name, fn in {
        "overview": overview,
        "days": convos.listing, "conversation": convos.get,
        "speaker.rename": convos.rename_speaker, "conversation.delete": convos.delete,
        "conversation.text": convos.as_text, "search": convos.search,
        "summarise": ask.summarise, "summarise.check": ask.summary_check,
        "summary.clear": ask.clear_summary,
        "todos.ask": ask.todos_ask, "todos.check": ask.todos_check,
        "todo.accept": todos.accept, "todo.decline": todos.decline, "todo.remove": todos.remove,
        "export.parts": backup.export_parts, "export.part": backup.export_part,
        "import.batch": backup.import_batch, "delete.all": convos.delete_all,
    }.items()}
