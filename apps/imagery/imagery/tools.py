"""The one tool Imagery offers agents: a read-only list of the pictures made lately.

It reads Imagery's own records only (no network, no service, no Bench). It gives each
picture's number, time, service, model, shape and the owner's description; never the
picture, a key or an address. Everything comes back in the ``shown`` form, since the
description is the owner's own words. It cannot make a picture: a tool that spends money
would have to wait on the ladder, and no first-party app offers one yet, so this app does
not.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from jimmyhq_app import App

from imagery.context import Ctx
from imagery.rules import Refused, whole

DEFAULT = 10
MOST = 25
TEXT = 400  # characters kept of one description
CUT = " (cut)"


def stamp(ms: int) -> str:
    """A stored time at the display edge: the day and minute, in UTC, saying so."""
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


def clip(text: Any, most: int) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= most else line[:max(0, most - len(CUT))].rstrip() + CUT


def limit_of(args: dict[str, Any]) -> int:
    value = args.get("limit")
    if value is None or value == "":
        return DEFAULT
    try:
        number = whole(value, "limit")
    except Refused:
        number = 0
    if not 1 <= number <= MOST:
        raise Refused(f"\"limit\" must be a whole number from 1 to {MOST}, and you gave "
                      f"{str(value)[:40]!r}. Nothing was read. Leave it out for {DEFAULT}.")
    return number


def recent(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    most = limit_of(args)
    store = ctx.store
    total = store.number("SELECT COUNT(*) FROM picture WHERE kind='made'")
    found = store.all("SELECT * FROM picture WHERE kind='made' ORDER BY created DESC, id "
                      "DESC LIMIT ?", (most,))
    rows = [f"- Picture {p['id']}, {stamp(p['created'])}, {clip(p['provider'], 40)} "
            f"{clip(p['model'], 60)}, shape {p['aspect']}"
            f"{', starred' if p['starred'] else ''}"
            f"{', style ' + clip(p['style'], 40) if p['style'] else ''}\n"
            f"  {clip(p['prompt'], TEXT)}" for p in found]
    head = (f"Pictures the owner made lately with Imagery, newest first. {total} in all. "
            "The words are the owner's own descriptions. The pictures themselves, keys and "
            "addresses are not given.")
    lines = [head, *rows] if rows else [head, "Nothing has been made yet."]
    if len(found) < total:
        lines.append(f"({total - len(found)} older not shown. Ask for more with limit, up "
                     f"to {MOST}.)")
    text = "\n".join(lines)
    return {"result": f"{total} pictures.", "ok": True, "shown": text}


def register(app: App, ctx: Ctx) -> None:
    def guarded(fn: Callable[[Ctx, dict[str, Any]], dict[str, Any]]) -> Callable[..., Any]:
        def run(args: dict[str, Any], agent: str = "", role: str | None = None) -> Any:
            try:
                return fn(ctx, dict(args or {}))
            except Refused as no:
                return {"result": str(no)[:300], "ok": False, "shown": str(no)}
        return run

    app.tool("recent")(guarded(recent))
