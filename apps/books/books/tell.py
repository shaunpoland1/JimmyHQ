"""Telling the owner on the Bench that a bill is coming due.

A card says the bill's name and its due date, and nothing else: never an amount, an
account, a payee or a category. One card for one due date; posting or skipping the bill
takes its card away. ``tell_bills`` switches it off (and takes open cards away);
``tell_days`` says how many days before the due date the card comes. A bill that pays
itself (``auto``, with a sure amount) needs no card while it is being posted by Books; one
that has been stopped, or whose amount is only about right, raises its card like any bill.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from jimmyhq_app import CallError, Decision

from books import reminders
from books.context import Ctx

log = logging.getLogger("books")

GOT_IT = {"id": "ok", "label": "Got it", "style": "primary", "closes": True}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def when_words(due: date, today: date) -> str:
    """"is due today", "is due tomorrow", "is due on 3 December", "was due on 1 May"."""
    gap = (due - today).days
    if gap == 0:
        return "is due today"
    if gap == 1:
        return "is due tomorrow"
    words = f"{due.day} {MONTHS[due.month - 1]}"
    if due.year != today.year:
        words += f" {due.year}"
    return f"was due on {words}" if gap < 0 else f"is due on {words}"


def card_words(name: str, due: date, today: date) -> str:
    """Rule ``a_bench_card_carries_no_amount``: the card's title, the bill's name and its due
    date. A private app's card is a title only (no body); the Bench's "Open Books" button
    says where to post it or skip it."""
    return f"{name} {when_words(due, today)}."[:200]


def withdraw(ctx: Ctx, card_id: str | None, note: str = "") -> None:
    """Take a card away. ``note`` is for the log of why: a private app's withdrawal carries
    no note, so it is not sent."""
    if not card_id:
        return
    try:
        ctx.app.bench.withdraw(card_id)
    except CallError as error:
        log.info("could not withdraw card %s: %s", card_id, error.message)


def check(ctx: Ctx) -> int:
    """File a card for each bill within ``tell_days`` of its due date that has none for
    that date. Returns how many were filed."""
    store, cfg = ctx.store, ctx.cfg()
    today = ctx.today()
    filed = 0
    bills = store.all("SELECT * FROM reminder WHERE kind='bill'")
    if not bool(cfg.tell_bills):
        for rem in bills:
            if rem["card_id"]:
                withdraw(ctx, rem["card_id"], "You switched these cards off.")
                with store.tx():
                    store.update("reminder", rem["id"], card_id=None)
        return 0
    by_itself = bool(cfg.auto_post)
    for rem in bills:
        if reminders.pays_itself(rem, by_itself):
            if rem["card_id"]:
                withdraw(ctx, rem["card_id"], "This one is posted by itself now.")
                with store.tx():
                    store.update("reminder", rem["id"], card_id=None)
            continue
        if rem["next_due"] is None:
            if rem["card_id"]:
                withdraw(ctx, rem["card_id"], "Nothing is due any more.")
                with store.tx():
                    store.update("reminder", rem["id"], card_id=None)
            continue
        due = date.fromisoformat(rem["next_due"])
        if (due - today).days > int(cfg.tell_days) or rem["told_due"] == rem["next_due"]:
            continue
        withdraw(ctx, rem["card_id"], "Replaced by the next due date.")
        title = card_words(rem["name"], due, today)
        try:
            card_id = ctx.app.bench.file("due", title, actions=[GOT_IT],
                                         subject=f"reminder:{rem['id']}")
        except CallError as error:
            log.warning("could not file a card for reminder %s: %s", rem["id"], error.message)
            continue
        with store.tx():
            store.update("reminder", rem["id"], card_id=card_id, told_due=rem["next_due"])
        filed += 1
    return filed


def settled(ctx: Ctx, result: dict[str, Any], note: str) -> dict[str, Any]:
    """After a post, a skip or a deletion: take away the card that was for it."""
    card_id = result.pop("card_id", None)
    if card_id:
        withdraw(ctx, card_id, note)
        with ctx.store.tx():
            ctx.store.run("UPDATE reminder SET card_id=NULL WHERE card_id=?", (card_id,))
    return result


def on_decision(ctx: Ctx, decision: Decision) -> str:
    """The owner closed a card: that reminder has no card on the Bench now."""
    with ctx.store.tx():
        ctx.store.run("UPDATE reminder SET card_id=NULL WHERE card_id=?", (decision.card_id,))
    return "Seen."
