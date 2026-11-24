"""Practice: records what it would send, and books nothing. The default."""

from __future__ import annotations

from typing import Any

from posts.schedulers import Booking, Outcome, Seen


class Practice:
    id = "practice"
    label = "Practice"
    sends_out = False
    can_edit = True
    ends_by_itself = True  # no Posted step for the owner: reconcile ends it when its time passes

    def __init__(self, ctx: Any):
        self.ctx = ctx

    def _log(self, post_id: int, action: str, would_send: dict[str, Any]) -> None:
        self.ctx.store.insert("practice_log", {"post_id": post_id, "action": action,
                                               "would_send": would_send,
                                               "created": self.ctx.now()})

    def needs(self, page: dict[str, Any]) -> str | None:
        return None

    def book(self, booking: Booking) -> Outcome:
        self._log(booking.post_id, "book", {"text": booking.text, "slot_ms": booking.slot_ms,
                                            "accounts": [a["label"] for a in booking.accounts],
                                            "pictures": [{"name": p["name"], "ref": p["ref"]}
                                                         for p in booking.pictures]})
        return Outcome("sent", [{"account": a["account"],
                                 "external_id": f"practice-{booking.post_id}-{n}"}
                                for n, a in enumerate(booking.accounts, 1)],
                       "Practice: nothing was sent.")

    def status(self, post_id: int, upstream: list[dict[str, Any]]) -> Seen:
        return Seen(True)  # nothing is held anywhere else, so nothing can change there

    def move(self, post_id: int, upstream: list[dict[str, Any]], slot_ms: int) -> Outcome:
        self._log(post_id, "move", {"slot_ms": slot_ms})
        return Outcome("sent", upstream, "Practice: nothing was sent.")

    def edit_text(self, post_id: int, upstream: list[dict[str, Any]], text: str) -> Outcome:
        self._log(post_id, "edit", {"text": text})
        return Outcome("sent", upstream, "Practice: nothing was sent.")

    def cancel(self, post_id: int, upstream: list[dict[str, Any]]) -> Outcome:
        self._log(post_id, "cancel", {})
        return Outcome("sent", upstream, "Practice: nothing was sent.")

    def scheduled(self) -> list[dict[str, Any]]:
        return self.ctx.store.all("SELECT p.id AS post_id, p.slot_ms FROM post p WHERE "
                                  "p.scheduler='practice' AND p.state='booked' ORDER BY slot_ms")
