"""What the owner decides, from a card or from the screen: one function for each, so both
ways do exactly the same.

Approving with questions open is allowed (the card says how many). A near-duplicate is
never withheld after approval: the warning rides along with the post. Send-back notes
and decline reasons become lessons for the page's later drafts.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError

from posts import booking, cards, checked, lessons, pages, states
from posts.context import Ctx

log = logging.getLogger("posts")


class ActionError(ValueError):
    """The owner's action could not be carried out. The message says what to do."""


def _post(ctx: Ctx, post_id: int, *, state: str | tuple[str, ...]) -> dict[str, Any]:
    post = ctx.store.post(post_id)
    wanted = (state,) if isinstance(state, str) else state
    if post is None:
        raise ActionError("There is no such post. Nothing was changed.")
    if post["state"] not in wanted:
        raise ActionError(f"This post is {states.WORDS[post['state']]} now, so that does "
                          "not apply to it. Nothing was changed.")
    return post


def _decided(ctx: Ctx, post_id: int, decision: str) -> None:
    ctx.tape("post.decided", {"post": post_id, "decision": decision}, subject=f"post:{post_id}")


def _may_book_on_approval(ctx: Ctx, sched: Any) -> bool:
    """A scheduler that sends nothing out (Practice, By hand) is booked at approval. Any
    other only when the owner lets Posts book alone on the ladder."""
    if not sched.sends_out:
        return True
    try:
        return ctx.app.ladder.check("posts", "book", "publish") == "alone"
    except CallError:
        return False


def approve(ctx: Ctx, post_id: int) -> dict[str, Any]:
    post = _post(ctx, post_id, state="draft")
    store = ctx.store
    piece = store.piece(post["piece_id"]) or {}
    if piece.get("state") != "ready":
        raise ActionError("This draft is still being worked on. Approve it once it is on your "
                          "Bench. Nothing was changed.")
    page = store.page(post["page_id"]) or {}
    slot = booking.propose_slot(ctx, page, skip_post=post_id)
    note = "" if slot else ("No free posting time was found in the next 90 days. Choose a "
                            "time, then book it.")
    post = store.move_post(post_id, "queued", slot_ms=slot, note=note, card_id=None)
    store.move_piece(post["piece_id"], "closed")
    _decided(ctx, post_id, "approve")
    try:
        sched = booking.scheduler_for(ctx, page=page)
    except booking.BookingError as error:
        return store.move_post(post_id, "queued", note=str(error))
    if slot and _may_book_on_approval(ctx, sched):
        try:
            post = booking.book(ctx, post_id)
        except booking.BookingError as error:
            post = store.move_post(post_id, "queued", note=str(error))
    ctx.emit("posts.changed")
    return post


def send_back(ctx: Ctx, post_id: int, note: str) -> dict[str, Any]:
    note = (note or "").strip()
    if not note:
        raise ActionError("Say what to change when you send a draft back. Nothing was changed.")
    post = _post(ctx, post_id, state="draft")
    store = ctx.store
    pages.add_lesson(store, page_id=post["page_id"], post_id=post_id, kind="send_back",
                     note=note)
    store.move_post(post_id, "revising", card_id=None)
    store.move_piece(post["piece_id"], "revising", owner_note=note[:2000])
    store.add_turn(post["piece_id"], "ready", f"You sent it back: {note[:300]}", role="owner")
    _decided(ctx, post_id, "send_back")
    ctx.emit("posts.changed")
    return store.post(post_id) or {}


def reason_from_list(given: str, reasons: list[str]) -> str:
    """The settings' reason the owner's words start with, else "Other" (or the last)."""
    low = given.strip().lower()
    for reason in reasons:
        if reason and low.startswith(str(reason).lower()):
            return str(reason)
    listed = [str(r) for r in reasons if r]
    return next((r for r in listed if r.lower() == "other"), listed[-1] if listed else "Other")


def decline(ctx: Ctx, post_id: int, reason: str) -> dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise ActionError("Give a reason when you decline a draft. Nothing was changed.")
    post = _post(ctx, post_id, state="draft")
    store = ctx.store
    listed = reason_from_list(reason, list(ctx.cfg().decline_reasons or []))
    pages.add_lesson(store, page_id=post["page_id"], post_id=post_id, kind="decline",
                     reason=listed, note=reason)
    done = store.move_post(post_id, "declined", card_id=None, note=f"Declined: {listed}.")
    piece = store.piece(post["piece_id"]) or {}
    store.move_piece(post["piece_id"], "closed" if piece.get("state") == "ready" else "dropped")
    _decided(ctx, post_id, "decline")
    ctx.emit("posts.changed")
    return done


def draft_anyway(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    held = ctx.store.piece(piece_id) or {}
    nxt = "captioning" if held.get("way") == "pictures" else "writing"
    piece = ctx.store.move_piece(piece_id, nxt, card_id=None)
    ctx.store.add_turn(piece_id, "held_overlap", "You chose to draft it anyway.", role="owner")
    post = ctx.store.post_of(piece_id)
    if post:
        _decided(ctx, post["id"], "draft_anyway")
    ctx.emit("posts.changed")
    return piece


def drop(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    piece = ctx.store.piece(piece_id)
    if piece is None:
        raise ActionError("There is no such piece. Nothing was changed.")
    post = ctx.store.post_of(piece_id)
    if post and post["state"] in ("draft", "revising"):
        cards._withdraw(ctx, post["card_id"])  # noqa: SLF001
        ctx.store.move_post(post["id"], "declined", card_id=None, note="Dropped.")
    if piece.get("card_id") and piece["state"] != "held_overlap":
        cards._withdraw(ctx, piece["card_id"])  # noqa: SLF001
    done = ctx.store.move_piece(piece_id, "dropped", card_id=None)
    ctx.store.add_turn(piece_id, piece["state"], "Dropped.", role="owner")
    from posts import work  # noqa: PLC0415 - work builds on actions

    work.settle(ctx, piece_id)  # any work an agent still holds for it is withdrawn
    ctx.emit("posts.changed")
    return done


def approval(ctx: Ctx, post_id: int) -> dict[str, Any]:
    """What approving this draft would do, before it is done: the time it would get and
    whether it is booked at once (``books``) or waits in the queue for the owner to book
    it. Changes nothing."""
    post = _post(ctx, post_id, state="draft")
    page = ctx.store.page(post["page_id"]) or {}
    slot = booking.propose_slot(ctx, page, skip_post=post_id)
    try:
        sched = booking.scheduler_for(ctx, page=page)
    except booking.BookingError as error:
        return {"slot_ms": slot, "books": False, "scheduler": "", "note": str(error)}
    return {"slot_ms": slot, "books": bool(slot) and _may_book_on_approval(ctx, sched),
            "scheduler": sched.label, "note": ""}


def _stop_research(ctx: Ctx, page_id: int) -> None:
    """The researcher is no longer asked for this page: open asks are withdrawn, and a
    card about a late one with them."""
    from posts import work  # noqa: PLC0415 - work builds on actions

    for row in ctx.store.all("SELECT * FROM agent_work WHERE step='research' AND page_id=? "
                             "AND piece_id IS NULL AND state='open'", (page_id,)):
        if row["answered"] is None:
            work._withdraw(ctx, row["work_id"])  # noqa: SLF001
        cards._withdraw(ctx, row["card_id"] or None)  # noqa: SLF001
        ctx.store.update("agent_work", row["id"], {"state": "withdrawn", "card_id": None,
                                                   "ended": ctx.now()})


def pause_page(ctx: Ctx, page_id: Any, paused: Any) -> dict[str, Any]:
    """Pause or resume a page. Pausing also takes back what the researcher was asked for
    it; what is drafted, queued or booked for it carries on."""
    page = pages.set_paused(ctx.store, page_id, paused)
    if page.get("paused"):
        _stop_research(ctx, page["id"])
    ctx.emit("posts.changed")
    return page


def remove_page(ctx: Ctx, page_id: Any) -> dict[str, Any]:
    """Remove a page with nothing queued or booked. Its open pieces are dropped (a draft
    of one is declined), its own rules and lessons go with it, and the posts that went
    out or were dropped stay in their lists."""
    found = pages.removal(ctx.store, page_id)
    if found["refused"]:
        raise ActionError(found["refused"])
    store, page = ctx.store, found["id"]
    _stop_research(ctx, page)
    for piece in store.all("SELECT id FROM piece WHERE page_id=? AND state NOT IN "
                           "('closed', 'dropped')", (page,)):
        drop(ctx, piece["id"])
    for table in ("rule", "lesson", "edit"):
        store.conn.execute(f"DELETE FROM {table} WHERE page_id=?", (page,))  # noqa: S608
    store.conn.execute("DELETE FROM page WHERE id=?", (page,))
    store.conn.commit()
    ctx.emit("posts.changed")
    return {**found, "removed": True}


def edit(ctx: Ctx, post_id: int, *, title: str | None, body: str) -> dict[str, Any]:
    """The owner's own text. The checks run again; a draft's card is filed again; a
    booked post's change goes to the scheduler, or the post says it did not."""
    body = (body or "").replace("\r\n", "\n").strip()
    if not body:
        raise ActionError("A post needs some text. Nothing was changed.")
    post = _post(ctx, post_id, state=("draft", "queued", "booked"))
    store = ctx.store
    lessons.record_edit(store, post, body[:5000])  # what they changed teaches the writer
    changes = {"body": body[:5000], "revision": post["revision"] + 1}
    if title is not None:
        changes["title"] = title.strip()[:200]
    post = store.move_post(post_id, post["state"], **changes)
    checked.run(ctx, post_id)
    store.add_turn(post["piece_id"], "edit", "You edited the text; the checks ran again.",
                   role="owner")
    if post["state"] == "draft":
        cards.file_draft(ctx, post_id)
    elif post["state"] == "booked":
        post = booking.push_text(ctx, post_id)
    ctx.emit("posts.changed")
    return store.post(post_id) or post


def answer(ctx: Ctx, check_id: int, given: str) -> dict[str, Any]:
    row = checked.answer(ctx, check_id, given)
    cards.refresh(ctx, row["post_id"])
    ctx.emit("posts.changed")
    return row
