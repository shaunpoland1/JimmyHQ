"""How many things wait on the owner in Articles: the number beside it in the sidebar,
right without the page being open.

The page lists what needs the owner on Today (``web/lib/today.js``: ``needs``, and ``count``
which adds the lines up). This is the same count made by the program. ``total`` is the one
place the lines are added up. ``count`` asks the records; ``in_overview`` reads the same
lines out of the list the page is given, and exists so a test can hold the two together
and hold both to the cases the page's script is also held to
(``tests/fixtures/waiting_cases.json``).

A first-run hint ("Set up a site") is guidance and never counted.
"""

from __future__ import annotations

from typing import Any

from articles import replies
from articles.context import Ctx

# One line each on Today, by the state an article is in: Held, Stuck, Publishing with no
# answer, Approved, Sent. Articles "to decide" are those that are ``ready``.
LINES = ("ready", "unsure", "held_coverage", "stopped_research", "stalled", "approved", "sent")


def total(*, by_state: dict[str, int], ideas: int, unread: int) -> int:
    """The lines of every group Today shows, added up: one for each article in a state of
    ``LINES``, one when ideas wait and one when replies are not looked at yet."""
    return (sum(by_state.get(state, 0) for state in LINES) + (1 if ideas else 0)
            + (1 if unread else 0))


def count(ctx: Ctx) -> int:
    """How many things wait on the owner now, from the records."""
    marks = ",".join("?" * len(LINES))
    rows = ctx.store.all(f"SELECT state, COUNT(*) AS n FROM piece WHERE state IN ({marks}) "  # noqa: S608
                         "GROUP BY state", LINES)
    waiting_ideas = ctx.replies.one("SELECT COUNT(*) AS n FROM idea WHERE status='new'")
    return total(by_state={r["state"]: int(r["n"]) for r in rows},
                 ideas=int((waiting_ideas or {"n": 0})["n"]),
                 unread=int(replies.status(ctx)["unread"]))


def in_overview(data: dict[str, Any]) -> int:
    """The same number, read from what the page is given (``screen.overview``)."""
    by_state: dict[str, int] = {}
    for piece in data.get("pieces") or []:
        by_state[piece["state"]] = by_state.get(piece["state"], 0) + 1
    return total(by_state=by_state, ideas=len(data.get("ideas") or []),
                 unread=int((data.get("replies") or {}).get("unread") or 0))
