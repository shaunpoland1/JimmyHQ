"""The one table of allowed state changes, for a piece and for a post.

Every change of state anywhere in Posts goes through ``move_piece`` or ``move_post`` in
``store``, which ask ``allowed`` here first. A change that is not in the table is refused
with a sentence, so no path (a click, a job, an agent later) can skip a step.

A piece is the work: reading the source, the overlap check, drafting, checking and
revising, until a draft is ready for the owner. A post is the text that goes out, from
its first draft to the scheduler.
"""

from __future__ import annotations

# --- a piece -------------------------------------------------------------------------------

PIECE: dict[str, frozenset[str]] = {
    "proposed": frozenset({"reading", "analysing", "overlap_check", "dropped"}),
    "reading": frozenset({"overlap_check", "stopped_source", "stalled", "dropped"}),
    "stopped_source": frozenset({"reading", "dropped"}),
    "analysing": frozenset({"overlap_check", "stalled", "dropped"}),
    "overlap_check": frozenset({"writing", "captioning", "held_overlap", "stalled", "dropped"}),
    "held_overlap": frozenset({"writing", "captioning", "dropped"}),
    "writing": frozenset({"drafted", "stalled", "dropped"}),
    "captioning": frozenset({"drafted", "stalled", "dropped"}),
    "drafted": frozenset({"in_check", "ready", "stalled", "dropped"}),
    "in_check": frozenset({"revising", "ready", "stalled", "dropped"}),
    "revising": frozenset({"drafted", "stalled", "dropped"}),
    "ready": frozenset({"revising", "closed", "dropped"}),
    "stalled": frozenset({"reading", "analysing", "overlap_check", "writing", "captioning",
                          "drafted", "in_check", "revising", "dropped"}),
    "closed": frozenset(),
    "dropped": frozenset(),
}

# The steps an ``advance`` run carries out, in the order a piece meets them.
PIECE_WORK = ("reading", "analysing", "overlap_check", "writing", "captioning", "drafted",
              "in_check", "revising")
PIECE_OPEN = frozenset(PIECE) - {"closed", "dropped"}

# --- a post --------------------------------------------------------------------------------

POST: dict[str, frozenset[str]] = {
    "draft": frozenset({"draft", "queued", "revising", "declined"}),
    "revising": frozenset({"draft", "declined"}),
    "queued": frozenset({"queued", "booking", "went_out", "declined"}),
    "booking": frozenset({"booked", "queued", "unsure"}),
    "unsure": frozenset({"booked", "queued"}),
    # booked -> booking: "Book the rest" books the accounts a partial booking did not reach
    "booked": frozenset({"booked", "booking", "went_out", "declined", "missing", "failed"}),
    "went_out": frozenset(),
    "declined": frozenset(),
    "missing": frozenset({"queued", "declined"}),
    "failed": frozenset({"queued", "declined"}),  # the scheduler said it could not publish
}

# Posts that are going out or went out: what overlap and the daily limit count.
POST_OUT = frozenset({"queued", "booking", "booked", "unsure", "went_out"})
POST_ON_SCHEDULE = frozenset({"booking", "booked", "unsure", "went_out"})

WORDS = {
    "proposed": "proposed", "reading": "reading the source",
    "stopped_source": "stopped: the source could not be used",
    "analysing": "looking at the pictures", "captioning": "writing the caption",
    "overlap_check": "checking for overlap", "held_overlap": "held: close to an earlier post",
    "writing": "drafting", "drafted": "drafted, checks run", "in_check": "being checked",
    "revising": "being revised",
    "ready": "waiting for you", "stalled": "stuck", "closed": "done", "dropped": "dropped",
    "draft": "a draft", "queued": "queued", "booking": "being booked", "unsure": "unsure",
    "booked": "booked", "went_out": "already out", "declined": "declined", "missing": "missing",
    "failed": "failed to go out",
}


class StateError(ValueError):
    """A change of state the table does not allow. The message is for the owner."""


def allowed(table: dict[str, frozenset[str]], old: str, new: str) -> bool:
    return new in table.get(old, frozenset())


def need(table: dict[str, frozenset[str]], what: str, old: str, new: str) -> None:
    """Raise ``StateError`` unless the table allows ``old`` to become ``new``."""
    if old not in table or new not in table:
        raise StateError(f"A {what} cannot be {WORDS.get(new, new)}: Posts knows no such "
                         "state. Nothing was changed.")
    if not allowed(table, old, new):
        raise StateError(f"This {what} is {WORDS.get(old, old)}, so it cannot be "
                         f"{WORDS.get(new, new)} now. Nothing was changed.")
