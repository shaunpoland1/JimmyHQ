"""What Articles writes on the owner's record: ids and short words only.

No topic, no article text, no source and nothing from readers' replies is ever on the
record. Articles' own tables hold the work; the record holds that it happened.
"""

from __future__ import annotations

from typing import Literal

from jimmyhq_app import App, Payload


class PieceStarted(Payload):
    piece: int
    way: Literal["topic", "idea", "reader"]


class PieceDecided(Payload):
    piece: int
    decision: Literal["approve", "send_back", "decline", "write_anyway", "drop"]


class PublishAttempted(Payload):
    piece: int
    attempt: int
    publisher: str


class PublishResult(Payload):
    piece: int
    attempt: int
    outcome: Literal["sent", "refused", "unsure", "cleared"]


class PiecePublished(Payload):
    piece: int


class HeaderMade(Payload):
    piece: int
    image: int
    provider: str


def declare(app: App) -> None:
    app.kind("piece.started", example={"piece": 1, "way": "topic"})(PieceStarted)
    app.kind("piece.decided", example={"piece": 1, "decision": "approve"})(PieceDecided)
    app.kind("publish.attempted", example={"piece": 1, "attempt": 1,
                                           "publisher": "practice"})(PublishAttempted)
    app.kind("publish.result", example={"piece": 1, "attempt": 1,
                                        "outcome": "sent"})(PublishResult)
    app.kind("piece.published", example={"piece": 1})(PiecePublished)
    app.kind("header.made", example={"piece": 1, "image": 1,
                                     "provider": "practice"})(HeaderMade)
