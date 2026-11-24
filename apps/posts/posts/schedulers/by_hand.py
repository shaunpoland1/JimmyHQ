"""By hand: the queue says when to post, and the owner presses Posted once they have."""

from __future__ import annotations

from typing import Any

from posts.schedulers import Booking, Outcome, Seen


class ByHand:
    id = "by_hand"
    label = "By hand"
    sends_out = False
    can_edit = True  # the text lives here until the owner posts it

    def __init__(self, ctx: Any):
        self.ctx = ctx

    def needs(self, page: dict[str, Any]) -> str | None:
        return None

    def book(self, booking: Booking) -> Outcome:
        return Outcome("sent", [{"account": a["account"], "external_id": None}
                                for a in booking.accounts],
                       "On your list: post it yourself at its time, then press Posted.")

    def status(self, post_id: int, upstream: list[dict[str, Any]]) -> Seen:
        return Seen(True)  # nothing is held anywhere else, so nothing can change there

    def move(self, post_id: int, upstream: list[dict[str, Any]], slot_ms: int) -> Outcome:
        return Outcome("sent", upstream)

    def edit_text(self, post_id: int, upstream: list[dict[str, Any]], text: str) -> Outcome:
        return Outcome("sent", upstream)

    def cancel(self, post_id: int, upstream: list[dict[str, Any]]) -> Outcome:
        return Outcome("sent", upstream)

    def scheduled(self) -> list[dict[str, Any]]:
        return self.ctx.store.all("SELECT p.id AS post_id, p.slot_ms FROM post p WHERE "
                                  "p.scheduler='by_hand' AND p.state='booked' ORDER BY slot_ms")
