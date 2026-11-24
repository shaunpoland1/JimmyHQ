"""The one table of allowed state changes for a piece: an article from its topic to the
owner's site.

With agents (``work``), the same states carry the work: ``researching`` is the research
given to the researcher (it ends in ``outlining`` with its summary, or goes back to
``searching`` to be done the plain way); ``outlining`` and ``drafting`` are the writer's,
``reviewing`` and ``rechecking`` the checker's, ``repairing`` and ``revising`` the writer's
again. Who did a step is in the piece's credits, never in its state.

Every change of state goes through ``Store.move``, which asks ``need`` here first. A
change not in the table is refused with a sentence, so no path (a click, the job, an agent
later) can skip a step. Each state's way out is in the table; ``WAYS_OUT`` says it in
words for the screen.
"""

from __future__ import annotations

PIECE: dict[str, frozenset[str]] = {
    "coverage_check": frozenset({"searching", "held_coverage", "stalled", "dropped"}),
    "held_coverage": frozenset({"searching", "dropped"}),
    "searching": frozenset({"reading", "researching", "stopped_research", "stalled",
                            "dropped"}),
    "researching": frozenset({"outlining", "searching", "stalled", "dropped"}),
    "reading": frozenset({"summarising", "stopped_research", "stalled", "dropped"}),
    "stopped_research": frozenset({"searching", "dropped"}),
    "summarising": frozenset({"outlining", "stalled", "dropped"}),
    "outlining": frozenset({"drafting", "stalled", "dropped"}),
    "drafting": frozenset({"reviewing", "stalled", "dropped"}),
    "reviewing": frozenset({"repairing", "ready", "stalled", "dropped"}),
    "repairing": frozenset({"rechecking", "ready", "stalled", "dropped"}),
    "rechecking": frozenset({"repairing", "ready", "stalled", "dropped"}),
    "ready": frozenset({"ready", "revising", "approved", "declined", "dropped"}),
    "revising": frozenset({"reviewing", "stalled", "dropped"}),
    "approved": frozenset({"publishing", "dropped"}),
    "publishing": frozenset({"sent", "approved", "unsure"}),
    "unsure": frozenset({"sent", "approved"}),
    "sent": frozenset({"published"}),
    "stalled": frozenset({"coverage_check", "searching", "researching", "reading",
                          "summarising",
                          "outlining", "drafting", "reviewing", "repairing", "rechecking",
                          "revising", "dropped"}),
    "published": frozenset(),
    "declined": frozenset(),
    "dropped": frozenset(),
}

# The steps an ``advance`` run carries out, in the order a piece meets them.
WORK = ("coverage_check", "searching", "researching", "reading", "summarising", "outlining",
        "drafting", "reviewing", "repairing", "rechecking", "revising")
OPEN = frozenset(PIECE) - {"published", "declined", "dropped"}
# What "have we written about this lately" compares with: everything but the abandoned.
COUNTED = frozenset(PIECE) - {"declined", "dropped"}
# The owner approved it, and it has not gone back: what may be given to anyone with no role
# (``outside``). Every state an approved piece can reach is here, and only those.
APPROVED = frozenset({"approved", "publishing", "unsure", "sent", "published"})

WORDS = {
    "coverage_check": "checking earlier articles", "held_coverage": "held: close to an "
    "earlier article", "searching": "searching", "researching": "being researched",
    "reading": "reading the sources",
    "stopped_research": "stopped: too few good sources", "summarising": "summarising the "
    "research", "outlining": "outlining", "drafting": "writing", "reviewing": "being "
    "reviewed", "repairing": "being repaired", "rechecking": "checking the repair",
    "ready": "waiting for you", "revising": "being revised from your note",
    "approved": "approved", "publishing": "being published", "unsure": "unsure: no answer "
    "from the site", "sent": "sent", "published": "published", "stalled": "stuck",
    "declined": "declined", "dropped": "dropped",
}

WAYS_OUT = {
    "coverage_check": "it carries on by itself, or Drop",
    "held_coverage": "Write anyway, or Drop",
    "searching": "it carries on by itself, or Drop", "reading": "it carries on by itself, or Drop",
    "researching": "it carries on when the researcher hands back its summary, or Drop",
    "stopped_research": "Retry, or Drop",
    "summarising": "it carries on by itself, or Drop", "outlining": "it carries on by itself, "
    "or Drop", "drafting": "it carries on by itself, or Drop",
    "reviewing": "it carries on by itself, or Drop", "repairing": "it carries on by itself, "
    "or Drop", "rechecking": "it carries on by itself, or Drop",
    "ready": "Approve, Send back with a note, Decline with a reason, edit it, or Drop",
    "revising": "it carries on by itself, or Drop",
    "approved": "Publish (when its site sends out), or Drop",
    "publishing": "it ends by itself with the site's answer",
    "unsure": "look on the site, then It is there: Published, or I checked, it is not there",
    "sent": "Published, once it is live", "stalled": "Retry, or Drop",
    "published": "none: done", "declined": "none: its reason is a lesson",
    "dropped": "none",
}


class StateError(ValueError):
    """A change of state the table does not allow. The message is for the owner."""


def allowed(old: str, new: str) -> bool:
    return new in PIECE.get(old, frozenset())


def need(old: str, new: str) -> None:
    """Raise ``StateError`` unless the table allows ``old`` to become ``new``."""
    if old not in PIECE or new not in PIECE:
        raise StateError(f"An article cannot be {WORDS.get(new, new)}: Articles knows no such "
                         "state. Nothing was changed.")
    if not allowed(old, new):
        raise StateError(f"This article is {WORDS.get(old, old)}, so it cannot be "
                         f"{WORDS.get(new, new)} now. Nothing was changed. Its ways on: "
                         f"{WAYS_OUT.get(old, 'none')}.")
