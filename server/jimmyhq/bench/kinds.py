"""Event kinds for the Bench and the permission ladder.

Every Bench event is printed with the Tape subject ``bench:<card id>``, so one
card's whole story is a single indexed read. The thing a card is *about*
(``task:<id>`` and the like) travels in the payload as ``subject``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

Lane = Literal["needs", "done", "waiting"]
Level = Literal["read", "approve", "alone"]

_CARD = "01JABCDEFGHJKMNPQRSTVWXYZ0"
_REF = "sha256:" + "0" * 64
_ROW = {"actor": "agent:scout", "tool": "posts", "action": "publish", "category": "publish"}


class Action(Payload):
    """One button on a card, declared by whoever filed it."""

    id: str
    label: str
    style: Literal["primary", "default", "danger"] = "default"
    needs_reason: bool = False  # the owner must type why before this is accepted
    closes: bool = False  # the card leaves the Bench
    moves_to: Lane | None = None  # or it moves to another lane


class Detail(Payload):
    label: str
    text: str


class FileRef(Payload):
    name: str
    ref: str  # a Vault reference, "sha256:<hex>"


class LadderRow(Payload):
    """Which ladder row a card belongs to, so a decision can be tallied on it."""

    actor: str
    tool: str
    action: str
    category: str


@kind("bench.filed", example={
    "card_id": _CARD, "kind": "posts.draft", "source": "Posts", "title": "Draft ready",
    "summary": "A post about the spring release", "body": "Here is the draft.",
    "lane": "needs",
    "actions": [{"id": "approve", "label": "Approve", "style": "primary", "closes": True}],
})
class BenchFiled(Payload):
    """A card was put on the Bench. The event's actor is who filed it."""

    card_id: str
    kind: str
    source: str
    title: str
    summary: str = ""
    body: str = ""  # short bodies travel here
    body_ref: str | None = None  # long ones live in the Vault
    why: str | None = None
    details: list[Detail] = Field(default_factory=list)
    files: list[FileRef] = Field(default_factory=list)
    subject: str | None = None
    lane: Lane
    actions: list[Action]
    ladder: LadderRow | None = None


@kind("bench.updated", example={"card_id": _CARD, "note": "Shortened the second paragraph."})
class BenchUpdated(Payload):
    """The filer added a note or revised the card. Absent fields are unchanged."""

    card_id: str
    note: str | None = None
    title: str | None = None
    summary: str | None = None
    body: str | None = None
    body_ref: str | None = None


@kind("bench.decided", example={
    "card_id": _CARD, "card_kind": "posts.draft", "action": "no", "label": "No",
    "reason": "Too long for this channel.", "closes": True,
    "filed_by": "agent:scout", "updates": 0,
})
class BenchDecided(Payload):
    """The owner clicked an action. Carries enough of the card for another
    process to react without looking the card up."""

    card_id: str
    card_kind: str
    subject: str | None = None
    action: str
    label: str
    note: str | None = None
    reason: str | None = None
    closes: bool = False
    moves_to: Lane | None = None
    ladder: LadderRow | None = None
    # Who filed the card, and how many times it had been updated when the owner
    # clicked: the revision the decision is about. None on events from before
    # these were recorded.
    filed_by: str | None = None
    updates: int | None = None
    # True when the owner had switched off "a No or an Undo takes back alone" at the
    # moment of this click. Written here so a replay reads the answer of that moment.
    keeps_level: bool = False


@kind("bench.snoozed", example={"card_id": _CARD, "until": 1767225600000})
class BenchSnoozed(Payload):
    card_id: str
    until: int  # unix milliseconds; the card returns on its own after this


@kind("bench.unsnoozed", example={"card_id": _CARD})
class BenchUnsnoozed(Payload):
    card_id: str


@kind("bench.withdrawn", example={"card_id": _CARD, "note": "No longer needed."})
class BenchWithdrawn(Payload):
    """The filer took the card back before it was decided."""

    card_id: str
    note: str | None = None


@kind("ladder.set", example={**_ROW, "level": "alone"})
class LadderSet(Payload):
    """A row's level was set. The event's actor is who set it."""

    actor: str
    tool: str
    action: str
    level: Level
    category: str | None = None
    # True on every level set since the owner may choose "alone" for any category.
    # Absent on older events: an "alone" from then never counted for a category
    # that was barred, and it still does not.
    unbarred: bool = False


@kind("ladder.acted", example={**_ROW, "card_id": _CARD})
class LadderActed(Payload):
    """An actor did something without asking, and filed a "done" card for it."""

    actor: str
    tool: str
    action: str
    category: str
    card_id: str
