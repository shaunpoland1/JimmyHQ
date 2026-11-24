"""The ways a piece comes in. Every way starts the same: a piece in ``reading`` (or, with
no article, straight to ``overlap_check``), so the source is read and the overlap check
runs before anything is spent on drafting.

There are a pasted link, a typed topic and uploaded pictures (which are looked at first,
in ``analysing``). Articles Reader offers come in through ``from_reader``, which calls
``start`` with the address alone and an ``origin`` ("from Reader, topic X") the piece
keeps and the screen shows.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from posts import credits, pages, pictures, sources
from posts.context import Ctx

MAX_NOTES = 4000
MAX_TOPIC = 300


class IntakeError(ValueError):
    """Nothing was started. The message says what to change."""


def start(ctx: Ctx, *, page_id: Any, way: str, source_url: str | None = None,
          topic: str = "", notes: str = "", sponsored: bool = False,
          came_from: str = "", by: str = "owner", first: str | None = None,
          kept: list[dict[str, Any]] | None = None, origin: str = "") -> dict[str, Any]:
    """Make the piece, in its first step (``first``, else reading a link or the
    overlap check). ``kept`` is the pictures to keep with it. ``by`` is who started it
    (the owner, or ``researcher`` for an agent's proposal), for the piece's own line.
    Returns it."""
    try:
        page = pages.need_page(ctx.store, page_id)
    except pages.PageError as error:
        raise IntakeError(str(error)) from None
    first = first or ("reading" if source_url else "overlap_check")
    piece_id = ctx.store.insert("piece", {
        "page_id": page["id"], "way": way, "source_url": source_url, "topic": topic,
        "notes": notes, "sponsored": int(bool(sponsored)), "came_from": came_from[:200],
        "origin": origin[:200], "state": "proposed", "created": ctx.now(), "updated": ctx.now()})
    if kept:
        pictures.keep(ctx, piece_id, kept)
    piece = ctx.store.move_piece(piece_id, first)
    ctx.store.update("piece", piece_id, {"came_from": ""})
    ctx.store.add_turn(piece_id, "proposed", f"Started from {_how(way, source_url, came_from)}.",
                       role=by)
    if by == "owner":
        credits.add(ctx.store, piece_id, "research", "owner")
    ctx.tape("piece.started", {"piece": piece_id, "way": way}, subject=f"piece:{piece_id}")
    ctx.emit("posts.changed")
    return ctx.store.piece(piece_id) or piece


def _how(way: str, url: str | None, came_from: str) -> str:
    if came_from:
        return came_from
    if way == "link":
        return f"a link you pasted ({sources.host_of(url)})"
    if way == "pictures":
        return "pictures you uploaded"
    return "a topic you typed"


def _text(value: Any, what: str, most: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) > most:
        raise IntakeError(f"The {what} is {len(text)} characters; keep it to {most}. Nothing "
                          "was started.")
    return text


def from_link(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    try:
        url = sources.check_url(args.get("url"))
    except sources.SourceProblem as problem:
        raise IntakeError(str(problem)) from None
    return start(ctx, page_id=args.get("page_id"), way="link", source_url=url,
                 notes=_text(args.get("notes"), "notes", MAX_NOTES),
                 sponsored=bool(args.get("sponsored")))


def from_topic(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    topic = _text(args.get("topic"), "topic", MAX_TOPIC)
    if not topic:
        raise IntakeError("Type a topic for the post. Nothing was started.")
    return start(ctx, page_id=args.get("page_id"), way="topic", topic=topic,
                 notes=_text(args.get("notes"), "notes", MAX_NOTES),
                 sponsored=bool(args.get("sponsored")))


def from_pictures(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Pictures the owner uploaded on the page (``hq.files.upload``), each with an
    optional note, and notes for the whole post."""
    notes = _text(args.get("notes"), "notes", MAX_NOTES)
    try:
        pages.need_page(ctx.store, args.get("page_id"))
        kept = pictures.check_pictures(ctx, args.get("pictures"),
                                       int(ctx.cfg().pictures_per_piece))
    except (pages.PageError, pictures.PictureError) as error:
        raise IntakeError(str(error)) from None
    return start(ctx, page_id=args.get("page_id"), way="pictures", notes=notes,
                 sponsored=bool(args.get("sponsored")), first="analysing", kept=kept,
                 came_from=f"{len(kept)} picture{'s' if len(kept) != 1 else ''} you "
                           "uploaded")


WAYS: dict[str, Callable[[Ctx, dict[str, Any]], dict[str, Any]]] = {
    "link": from_link,
    "topic": from_topic,
    "pictures": from_pictures,
}
