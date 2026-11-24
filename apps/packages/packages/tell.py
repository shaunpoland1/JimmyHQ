"""Telling the owner: one card on the Bench for one change, never repeated.

The setting ``tell_when`` says which changes count. A change that counts is first written
on the package (``tell_status``) in the same step that changes its status, then filed;
a card that could not be filed stays waiting and is tried again at the next look, so a
change is told once whether or not the Bench was ready. A card says the package's name
and its new status in words, and nothing else: never the tracking number, the carrier's
words or any address. A newer card for a package takes the place of its older one.
"""

from __future__ import annotations

import logging

from jimmyhq_app import CallError, Decision

from packages.context import Ctx
from packages.status import SENTENCES

log = logging.getLogger("packages")

GOT_IT = {"id": "ok", "label": "Got it", "style": "primary", "closes": True}


def should_tell(mode: str, old: str, new: str) -> bool:
    """Rule ``tell_when``: whether a change from ``old`` to ``new`` is worth a card."""
    if old == new or mode == "off":
        return False
    if mode == "delivered":
        return new == "delivered"
    if mode == "delivered_or_problem":
        return new in ("delivered", "problem")
    return mode == "any_change"


def lane_of(status: str) -> str:
    """Rule ``a_delivery_is_news_not_a_decision``: a delivered package is filed as done for
    the owner, with "Got it", the way the Bench files what was done on their behalf. Any
    other news (a problem, a step along the way) may want a look, so it stays among the
    cards that need them."""
    return "done" if status == "delivered" else "needs"


def card_words(label: str, status: str) -> str:
    """The card's title for a package called ``label``: its name and its new status. A
    private app's card is a title only (no body); the Bench's "Open Packages" button says
    where to see its last event."""
    return f"{label} {SENTENCES.get(status, 'has news')}."[:200]


def package_of(subject: str | None) -> int | None:
    if subject and subject.startswith("package:") and subject[8:].isdigit():
        return int(subject[8:])
    return None


def withdraw(ctx: Ctx, card_id: str | None, note: str = "") -> None:
    """Take a card away. A private app's withdrawal carries no note, so ``note`` (why, for
    the reader of this code) is not sent."""
    if not card_id:
        return
    try:
        ctx.app.bench.withdraw(card_id)
    except CallError as error:
        log.info("could not withdraw card %s: %s", card_id, error.message)


def deliver(ctx: Ctx, package_id: int) -> bool:
    """File the card a package is waiting to tell. True when one went on the Bench."""
    pkg = ctx.store.package(package_id)
    if pkg is None or not pkg["tell_status"]:
        return False
    if str(ctx.cfg().tell_when) == "off":
        ctx.store.set(package_id, tell_status="")
        return False
    title = card_words(pkg["label"], pkg["tell_status"])
    withdraw(ctx, pkg["card_id"], "Replaced by newer news about the same package.")
    try:
        card_id = ctx.app.bench.file("update", title, actions=[GOT_IT],
                                     subject=f"package:{package_id}",
                                     lane=lane_of(pkg["tell_status"]))
    except CallError as error:
        log.warning("could not file a card for package %s: %s", package_id, error.message)
        return False
    ctx.store.set(package_id, tell_status="", card_id=card_id)
    return True


def retry(ctx: Ctx) -> int:
    """Try again every card that could not be filed. Returns how many went on the Bench."""
    waiting = ctx.store.all("SELECT id FROM package WHERE tell_status != ''")
    return sum(deliver(ctx, row["id"]) for row in waiting)


def on_decision(ctx: Ctx, decision: Decision) -> str:
    """The owner closed a card: the package no longer has one on the Bench."""
    pkg = ctx.store.package(package_of(decision.subject))
    if pkg is not None and pkg["card_id"] == decision.card_id:
        ctx.store.set(pkg["id"], card_id=None)
    return "Seen."
