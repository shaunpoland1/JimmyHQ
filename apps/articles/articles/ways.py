"""Ways in: how an article starts. Every way ends in ``start_piece``, so every article meets
the same steps from "have we written about this lately" on, whoever started it.

- ``topic``: the owner's topic, with an optional angle and optional notes.
- ``idea``: an idea found in readers' replies, which the owner chose to start
  (``replies.start_idea`` calls ``start_piece`` with ``way="idea"``).
- ``reader`` (later): an article Reader offers through its feed ``reader.articles``. It
  adds one function here and one line to ``WAYS``; the item's address becomes the first
  candidate in ``research`` and nothing after that changes.
"""

from __future__ import annotations

from typing import Any

from articles.context import Ctx
from articles.sites import NO_SITES

TOPIC_MOST = 300
ANGLE_MOST = 600
NOTES_MOST = 4000
KNOWN = ("topic", "idea", "reader")


class WayError(ValueError):
    """Nothing was started. The message says why and what to do."""


def _text(args: dict[str, Any], key: str, most: int) -> str:
    text = " ".join(str(args.get(key) or "").split()) if key != "notes" else \
        str(args.get(key) or "").replace("\r\n", "\n").strip()
    if len(text) > most:
        raise WayError(f"The {key} is at most {most} characters. Nothing was started.")
    return text


def _site(ctx: Ctx, site_id: Any) -> dict[str, Any]:
    if not ctx.store.sites():
        raise WayError(NO_SITES)
    try:
        site = ctx.store.site(int(site_id))
    except (TypeError, ValueError):
        site = None
    if site is None:
        raise WayError("Choose which site the article is for. Nothing was started.")
    return site


def start_piece(ctx: Ctx, *, site_id: Any, way: str, topic: str, angle: str = "",
                notes: str = "", origin: str = "") -> dict[str, Any]:
    """A new article, waiting for its first step: the coverage check, before anything is
    spent. ``origin`` names where it came from by id only (``idea:3``), never by text."""
    if way not in KNOWN:
        raise WayError(f"Articles knows no way in called {way!r}. Nothing was started.")
    site = _site(ctx, site_id)
    if not topic:
        raise WayError("Say what the article is about. Nothing was started.")
    now = ctx.now()
    piece_id = ctx.store.insert("piece", {
        "site_id": site["id"], "way": way, "origin": origin, "topic": topic, "angle": angle,
        "notes": notes, "state": "coverage_check", "created": now, "updated": now})
    ctx.store.add_turn(piece_id, "started", f"Started from {_said(way)} for {site['name']}.",
                       role="owner")
    ctx.tape("piece.started", {"piece": piece_id, "way": way}, subject=f"piece:{piece_id}")
    ctx.emit()
    return ctx.store.piece(piece_id) or {}


def _said(way: str) -> str:
    return {"topic": "your topic", "idea": "an idea in readers' replies",
            "reader": "an article Reader offered"}[way]


def start_topic(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The owner's own topic, angle and notes."""
    return start_piece(ctx, site_id=args.get("site_id"), way="topic",
                       topic=_text(args, "topic", TOPIC_MOST),
                       angle=_text(args, "angle", ANGLE_MOST),
                       notes=_text(args, "notes", NOTES_MOST))


# The page's "new.<way>" calls. An idea starts from the Ideas list (``idea.start``).
WAYS = {"topic": start_topic}
