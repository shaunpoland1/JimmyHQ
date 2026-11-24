"""Plain mode: the whole pipeline with no agents, one step per run of the ``advance`` job.

Each step has a deadline (``step_timeout_minutes``) and writes one human line saying what
it did. The steps are ``research`` (coverage, searching, reading, summarising) and
``writing`` (outlining, drafting, reviewing, repairing, rechecking, revising). A step that
cannot finish (a model refuses, an answer is not in its shape, a deadline passes) leaves
the article ``stalled`` with a sentence; nothing is tried again until the owner presses
Retry. Publishing is never a step of the job: it happens only on the owner's click.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError

from articles import research, states, writing
from articles.context import Ctx
from articles.steps import StepError

log = logging.getLogger("articles")

MINUTE_MS = 60_000

STEPS: dict[str, Any] = {
    "coverage_check": research.coverage_step, "searching": research.search_step,
    "reading": research.read_step, "summarising": research.summary_step,
    "outlining": writing.outline_step, "drafting": writing.draft_step,
    "reviewing": writing.review_step, "repairing": writing.repair_step,
    "rechecking": writing.recheck_step, "revising": writing.revise_step,
}


def advance(ctx: Ctx) -> str:
    """One run of the job: tidy what timed out, then carry one article one step on, the
    plain way or by giving the step to the agent in its role (``work.route``). Returns
    the line it wrote, for the job's log."""
    from articles import work  # noqa: PLC0415 - work hands plain steps back to here

    expire(ctx)
    marks = ",".join("?" * len(states.WORK))
    for piece in ctx.store.all(f"SELECT * FROM piece WHERE state IN ({marks}) AND "  # noqa: S608
                               "step_started IS NULL ORDER BY updated, id", states.WORK):
        try:
            routed = work.route(ctx, piece)
        except CallError as error:
            log.warning("could not give article %s to an agent: %s", piece["id"],
                        error.message)
            continue
        if routed is work.PLAIN:
            said = step(ctx, piece["id"])
            if said is not None:
                work.after_plain(ctx, piece["id"], piece["state"])
                return said
        elif routed is not None:
            ctx.emit()
            return routed
    return "Nothing to carry on."


def no_answer(ctx: Ctx) -> int:
    """Rule ``no answer is unsure``: an attempt to publish still waiting long after its
    wait (the program stopped while it was asking) may have reached the site, so it
    becomes unsure, and is never sent again by itself."""
    since = ctx.now() - 2 * int(ctx.cfg().publish_timeout_s) * 1000
    count = 0
    for attempt in ctx.store.all("SELECT * FROM attempt WHERE outcome='pending' AND "
                                 "started < ?", (since,)):
        said = ("The site gave no answer before Articles stopped. Look on the site before "
                "trying again.")
        ctx.store.update("attempt", attempt["id"], {"outcome": "unsure", "finished": ctx.now(),
                                                    "detail": said})
        ctx.tape("publish.result", {"piece": attempt["piece_id"], "attempt": attempt["id"],
                                    "outcome": "unsure"}, subject=f"piece:{attempt['piece_id']}")
        piece = ctx.store.piece(attempt["piece_id"])
        if piece and piece["state"] == "publishing":
            ctx.store.move(piece["id"], "unsure", error=said)
        count += 1
    return count


def expire(ctx: Ctx) -> int:
    """A step still marked as running after its deadline stopped without finishing."""
    count = no_answer(ctx)
    for piece in ctx.store.all("SELECT * FROM piece WHERE step_started IS NOT NULL AND "
                               "deadline_ms < ?", (ctx.now(),)):
        minutes = int(ctx.cfg().step_timeout_minutes)
        stall(ctx, piece, f"The {states.WORDS.get(piece['state'], piece['state'])} step did "
                          f"not finish within {minutes} minutes. Nothing more was done. Press "
                          "Retry to try it again, or drop the article.")
        count += 1
    return count


def stall(ctx: Ctx, piece: dict[str, Any], sentence: str) -> None:
    ctx.store.add_turn(piece["id"], piece["state"], sentence)
    ctx.store.move(piece["id"], "stalled", error=sentence, step_started=None,
                   deadline_ms=None, came_from=piece["state"])
    ctx.emit()


def retry(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    """The owner pressed Retry on a stuck article, or on one that stopped for sources."""
    piece = ctx.store.piece(piece_id)
    if piece is None:
        raise StepError("There is no such article.")
    if piece["state"] == "stopped_research":
        back = "searching"
    elif piece["state"] == "stalled":
        back = piece["came_from"] or "coverage_check"
    else:
        raise StepError("Only a stuck article, or one stopped for sources, can be retried. "
                        "Nothing was changed.")
    ctx.store.add_turn(piece_id, back, "You pressed Retry.", role="owner")
    return ctx.store.move(piece_id, back, error="", came_from="")


def step(ctx: Ctx, piece_id: int) -> str | None:
    """Carry one article one step. None when it had to wait (today's articles have
    started), so another may go instead."""
    piece = ctx.store.piece(piece_id)
    if piece is None or piece["state"] not in STEPS:
        return None
    timeout = int(ctx.cfg().step_timeout_minutes) * MINUTE_MS
    ctx.store.update("piece", piece_id, {"step_started": ctx.now(),
                                         "deadline_ms": ctx.now() + timeout})
    piece = ctx.store.piece(piece_id) or piece
    try:
        said = STEPS[piece["state"]](ctx, piece)
    except (StepError, CallError) as error:
        sentence = error.message if isinstance(error, CallError) else str(error)
        current = ctx.store.piece(piece_id) or piece
        stall(ctx, current, f"{sentence} Press Retry to try again, or drop the article.")
        return sentence
    finally:
        if ctx.store.piece(piece_id)["step_started"] is not None:
            ctx.store.update("piece", piece_id, {"step_started": None, "deadline_ms": None})
    ctx.emit()
    return said
