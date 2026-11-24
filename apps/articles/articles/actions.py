"""What the owner decides: approve, send back, decline, drop, write anyway, edit.

Each is reachable from the Bench card and from the page, and goes through the same
function here and the same state table. A send-back note and a decline reason are
required, and each becomes a lesson the writer is given on later articles of the site.
"""

from __future__ import annotations

from typing import Any

from articles import checked, publish, render, review, states, writing
from articles.context import Ctx


class ActionError(ValueError):
    """Nothing was changed. The message says why and what to do instead."""


def _piece(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    piece = ctx.store.piece(piece_id)
    if piece is None:
        raise ActionError("There is no such article. Nothing was changed.")
    return piece


def _need_ready(piece: dict[str, Any], what: str) -> None:
    if piece["state"] != "ready":
        raise ActionError(f"This article is {states.WORDS.get(piece['state'], piece['state'])}"
                          f", so you cannot {what} it now. Nothing was changed.")


def _lesson(ctx: Ctx, piece: dict[str, Any], kind: str, *, reason: str = "",
            note: str = "") -> None:
    ctx.store.insert("lesson", {"site_id": piece["site_id"], "piece_id": piece["id"],
                                "kind": kind, "reason": reason[:200], "note": note[:2000],
                                "created": ctx.now()})


def _decided(ctx: Ctx, piece_id: int, decision: str) -> None:
    ctx.tape("piece.decided", {"piece": piece_id, "decision": decision},
             subject=f"piece:{piece_id}")


def _withdraw_card(ctx: Ctx, piece: dict[str, Any]) -> None:
    from articles import cards  # noqa: PLC0415 - cards hands decisions back to here

    cards.withdraw(ctx, piece.get("card_id"))
    ctx.store.update("piece", piece["id"], {"card_id": None})


def approve(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    piece = _piece(ctx, piece_id)
    _need_ready(piece, "approve")
    ctx.store.move(piece_id, "approved", card_id=None)
    _decided(ctx, piece_id, "approve")
    return publish.after_approval(ctx, piece_id)


def send_back(ctx: Ctx, piece_id: int, note: str) -> dict[str, Any]:
    piece = _piece(ctx, piece_id)
    _need_ready(piece, "send back")
    note = note.strip()
    if not note:
        raise ActionError("You need to say what to change when you send an article back. "
                          "Nothing was changed. Add a note, then send it back.")
    _lesson(ctx, piece, "send_back", note=note)
    moved = ctx.store.move(piece_id, "revising", owner_note=note, card_id=None,
                           round=piece["round"] + 1)
    ctx.store.add_turn(piece_id, "ready", f"You sent it back: {note}"[:600], role="owner")
    _decided(ctx, piece_id, "send_back")
    return {**moved, "said": "Sent back; a new version is written from your note."}


def decline(ctx: Ctx, piece_id: int, reason: str) -> dict[str, Any]:
    piece = _piece(ctx, piece_id)
    _need_ready(piece, "decline")
    reason = reason.strip()
    if not reason:
        raise ActionError("You need to give a reason to decline an article. Nothing was "
                          "changed. Start it with one of the reasons under Articles' "
                          "settings, then decline.")
    listed = [str(r) for r in ctx.cfg().decline_reasons or []]
    named = next((r for r in listed if reason.lower().startswith(r.lower())), "")
    _lesson(ctx, piece, "decline", reason=named or "Other", note=reason)
    moved = ctx.store.move(piece_id, "declined", card_id=None)
    ctx.store.add_turn(piece_id, "ready", f"You declined it: {reason}"[:600], role="owner")
    _decided(ctx, piece_id, "decline")
    return {**moved, "said": "Declined; your reason is a lesson for later articles."}


def drop(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    piece = _piece(ctx, piece_id)
    if not states.allowed(piece["state"], "dropped"):
        raise ActionError(f"This article is {states.WORDS.get(piece['state'], piece['state'])}"
                          ", so it cannot be dropped. Nothing was changed.")
    _withdraw_card(ctx, piece)
    moved = ctx.store.move(piece_id, "dropped", step_started=None, deadline_ms=None)
    ctx.store.add_turn(piece_id, piece["state"], "You dropped it.", role="owner")
    _decided(ctx, piece_id, "drop")
    return moved


def write_anyway(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    piece = _piece(ctx, piece_id)
    if piece["state"] != "held_coverage":
        raise ActionError("Only an article held as close to an earlier one can be written "
                          "anyway. Nothing was changed.")
    _withdraw_card(ctx, piece)
    moved = ctx.store.move(piece_id, "searching")
    ctx.store.add_turn(piece_id, "held_coverage", "You chose to write it anyway.",
                       role="owner")
    _decided(ctx, piece_id, "write_anyway")
    return moved


def edit(ctx: Ctx, piece_id: int, *, title: Any, body: Any) -> dict[str, Any]:
    """The owner's own edit of a waiting article: a new version, sanitised and checked
    like any other. The review's findings that still quote the text stay with it."""
    from articles import cards  # noqa: PLC0415 - cards hands decisions back to here

    piece = _piece(ctx, piece_id)
    _need_ready(piece, "edit")
    before = ctx.store.revision(piece_id)
    if before is None:
        raise ActionError("This article has no text to edit. Nothing was changed.")
    if not str(body or "").strip():
        raise ActionError("You left the article empty. Nothing was changed. Write its text, "
                          "or drop it.")
    new = checked.new_revision(ctx, piece, title=title or before["title"], body=body,
                               kind="edit")
    sources = ctx.store.sources(piece_id)
    earlier = [{**f, "cites": [{"source": c[0], "sentence": c[1]} for c in f["cites"]]}
               for f in before["review"] or []]
    findings, aside = review.read_review({"findings": earlier}, new, sources,
                                         int(ctx.cfg().review_findings_max))
    said = writing.changes(render.plain_text(before["body"]), render.plain_text(new["body"]))
    ctx.store.update("revision", new["id"], {
        "review": findings if before["review"] is not None else None,
        "review_aside": aside + int(before["review_aside"]), "changes": said})
    results = checked.run(ctx, new["id"])
    if said:
        _lesson(ctx, piece, "edit", note="; ".join(said))
    ctx.store.add_turn(piece_id, "ready", f"You edited it. Code checks: "
                       f"{checked.summary(results)}", role="owner")
    cards.refresh(ctx, piece_id)
    return {"revision": ctx.store.revision(piece_id), "checks": results}
