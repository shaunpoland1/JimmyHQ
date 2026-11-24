"""What Posts writes on the owner's record: ids and short words only, except ``posted``.

``posted`` is Posts' feed ``posted``: another app the owner lets read it (Reader, with
``feed:posts.posted``) learns what went out, to say an article is close to something
already posted. So from v2 it carries a post's title, its source's address and the
opening of what went out: text that is public already, and that the owner can have
erased like any other line of the record. Never the owner's notes or a draft.
"""

from __future__ import annotations

from typing import Literal

from jimmyhq_app import App, Payload


class PieceStarted(Payload):
    piece: int
    way: Literal["link", "topic", "pictures", "article"]


class PostDecided(Payload):
    post: int
    decision: Literal["approve", "send_back", "decline", "draft_anyway", "drop"]


class BookingAttempted(Payload):
    post: int
    attempt: int
    action: Literal["book", "move", "edit", "cancel"]
    scheduler: str


class BookingResult(Payload):
    post: int
    attempt: int
    outcome: Literal["sent", "refused", "unsure", "cleared"]


class PostWentOut(Payload):
    post: int


class PostFailed(Payload):
    post: int


class PostedV1(Payload):
    post: int
    page: int


class Posted(Payload):
    post: int
    page: int
    title: str = ""
    source_url: str | None = None
    opening: str = ""  # the start of what went out, at most OPENING_CHARS


OPENING_CHARS = 600
POSTED_EXAMPLE = {"post": 1, "page": 1, "title": "Soil that holds water",
                  "source_url": "https://news.example.com/2027/soil",
                  "opening": "A field trial found soil kept water for longer."}


def posted(post: dict, piece: dict | None) -> dict:
    """The ``posted`` item for one post that went out."""
    return {"post": post["id"], "page": post["page_id"],
            "title": str(post.get("title") or "")[:300],
            "source_url": (piece or {}).get("source_url") or None,
            "opening": str(post.get("body") or "")[:OPENING_CHARS]}


def declare(app: App) -> None:
    app.kind("piece.started", example={"piece": 1, "way": "link"})(PieceStarted)
    app.kind("post.decided", example={"post": 1, "decision": "approve"})(PostDecided)
    app.kind("booking.attempted", example={"post": 1, "attempt": 1, "action": "book",
                                           "scheduler": "practice"})(BookingAttempted)
    app.kind("booking.result", example={"post": 1, "attempt": 1,
                                        "outcome": "sent"})(BookingResult)
    app.kind("post.went_out", example={"post": 1})(PostWentOut)
    app.kind("post.failed", example={"post": 1})(PostFailed)
    app.kind("posted", example={"post": 1, "page": 1})(PostedV1)
    app.kind("posted", v=2, example=POSTED_EXAMPLE)(Posted)

    @app.upcaster("posted", 1)
    def posted_v2(payload: dict) -> dict:
        return {**payload, "title": "", "source_url": None, "opening": ""}
