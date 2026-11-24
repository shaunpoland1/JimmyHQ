"""The cards Posts puts on the owner's Bench, and what it does with each decision.

- ``draft``: the post with its checks. Approve (the Bench's own), Send back (needs a
  note), Decline (needs a reason, which should start with one of the settings' reasons).
- ``overlap``: "82% like the post of 3 weeks ago", filed before anything is spent on the
  piece. Draft anyway, or Drop.
- ``notice``: something the owner should know, such as a setting that was not taken.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError, Decision

from posts import actions, checked, similar
from posts.booking import booked_text
from posts.context import Ctx

log = logging.getLogger("posts")

HOUR_MS = 3_600_000
MAX_BODY = 8000

APPROVE = {"id": "approve", "label": "Approve", "style": "primary", "closes": True}
SEND_BACK = {"id": "send_back", "label": "Send back", "needs_reason": True, "closes": True}
DECLINE = {"id": "decline", "label": "Decline", "style": "danger", "needs_reason": True,
           "closes": True}
DRAFT_ANYWAY = {"id": "draft_anyway", "label": "Draft anyway", "style": "primary",
                "closes": True}
DROP = {"id": "drop", "label": "Drop", "style": "danger", "closes": True}
GOT_IT = {"id": "ok", "label": "Got it", "style": "primary", "closes": True}


def _withdraw(ctx: Ctx, card_id: str | None) -> None:
    if not card_id:
        return
    try:
        ctx.app.bench.withdraw(card_id, note="Replaced by a newer version.")
    except CallError as error:
        log.info("could not withdraw card %s: %s", card_id, error.message)


def draft_body(ctx: Ctx, post: dict[str, Any], piece: dict[str, Any],
               results: list[dict[str, Any]]) -> str:
    cfg = ctx.cfg()
    lines = [booked_text(post, piece, str(cfg.sponsor_tag))]
    if post["links"]:
        lines.append("Source: " + ", ".join(post["links"]))
    elif piece["source_url"]:
        lines.append(f"Source: {piece['source_url']}")
    raised = [r for r in results if r["level"] != "ok"]
    if raised:
        lines.append("")
        for r in raised:
            mark = "Question" if r["level"] == "ask" else "Warning"
            answer = f" (you said {r['answer']})" if r.get("answer") else ""
            lines.append(f"{mark}: {r['detail']}{answer}")
    lines.append("")
    lines.append(checked.summary(results))
    reasons = ", ".join(str(r) for r in cfg.decline_reasons or [])
    if reasons:
        lines.append(f"To decline, start your reason with one of: {reasons}.")
    return "\n".join(lines)[:MAX_BODY]


def file_draft(ctx: Ctx, post_id: int) -> str | None:
    """File the draft card, replacing an earlier one for the same post."""
    store = ctx.store
    post = store.post(post_id) or {}
    piece = store.piece(post["piece_id"]) or {}
    page = store.page(post["page_id"]) or {}
    results = store.checks(post_id)
    _withdraw(ctx, post.get("card_id"))
    title = post["title"] or post["body"].split("\n", 1)[0]
    try:
        card_id = ctx.app.bench.file(
            "draft", f"{page.get('name', 'Post')}: {title}"[:200],
            body=draft_body(ctx, post, piece, results), subject=f"post:{post_id}",
            actions=[APPROVE, SEND_BACK, DECLINE], summary=checked.summary(results)[:300])
    except CallError as error:
        store.update("post", post_id, {"card_id": None, "note": "The draft could not be put on "
                                       f"your Bench: {error.message} It is waiting under "
                                       "Drafts."})
        return None
    store.update("post", post_id, {"card_id": card_id, "card_filed": ctx.now(), "nudged": 0})
    return card_id


def refresh(ctx: Ctx, post_id: int) -> None:
    """Say the new count of open questions on the card that is already there."""
    post = ctx.store.post(post_id) or {}
    if not post.get("card_id") or post.get("state") != "draft":
        return
    results = ctx.store.checks(post_id)
    piece = ctx.store.piece(post["piece_id"]) or {}
    try:
        ctx.app.bench.update(post["card_id"], summary=checked.summary(results)[:300],
                             body=draft_body(ctx, post, piece, results))
    except CallError as error:
        log.info("could not update card %s: %s", post["card_id"], error.message)


def file_overlap(ctx: Ctx, piece_id: int) -> str | None:
    store = ctx.store
    piece = store.piece(piece_id) or {}
    page = store.page(piece["page_id"]) or {}
    overlap = similar.Overlap(**{**piece["overlap"], "best": None, "matches": []})
    best = piece["overlap"].get("best")
    if best:
        overlap = similar.Overlap(**{**piece["overlap"], "best": similar.Match(**best),
                                     "matches": []})
    said = similar.describe(overlap, ctx.now())
    what = piece["source_url"] or piece["topic"]
    body = (f"{what}\n\n{said} Nothing has been spent on drafting it. Draft anyway, or "
            "drop it.")
    if overlap.note:
        body += f"\n\n{overlap.note}"
    try:
        card_id = ctx.app.bench.file("overlap", f"{page.get('name', 'Posts')}: {said}"[:200],
                                     body=body, subject=f"piece:{piece_id}",
                                     actions=[DRAFT_ANYWAY, DROP])
    except CallError as error:
        store.update("piece", piece_id, {"error": "The overlap card could not be put on your "
                                         f"Bench: {error.message} Decide it under Pieces."})
        return None
    store.update("piece", piece_id, {"card_id": card_id})
    return card_id


def notice(ctx: Ctx, title: str, body: str) -> str | None:
    try:
        return ctx.app.bench.file("notice", title[:200], body=body, actions=[GOT_IT])
    except CallError as error:
        log.warning("could not tell the owner %r: %s", title, error.message)
        return None


def nudge(ctx: Ctx) -> int:
    """A draft card waiting longer than ``nudge_hours`` gets one reminder."""
    since = ctx.now() - int(ctx.cfg().nudge_hours) * HOUR_MS
    count = 0
    for post in ctx.store.all("SELECT * FROM post WHERE state='draft' AND card_id IS NOT NULL "
                              "AND nudged=0 AND card_filed < ?", (since,)):
        try:
            ctx.app.bench.update(post["card_id"], note="This draft is still waiting for you.")
        except CallError as error:
            log.info("could not remind about card %s: %s", post["card_id"], error.message)
        ctx.store.update("post", post["id"], {"nudged": 1})
        count += 1
    return count


# --- decisions ------------------------------------------------------------------------------


def _id_of(subject: str | None, prefix: str) -> int | None:
    if subject and subject.startswith(prefix) and subject[len(prefix):].isdigit():
        return int(subject[len(prefix):])
    return None


def on_draft(ctx: Ctx, decision: Decision) -> str:
    post_id = _id_of(decision.subject, "post:")
    post = ctx.store.post(post_id) if post_id else None
    if post is None or post.get("card_id") != decision.card_id:
        return "That card is no longer the post's current one; nothing was done."
    words = (decision.reason or decision.note or "").strip()
    if decision.action == "approve":
        done = actions.approve(ctx, post_id)
    elif decision.action == "send_back":
        done = actions.send_back(ctx, post_id, words)
    elif decision.action == "decline":
        done = actions.decline(ctx, post_id, words)
    else:
        return f"Posts does not know the action {decision.action!r}; nothing was done."
    return done.get("note") or done["state"]


def on_overlap(ctx: Ctx, decision: Decision) -> str:
    piece_id = _id_of(decision.subject, "piece:")
    piece = ctx.store.piece(piece_id) if piece_id else None
    if piece is None or piece.get("card_id") != decision.card_id:
        return "That card is no longer the piece's current one; nothing was done."
    if decision.action == "draft_anyway":
        return actions.draft_anyway(ctx, piece_id)["state"]
    if decision.action == "drop":
        return actions.drop(ctx, piece_id)["state"]
    return f"Posts does not know the action {decision.action!r}; nothing was done."
