"""The cards Articles puts on the owner's Bench, and what it does with each decision.

- ``article``: the article with what the checks found. Approve (the Bench's own), Send
  back (needs a note), Decline (needs a reason, best started with one of the settings'
  reasons). Notes and reasons become lessons for later articles.
- ``coverage``: "86% like an earlier article", filed before anything is spent. Write
  anyway, or Drop.
- ``notice``: something the owner should know.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError, Decision

from articles import actions, checked, coverage, markup, render
from articles.context import Ctx

log = logging.getLogger("articles")

MAX_BODY = 8000

APPROVE = {"id": "approve", "label": "Approve", "style": "primary", "closes": True}
SEND_BACK = {"id": "send_back", "label": "Send back", "needs_reason": True, "closes": True}
DECLINE = {"id": "decline", "label": "Decline", "style": "danger", "needs_reason": True,
           "closes": True}
WRITE_ANYWAY = {"id": "write_anyway", "label": "Write anyway", "style": "primary",
                "closes": True}
DROP = {"id": "drop", "label": "Drop", "style": "danger", "closes": True}
GOT_IT = {"id": "ok", "label": "Got it", "style": "primary", "closes": True}


def withdraw(ctx: Ctx, card_id: str | None) -> None:
    if not card_id:
        return
    try:
        ctx.app.bench.withdraw(card_id, note="Replaced by a newer version.")
    except CallError as error:
        log.info("could not withdraw card %s: %s", card_id, error.message)


def article_body(ctx: Ctx, piece: dict[str, Any]) -> str:
    revision = ctx.store.revision(piece["id"]) or {}
    results = ctx.store.checks(revision["id"]) if revision else []
    sources = ctx.store.sources(piece["id"])
    words = markup.word_count(revision.get("body", ""))
    lines = [f"{words} words from {len(sources)} sources. Read and edit it under Articles."]
    raised = [r for r in results if r["level"] != "ok"]
    if raised:
        lines.append("")
        for r in raised:
            mark = "Question" if r["level"] == "ask" else "Warning"
            answer = f" (you said {r['answer']})" if r.get("answer") else ""
            lines.append(f"{mark}: {r['detail']}{answer}")
    lines += ["", checked.summary(results), "", render.plain_text(revision.get("body", ""))]
    reasons = ", ".join(str(r) for r in ctx.cfg().decline_reasons or [])
    if reasons:
        lines.insert(1, f"To decline, start your reason with one of: {reasons}.")
    return "\n".join(lines)[:MAX_BODY]


def file_article(ctx: Ctx, piece_id: int) -> str | None:
    """The article's card, replacing an earlier one for the same article."""
    store = ctx.store
    piece = store.piece(piece_id) or {}
    site = store.site(piece["site_id"]) or {}
    revision = store.revision(piece_id) or {}
    withdraw(ctx, piece.get("card_id"))
    results = store.checks(revision["id"]) if revision else []
    try:
        card_id = ctx.app.bench.file(
            "article", f"{site.get('name', 'Article')}: {revision.get('title', '')}"[:200],
            body=article_body(ctx, piece), subject=f"piece:{piece_id}",
            actions=[APPROVE, SEND_BACK, DECLINE], summary=checked.summary(results)[:300])
    except CallError as error:
        store.update("piece", piece_id, {"card_id": None, "error": "The article could not be "
                                         f"put on your Bench: {error.message} It is waiting "
                                         "under Articles."})
        return None
    store.update("piece", piece_id, {"card_id": card_id})
    return card_id


def refresh(ctx: Ctx, piece_id: int) -> None:
    """Say the article's new checks on the card that is already there."""
    piece = ctx.store.piece(piece_id) or {}
    if not piece.get("card_id") or piece.get("state") != "ready":
        return
    revision = ctx.store.revision(piece_id) or {}
    results = ctx.store.checks(revision["id"]) if revision else []
    try:
        ctx.app.bench.update(piece["card_id"], summary=checked.summary(results)[:300],
                             body=article_body(ctx, piece))
    except CallError as error:
        log.info("could not update card %s: %s", piece["card_id"], error.message)


def file_coverage(ctx: Ctx, piece_id: int) -> str | None:
    store = ctx.store
    piece = store.piece(piece_id) or {}
    site = store.site(piece["site_id"]) or {}
    said = coverage.describe(piece["coverage"], ctx.now())
    body = (f"{piece['topic']}\n\n{said} Nothing has been spent on it yet. Write anyway, or "
            "drop it.")
    if piece["coverage"].get("note"):
        body += f"\n\n{piece['coverage']['note']}"
    try:
        card_id = ctx.app.bench.file("coverage", f"{site.get('name', 'Articles')}: {said}"[:200],
                                     body=body, subject=f"piece:{piece_id}",
                                     actions=[WRITE_ANYWAY, DROP])
    except CallError as error:
        store.update("piece", piece_id, {"error": "The card could not be put on your Bench: "
                                         f"{error.message} Decide it under Articles."})
        return None
    store.update("piece", piece_id, {"card_id": card_id})
    return card_id


def notice(ctx: Ctx, title: str, body: str) -> str | None:
    try:
        return ctx.app.bench.file("notice", title[:200], body=body, actions=[GOT_IT])
    except CallError as error:
        log.warning("could not tell the owner %r: %s", title, error.message)
        return None


# --- decisions ------------------------------------------------------------------------------


def _piece_of(ctx: Ctx, decision: Decision) -> dict[str, Any] | None:
    subject = decision.subject or ""
    if not subject.startswith("piece:") or not subject[6:].isdigit():
        return None
    piece = ctx.store.piece(int(subject[6:]))
    if piece is None or piece.get("card_id") != decision.card_id:
        return None
    return piece


def on_article(ctx: Ctx, decision: Decision) -> str:
    piece = _piece_of(ctx, decision)
    if piece is None:
        return "That card is no longer the article's current one; nothing was done."
    words = (decision.reason or decision.note or "").strip()
    if decision.action == "approve":
        done = actions.approve(ctx, piece["id"])
    elif decision.action == "send_back":
        done = actions.send_back(ctx, piece["id"], words)
    elif decision.action == "decline":
        done = actions.decline(ctx, piece["id"], words)
    else:
        return f"Articles does not know the action {decision.action!r}; nothing was done."
    return done.get("said") or done["state"]


def on_coverage(ctx: Ctx, decision: Decision) -> str:
    piece = _piece_of(ctx, decision)
    if piece is None:
        return "That card is no longer the article's current one; nothing was done."
    if decision.action == "write_anyway":
        return actions.write_anyway(ctx, piece["id"])["state"]
    if decision.action == "drop":
        return actions.drop(ctx, piece["id"])["state"]
    return f"Articles does not know the action {decision.action!r}; nothing was done."
