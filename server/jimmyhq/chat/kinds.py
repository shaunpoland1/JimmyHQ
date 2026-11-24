"""Event kinds for chat.

Every chat event is printed with the Tape subject ``chat:<conversation id>``,
so the recorded part of one conversation's story is a single indexed read.
What is said while a conversation is off the record is not an event at all. Message text up to
about 4 KB travels in the event; longer text goes to the Vault and the event
keeps the reference.

Room is left for what comes later without changing stored data: a target of
kind "agent" is already a valid shape, and attachments or the steps a model
took can be added to the message kinds as a new version with an upcaster.

Version 2 of four kinds arrived with memory: a conversation says whether it
uses memory (``use_memory``; one from before the switch existed reads as on,
which is the setting's default), and a reply says which memory it was given
(``memory``; a reply from before reads as ``None``, the same as "memory was
off"). Stored bytes are not rewritten: the upcasters below fill the gap when
an old event is read.

Version 3 of ``chat.message.answered`` arrived with web lookup: a reply says
what it looked up (``web``: counts, and the title and address of each source,
never what a page said; a reply from before reads as ``None``, "web was off").
"""

from __future__ import annotations

from typing import Literal

from jimmyhq.tape.registry import Payload, kind, upcaster

TitleSource = Literal["none", "cut", "model", "owner"]
State = Literal["complete", "stopped", "failed"]

_CONV = "01JABCDEFGHJKMNPQRSTVWXYZ0"
_USER = "01JABCDEFGHJKMNPQRSTVWXYZ1"
_REPLY = "01JABCDEFGHJKMNPQRSTVWXYZ2"
_RECEIPT = "01JABCDEFGHJKMNPQRSTVWXYZ3"
_FACT = "01JABCDEFGHJKMNPQRSTVWXYZ4"
_ROLE = {"kind": "role", "role": "default"}


class TargetP(Payload):
    """What answers in a conversation: a role, one named model, or (later) an agent."""

    kind: Literal["role", "model", "agent"]
    role: str | None = None
    provider_id: str | None = None
    model: str | None = None
    agent_id: str | None = None


@kind("chat.conversation.started", example={
    "conversation_id": _CONV, "title": "", "title_source": "none", "target": _ROLE,
    "effort": None, "local_only": False,
})
class ConversationStartedV1(Payload):
    conversation_id: str
    title: str = ""
    title_source: TitleSource = "none"  # who named it; "none" until the first message
    target: TargetP
    effort: str | None = None
    local_only: bool = False


@kind("chat.conversation.started", v=2, example={
    "conversation_id": _CONV, "title": "", "title_source": "none", "target": _ROLE,
    "effort": None, "local_only": False, "use_memory": True,
})
class ConversationStarted(ConversationStartedV1):
    use_memory: bool = True  # recall from memory before each reply


@upcaster("chat.conversation.started", 1)
def _started_v2(payload: dict) -> dict:
    return payload | {"use_memory": True}


@kind("chat.conversation.changed", example={
    "conversation_id": _CONV, "title": "Plans for the spring", "title_source": "owner",
})
class ConversationChangedV1(Payload):
    """A rename, or a new default for what answers. Absent fields are unchanged."""

    conversation_id: str
    title: str | None = None
    title_source: TitleSource | None = None
    target: TargetP | None = None
    effort: str | None = None
    effort_cleared: bool = False  # true when the effort was set back to "not chosen"
    local_only: bool | None = None


@kind("chat.conversation.changed", v=2, example={
    "conversation_id": _CONV, "use_memory": False,
})
class ConversationChanged(ConversationChangedV1):
    use_memory: bool | None = None


@upcaster("chat.conversation.changed", 1)
def _changed_v2(payload: dict) -> dict:
    return payload | {"use_memory": None}


@kind("chat.conversation.archived", example={"conversation_id": _CONV})
class ConversationArchived(Payload):
    conversation_id: str


@kind("chat.conversation.mode", example={"conversation_id": _CONV, "on_record": False})
class ConversationMode(Payload):
    """From here the conversation is off the record, or back on it. The event
    says only that the switch was flipped: what is said off the record is kept
    in another store and never reaches the Tape."""

    conversation_id: str
    on_record: bool


@kind("chat.conversation.restored", example={
    "conversation_id": _CONV, "title": "Plans for the spring", "title_source": "cut",
    "target": _ROLE, "effort": None, "local_only": False, "created": 1767225600000,
    "updated": 1767225660000, "messages": 2, "started_event": _CONV,
})
class ConversationRestoredV1(Payload):
    """Carries the conversation's list entry, so a view that had let an old
    archived conversation go can show it again without reading its history."""

    conversation_id: str
    title: str
    title_source: TitleSource
    target: TargetP
    effort: str | None = None
    local_only: bool
    created: int
    updated: int
    messages: int
    started_event: str


@kind("chat.conversation.restored", v=2, example={
    "conversation_id": _CONV, "title": "Plans for the spring", "title_source": "cut",
    "target": _ROLE, "effort": None, "local_only": False, "use_memory": True,
    "created": 1767225600000, "updated": 1767225660000, "messages": 2,
    "started_event": _CONV,
})
class ConversationRestored(ConversationRestoredV1):
    use_memory: bool = True


@upcaster("chat.conversation.restored", 1)
def _restored_v2(payload: dict) -> dict:
    return payload | {"use_memory": True}


@kind("chat.message.sent", example={
    "conversation_id": _CONV, "message_id": _USER, "text": "What should I plant in spring?",
    "text_ref": None, "chars": 30, "title": "What should I plant in spring?",
})
class MessageSent(Payload):
    """A message from the person. The event's actor is who sent it."""

    conversation_id: str
    message_id: str
    text: str = ""  # short text travels here
    text_ref: str | None = None  # long text lives in the Vault
    chars: int
    title: str | None = None  # set when this message also names the conversation


@kind("chat.message.answered", example={
    "conversation_id": _CONV, "message_id": _REPLY, "reply_to": _USER, "replaces": None,
    "state": "complete", "text": "Peas and broad beans do well early.", "text_ref": None,
    "chars": 35, "error": None, "error_kind": None, "provider_id": "home",
    "provider_type": "openai_compatible", "model": "example-model", "local": True,
    "fallback_from": None, "fallback_reason": None, "input_tokens": 120, "output_tokens": 48,
    "duration_ms": 840, "omitted": 0,
})
class MessageAnsweredV1(Payload):
    """One reply, printed once when it ends: complete, stopped part-way, or failed."""

    conversation_id: str
    message_id: str
    reply_to: str  # the message this answers
    replaces: str | None = None  # the earlier reply this one takes the place of
    state: State
    text: str = ""
    text_ref: str | None = None
    chars: int
    error: str | None = None  # for "failed": one sentence saying why
    error_kind: str | None = None
    provider_id: str | None = None  # None when no provider was reached
    provider_type: str | None = None
    model: str | None = None
    local: bool | None = None  # False means a hosted provider wrote this
    fallback_from: str | None = None
    fallback_reason: str | None = None
    input_tokens: int | None = None  # None when the provider never reported them
    output_tokens: int | None = None
    duration_ms: int
    omitted: int = 0  # earlier messages left out of what the model was sent


class MemoryPassageP(Payload):
    """One passage of memory a reply was given: where it is, never what it says.
    The words live in the Library, where they can later be forgotten for good."""

    id: str  # the fact id, or "c:" and the start of a free-text passage's hash
    note: str
    fact_id: str | None = None
    chars: int


class MemoryUsedP(Payload):
    """What memory did for one reply. ``used`` is false when memory was on for
    the conversation but nothing from it reached the model; ``reason`` then
    says why, in a sentence."""

    used: bool
    reason: str | None = None
    receipt_id: str | None = None  # the memory.recalled event, when a recall ran
    passages: list[MemoryPassageP] = []
    trimmed: int = 0  # found, but did not fit the budget
    methods: list[str] = []


@kind("chat.message.answered", v=2, example={
    "conversation_id": _CONV, "message_id": _REPLY, "reply_to": _USER, "replaces": None,
    "state": "complete", "text": "Peas and broad beans do well early.", "text_ref": None,
    "chars": 35, "error": None, "error_kind": None, "provider_id": "home",
    "provider_type": "openai_compatible", "model": "example-model", "local": True,
    "fallback_from": None, "fallback_reason": None, "input_tokens": 120, "output_tokens": 48,
    "duration_ms": 840, "omitted": 0,
    "memory": {"used": True, "reason": None, "receipt_id": _RECEIPT,
               "passages": [{"id": _FACT, "note": "shared/preferences.md",
                             "fact_id": _FACT, "chars": 41}],
               "trimmed": 0, "methods": ["keyword"]},
})
class MessageAnswered(MessageAnsweredV1):
    memory: MemoryUsedP | None = None  # None: memory was off for the conversation


@upcaster("chat.message.answered", 1)
def _answered_v2(payload: dict) -> dict:
    return payload | {"memory": None}


class WebSourceP(Payload):
    """One thing a reply found on the web: where it is, never what it says."""

    title: str
    url: str
    read: bool = False  # the page itself was read; otherwise it was a search result


class WebUsedP(Payload):
    """What web lookup did for one reply. ``used`` is false when the web was
    asked for and nothing was looked up; ``reason`` then says why, in a
    sentence, or is None when the model simply chose not to."""

    used: bool
    reason: str | None = None
    searches: int = 0
    pages: int = 0
    failed: int = 0  # lookups that did not work
    sources: list[WebSourceP] = []


@kind("chat.message.answered", v=3, example={
    "conversation_id": _CONV, "message_id": _REPLY, "reply_to": _USER, "replaces": None,
    "state": "complete", "text": "Sow broad beans from late winter (https://example.com/beans).",
    "text_ref": None, "chars": 60, "error": None, "error_kind": None, "provider_id": "home",
    "provider_type": "openai_compatible", "model": "example-model", "local": True,
    "fallback_from": None, "fallback_reason": None, "input_tokens": 2120, "output_tokens": 48,
    "duration_ms": 3840, "omitted": 0, "memory": None,
    "web": {"used": True, "reason": None, "searches": 1, "pages": 1, "failed": 0,
            "sources": [{"title": "Growing broad beans", "url": "https://example.com/beans",
                         "read": True}]},
})
class MessageAnsweredV3(MessageAnswered):
    web: WebUsedP | None = None  # None: web lookup was not asked for


@upcaster("chat.message.answered", 2)
def _answered_v3(payload: dict) -> dict:
    return payload | {"web": None}


@kind("chat.conversation.deleted", example={"conversation_id": _CONV, "messages": 12,
                                            "settled": True})
class ConversationDeleted(Payload):
    """A conversation was deleted for good, and everything that had to be
    cleaned up after it has been. Its other events were erased from the Tape
    (see the ``tape.erased`` event on the same subject); this says only that
    it happened, when, and how many messages on the record went. Never a
    title or a word of it.

    ``settled`` is whether, at that moment, the erased bytes were already out
    of the Tape's write-ahead log and the Vault. False means the worker
    finishes that within the hour (``Tape.finish``)."""

    conversation_id: str
    messages: int
    settled: bool = True


@kind("chat.conversation.delete_marked", example={"conversation_id": _CONV,
                                                  "at": 1767225600000})
class ConversationDeleteMarked(Payload):
    """An archived conversation has been archived for longer than the owner's
    setting allows, and was marked for deletion at ``at``. It is deleted only
    if it is still due after the grace period has passed since. ``at`` is None
    when the mark is taken away again (the setting changed). Restoring the
    conversation takes the mark away without an event of this kind."""

    conversation_id: str
    at: int | None
