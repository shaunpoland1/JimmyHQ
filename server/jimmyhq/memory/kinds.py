"""Event kinds for the memory module.

The Library's files are the truth; these events are its history. None of them
carries a note's text, a fact's text, a search query, a candidate's statement
or anything read from a source. A note's bytes go to memory's own store
(``memory_store.db``, which can really delete) and the event carries their
hash and size, so history can show what a note said before for as long as the
owner keeps it. A fact event points at the note version in which the change
can be read.

Subjects: ``memory_note:<path>``, ``memory_fact:<id>`` and
``memory_candidate:<id>``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

_REF = "sha256:" + "0" * 64
_FACT = "01JABCDEFGHJKMNPQRSTVWXYZ0"


@kind("memory.note.saved", example={
    "note": "shared/preferences.md", "ref": _REF, "size": 120, "facts": 3, "created": False,
    "moved_from": None,
})
class NoteSaved(Payload):
    """A note was created or changed, in the app or by hand on disk (the actor
    says which: a hand edit is ``owner:disk``). ``ref`` is the hash of its bytes,
    which memory's own store keeps under this event's id until they are purged."""

    note: str
    ref: str
    size: int = Field(ge=0)
    facts: int = Field(ge=0)
    created: bool = False
    moved_from: str | None = None  # set when this is the same note under a new name


@kind("memory.note.removed", example={"note": "shared/old.md", "moved_to": None})
class NoteRemoved(Payload):
    """A note is no longer in the Library: deleted, renamed, or no longer readable."""

    note: str
    moved_to: str | None = None
    reason: str | None = Field(None, max_length=200)


@kind("memory.fact.saved", example={
    "fact_id": _FACT, "note": "shared/preferences.md", "note_ref": _REF, "kind": "preference",
    "confidence": 0.8, "confirmed": False, "evidence": ["tape:01JABCDEFGHJKMNPQRSTVWXYZ1"],
    "chars": 31, "revived": False,
})
class FactSaved(Payload):
    """A fact exists: remembered through the app, or typed into a note by hand.
    Read its words in the note version ``note_ref``."""

    fact_id: str
    note: str
    note_ref: str
    kind: str
    confidence: float = Field(ge=0, le=1)
    confirmed: bool
    evidence: list[str] = Field(default_factory=list, max_length=20)
    chars: int = Field(ge=0)
    revived: bool = False  # a retired fact whose line came back


@kind("memory.fact.edited", example={
    "fact_id": _FACT, "note": "shared/preferences.md", "note_ref": _REF,
    "changed": ["text"], "from_note": None, "kind": "preference", "confirmed": True,
    "chars": 34,
})
class FactEdited(Payload):
    """A fact's words, note, kind or confirmation changed. ``changed`` says which."""

    fact_id: str
    note: str
    note_ref: str | None = None
    changed: list[Literal["text", "note", "kind", "confirmed"]]
    from_note: str | None = None
    kind: str
    confirmed: bool
    chars: int = Field(ge=0)


@kind("memory.fact.retired", example={
    "fact_id": _FACT, "note": "shared/preferences.md", "reason": "The line was removed.",
})
class FactRetired(Payload):
    """A fact's line is gone from the Library."""

    fact_id: str
    note: str
    reason: str = Field(max_length=300)


class RecalledPassage(Payload):
    id: str  # the fact id, or "c:" and the start of the passage's hash for free text
    note: str
    chars: int = Field(ge=0)
    why: Literal["keyword", "meaning", "both"]


@kind("memory.recalled", example={
    "caller": "chat.reply", "query_chars": 24, "query_hash": "0" * 64, "scope": ["shared"],
    "passages": [{"id": _FACT, "note": "shared/preferences.md", "chars": 31, "why": "both"}],
    "trimmed": 1, "methods": ["keyword", "meaning"], "shown_chars": 62, "k": 8,
    "budget_chars": 4000, "embedding_model": "home/example-embedder",
})
class Recalled(Payload):
    """The receipt for one retrieval: what the caller was given, to the character.

    ``passages`` are the ones admitted, in order, and ``shown_chars`` is the
    length of the block ``Recall.render()`` returns. The query is not here:
    only its length and a hash.
    """

    caller: str
    query_chars: int = Field(ge=0)
    query_hash: str
    scope: list[str] = Field(max_length=20)
    passages: list[RecalledPassage] = Field(max_length=100)
    trimmed: int = Field(ge=0)
    methods: list[Literal["keyword", "meaning"]]
    shown_chars: int = Field(ge=0)
    k: int = Field(ge=0)
    budget_chars: int = Field(ge=0)
    embedding_model: str | None = None


@kind("memory.history.purged", example={
    "why": "note_deleted", "versions": 4, "bodies": 3, "notes": 1,
})
class HistoryPurged(Payload):
    """Stored versions of notes were deleted for good. Counts only: which text
    went is exactly what this event must not say."""

    why: Literal["note_deleted", "forgotten_everywhere", "retention"]
    versions: int = Field(ge=0)
    bodies: int = Field(ge=0)
    notes: int = Field(ge=0)


class MinedDropped(Payload):
    """Why proposed facts were not kept, by reason."""

    duplicate: int = Field(0, ge=0)
    declined: int = Field(0, ge=0)
    no_evidence: int = Field(0, ge=0)
    not_from_owner: int = Field(0, ge=0)
    secret: int = Field(0, ge=0)
    too_long: int = Field(0, ge=0)
    invalid: int = Field(0, ge=0)
    too_many: int = Field(0, ge=0)  # more facts in one answer than are ever read


@kind("memory.mined", example={
    "source": "chat", "trigger": "schedule", "read": 20, "skipped": 2, "proposed": 5,
    "saved": 1, "sent": 2, "ignored": 1,
    "dropped": {"duplicate": 1, "declined": 0, "no_evidence": 0, "not_from_owner": 0,
                "secret": 0, "too_long": 0, "invalid": 0, "too_many": 0},
    "model": "home/example-model", "local": True, "error": None, "cursor": "1042",
})
class Mined(Payload):
    """One mining pass over one source. Counts only: what was read and what
    was proposed stay in memory's own store. ``proposed`` always equals
    ``saved + sent + ignored`` plus everything in ``dropped``."""

    source: str
    trigger: Literal["schedule", "owner"]
    read: int = Field(ge=0)  # items shown to the model
    skipped: int = Field(ge=0)  # items the source passed over
    proposed: int = Field(ge=0)
    saved: int = Field(ge=0)  # straight to the Library
    sent: int = Field(ge=0)  # to the Bench, or queued for it
    ignored: int = Field(ge=0)
    dropped: MinedDropped
    model: str | None = None  # "provider/model" that answered
    local: bool | None = None
    error: str | None = Field(None, max_length=300)  # our own sentence, never the model's
    # How far the source has now been read: a plain marker (a Tape position for
    # the built-in sources), never text. Lets a lost store resume in place.
    cursor: str | None = Field(None, max_length=64, pattern=r"^[0-9A-Za-z:_.-]+$")


@kind("memory.candidate.settled", example={
    "candidate_id": _FACT, "source": "chat", "outcome": "kept", "via": "bench",
    "fact_id": _FACT, "auto": False,
})
class CandidateSettled(Payload):
    """What became of a proposed fact. The statement is not here."""

    candidate_id: str
    source: str
    outcome: Literal["kept", "declined", "withdrawn"]
    via: Literal["bench", "memory", "miner", "library"]
    fact_id: str | None = None
    auto: bool = False  # it had been saved without asking
