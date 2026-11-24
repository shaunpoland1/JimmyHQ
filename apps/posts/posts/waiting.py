"""How many things wait on the owner in Posts: the number beside it in the sidebar, right
without the page being open.

The page lists what needs the owner on Today (``web/lib/today.js``: ``needs``, and
``waiting`` which adds the lines up); this is the same count made by the program, so the
sidebar does not depend on the page being opened. ``total`` is the one place the lines are
added up. ``count`` asks the records; ``in_overview`` reads the same lines out of the list
the page is given, and exists so a test can hold the two together and hold both to the
cases the page's script is also held to (``tests/fixtures/waiting_cases.json``).

A first-run hint ("Add a brand") is guidance and never counted.
"""

from __future__ import annotations

from typing import Any

from posts import from_reader
from posts.context import Ctx

# Pieces that stopped and need the owner (``STUCK_PIECES`` in ``web/lib/lists.js``).
STUCK_PIECES = ("stalled", "stopped_source", "held_overlap")
# Today lists at most this many of Reader's ideas, a line each (``ideasWaiting``).
IDEAS_SHOWN = 5


def total(*, drafts: int, unsure: int, apart: int, stuck: int, untimed: int, ideas: int,
          reader_loud: bool) -> int:
    """The lines of every group Today shows, added up. A post can be in more than one
    group (an out-of-step post with no answer is in both), as it is on Today."""
    return (int(reader_loud) + drafts + unsure + apart + stuck + untimed
            + min(ideas, IDEAS_SHOWN))


def _n(ctx: Ctx, sql: str, *params: Any) -> int:
    return int((ctx.store.one(sql, params) or {"n": 0})["n"])


def count(ctx: Ctx) -> int:
    """How many things wait on the owner now, from the records."""
    marks = ",".join("?" * len(STUCK_PIECES))
    return total(
        drafts=_n(ctx, "SELECT COUNT(*) AS n FROM post o JOIN piece i ON i.id = o.piece_id "
                       "WHERE o.state = 'draft' AND i.state = 'ready'"),
        unsure=_n(ctx, "SELECT COUNT(*) AS n FROM post WHERE state = 'unsure'"),
        apart=_n(ctx, "SELECT COUNT(*) AS n FROM post WHERE out_of_step = 1 OR "
                      "state = 'missing'"),
        stuck=_n(ctx, f"SELECT COUNT(*) AS n FROM piece WHERE state IN ({marks})",  # noqa: S608
                 *STUCK_PIECES),
        untimed=_n(ctx, "SELECT COUNT(*) AS n FROM post WHERE state = 'queued' AND "
                        "slot_ms IS NULL"),
        ideas=_n(ctx, "SELECT COUNT(*) AS n FROM idea WHERE state = 'new'"),
        reader_loud=bool(from_reader.status(ctx)["loud"]))


def in_overview(data: dict[str, Any]) -> int:
    """The same number, read from what the page is given (``screen.overview``)."""
    posts = data.get("posts") or []
    pieces = data.get("pieces") or []
    return total(
        drafts=sum(1 for p in posts if p["state"] == "draft" and p["piece_state"] == "ready"),
        unsure=sum(1 for p in posts if p["state"] == "unsure"),
        apart=sum(1 for p in posts if p.get("out_of_step") or p["state"] == "missing"),
        stuck=sum(1 for p in pieces if p["state"] in STUCK_PIECES),
        untimed=sum(1 for p in posts if p["state"] == "queued"
                    and p.get("slot_ms") is None),
        ideas=len(data.get("ideas") or []),
        reader_loud=bool((data.get("reader") or {}).get("loud")))
