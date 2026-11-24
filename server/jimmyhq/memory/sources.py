"""Sources: the places mining reads, and the three that come with the core.

A source yields **items**: something that was said or done, with a stable
reference, a time, who wrote it and its text. Any module or app adds one:

    from jimmyhq import memory

    memory.register_source(memory.Source(
        id="journal", label="Journal", description="What you write in your journal.",
        default_on=False, collect=collect))

    def collect(core, cursor, limit):
        ...  # read forward from ``cursor`` (None the first time)
        return items, next_cursor

``collect`` reads at most ``limit`` things forward from an opaque cursor, in
the order they happened, and never returns the same thing twice. The cursor it
returns is stored in memory's own database, so a restart carries on where it
stopped. Something a source decides not to read is returned as
``Item.skipped(ref, when_ms, reason)`` so it is counted rather than lost
without trace.

Registering a source declares its switch, ``memory.source.<id>``, so the
generated settings screen shows it with no UI work.

The three built in read the Tape, which only ever holds what is on the record.
Nothing here opens the off-the-record chat store.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jimmyhq.core import Core
from jimmyhq.settings import Setting, declare, declared, section

OWNER = "owner"
ASSISTANT = "assistant"
_ID = re.compile(r"[a-z][a-z0-9_]{0,39}")
_WAITING_CAP = 500  # "more than this" is all the status needs to say
_CARD_BODY = 1500  # characters of a card's body shown beside the owner's decision


@dataclass(frozen=True)
class Item:
    ref: str  # stable, without spaces: "tape:<event id>"
    when_ms: int
    who: str  # "owner", "assistant", or another actor
    text: str
    where: str = ""  # a few words for the owner: "Chat: Plans for spring"
    skip: str | None = None  # set when the source chose not to read it: the reason

    @classmethod
    def skipped(cls, ref: str, when_ms: int, reason: str) -> Item:
        return cls(ref, when_ms, "", "", "", reason)


Collect = Callable[[Core, str | None, int], tuple[list[Item], str | None]]


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    description: str  # plain words: what is read, shown as the switch's help
    default_on: bool
    collect: Collect
    # Optional: how many things lie beyond a cursor, as (count, there_are_more).
    waiting: Callable[[Core, str | None], tuple[int, bool]] | None = None
    # Optional: whether the thing a reference names is still there. A candidate
    # whose evidence has all gone is withdrawn.
    exists: Callable[[Core, str], bool] | None = None
    # True when the cursor is a place on the Tape, a number (``_after`` below). Mining
    # takes such a cursor that is past the Tape's end back to it (see ``mining._cursor``).
    tape_cursor: bool = False

    @property
    def setting(self) -> str:
        return f"memory.source.{self.id}"


_SOURCES: dict[str, Source] = {}


def register_source(source: Source) -> Source:
    """Add a source and declare its on/off setting. Call it at import time."""
    if not isinstance(source.id, str) or not _ID.fullmatch(source.id):
        raise ValueError("a source id is a short lower-case name such as 'journal'")
    if source.id in _SOURCES:
        raise ValueError(f"the source {source.id!r} is already registered")
    if not source.label.strip() or not source.description.strip():
        raise ValueError(f"the source {source.id!r} needs a label and a description")
    if not any(s.key == source.setting for s in declared()):
        section("memory", "Memory")
        section("memory_sources", "What it learns from", parent="memory")
        declare(Setting(
            key=source.setting, section="memory_sources", type="bool",
            default=bool(source.default_on), label=f"Learn from: {source.label}",
            help=source.description,
        ))
    _SOURCES[source.id] = source
    return source


def unregister_source(source_id: str) -> None:
    """Take a source out. Its setting stays declared; its cursor stays stored."""
    _SOURCES.pop(source_id, None)


def all_sources() -> list[Source]:
    return list(_SOURCES.values())


def get_source(source_id: str) -> Source | None:
    return _SOURCES.get(source_id)


# --- reading the Tape forward --------------------------------------------------


def _after(core: Core, cursor: str | None, limit: int, kinds: tuple[str, ...]):
    seq = int(cursor) if cursor and cursor.isdigit() else 0
    events = core.tape.after(seq, kinds=kinds, limit=limit)
    return events, (str(events[-1].seq) if events else cursor)


def _waiting(kinds: tuple[str, ...]) -> Callable[[Core, str | None], tuple[int, bool]]:
    def count(core: Core, cursor: str | None) -> tuple[int, bool]:
        events, _ = _after(core, cursor, _WAITING_CAP + 1, kinds)
        return min(len(events), _WAITING_CAP), len(events) > _WAITING_CAP
    return count


def _on_tape(core: Core, ref: str) -> bool:
    """Whether the event a reference names still says anything: one that the
    owner had erased is no longer evidence."""
    if not ref.startswith("tape:"):
        return True
    event = core.tape.get(ref[5:])
    return event is not None and not event.erased


def _ref(event: Any) -> str:
    return f"tape:{event.id}"


# --- chat ----------------------------------------------------------------------

_CHAT = ("chat.message.sent", "chat.message.answered")


def _collect_chat(core: Core, cursor: str | None, limit: int) -> tuple[list[Item], str | None]:
    """The owner's messages and the model's complete replies, from conversations
    on the record. These are Tape events; what is said off the record is never
    an event, so it cannot be reached from here.

    Passed over: a conversation that is archived, a message sent by a token
    rather than the owner, a reply that failed or was stopped, and every reply
    written after the conversation was first switched off or on the record:
    the model that wrote it was shown the off-the-record part too and may
    repeat it (``off_record_question`` when the question itself was asked off
    the record, ``off_record_context`` otherwise)."""
    from jimmyhq.chat import store  # noqa: PLC0415 - chat may import memory

    events, cursor = _after(core, cursor, limit, _CHAT)
    listed = store.listed(core) if events else {}
    asked: dict[str, set[str]] = {}
    flipped: dict[str, int] = {}
    out: list[Item] = []
    for event in events:
        ref = _ref(event)
        if not event.readable:
            out.append(Item.skipped(ref, event.ts, "unreadable"))
            continue
        p = event.payload
        conv = listed.get(p["conversation_id"])
        if conv is None or conv.get("archived"):
            out.append(Item.skipped(ref, event.ts, "archived"))
            continue
        where = f"Chat: {conv['title'][:60]}" if conv.get("title") else "Chat"
        if event.kind == "chat.message.sent":
            if event.actor != OWNER:
                out.append(Item.skipped(ref, event.ts, "not_owner"))
                continue
            who = OWNER
        else:
            if p["state"] != "complete":
                out.append(Item.skipped(ref, event.ts, "incomplete"))
                continue
            if conv.get("mixed"):
                conv_id = p["conversation_id"]
                if conv_id not in asked:
                    asked[conv_id] = {
                        e.payload["message_id"] for e in core.tape.by_subject(
                            store.subject(conv_id), kind="chat.message.sent",
                            limit=store.MAX_EVENTS) if e.readable}
                if p["reply_to"] not in asked[conv_id]:
                    out.append(Item.skipped(ref, event.ts, "off_record_question"))
                    continue
                # From the first time the conversation was switched, the model
                # that writes a reply is shown the whole conversation, the part
                # kept off the record included, and may repeat it. After that
                # point only the owner's own on-the-record words are read.
                if conv_id not in flipped:
                    first = core.tape.by_subject(store.subject(conv_id), limit=1,
                                                 kind="chat.conversation.mode")
                    flipped[conv_id] = first[0].seq if first else 0
                if event.seq > flipped[conv_id]:
                    out.append(Item.skipped(ref, event.ts, "off_record_context"))
                    continue
            who = ASSISTANT
        out.append(Item(ref, event.ts, who, store.text_of(core, store.message_of(event)),
                        where))
    return out, cursor


# --- tasks ---------------------------------------------------------------------

_TASKS = ("task.filed", "task.completed")


def _others_wrote(core: Core, task_id: str) -> bool:
    """Whether anyone but the owner has filed or edited this task. An edit
    re-files every field, so after one the title and notes may be a token's
    or an agent's words, whoever completes the task."""
    return any(e.actor != OWNER for kind in ("task.filed", "task.refiled")
               for e in core.tape.by_subject(f"task:{task_id}", kind=kind, limit=10_000))


def _collect_tasks(core: Core, cursor: str | None, limit: int) -> tuple[list[Item], str | None]:
    """A task's title and notes when the owner files it, and again when the
    owner completes it. Passed over: tasks filed, edited or completed by a
    token or an agent (their words are not the owner's), and a task that can
    no longer be read. Rounds of a repeating task are not read: they would say the same
    thing every week."""
    from jimmyhq.tasks import TaskError, service  # noqa: PLC0415

    events, cursor = _after(core, cursor, limit, _TASKS)
    out: list[Item] = []
    for event in events:
        ref = _ref(event)
        if not event.readable:
            out.append(Item.skipped(ref, event.ts, "unreadable"))
            continue
        if event.actor != OWNER:
            out.append(Item.skipped(ref, event.ts, "not_owner"))
            continue
        if event.kind == "task.filed":
            title, notes, verb = event.payload["title"], event.payload["notes"], "filed"
        else:
            try:
                task = service(core).get(event.payload["task_id"])
            except TaskError:
                out.append(Item.skipped(ref, event.ts, "gone"))
                continue
            if task.get("filed_by", OWNER) != OWNER or _others_wrote(core, task["id"]):
                out.append(Item.skipped(ref, event.ts, "not_owner"))
                continue
            title, notes, verb = task["title"], task.get("notes") or "", "completed"
        text = f"Task {verb}: {title}" + (f"\n{notes}" if notes.strip() else "")
        out.append(Item(ref, event.ts, OWNER, text, "Tasks"))
    return out, cursor


# --- the Bench -----------------------------------------------------------------

_BENCH = ("bench.decided",)
_WORDLESS = ("not_yet", "ok", "undo")


def _collect_bench(core: Core, cursor: str | None, limit: int) -> tuple[list[Item], str | None]:
    """What the owner decided on the Bench, with their note or reason, after
    the card they were answering (two items: see below). Passed over: memory's own cards (their
    answer is already recorded as a keep or a decline), a click that says
    nothing (Not yet, Got it or Undo without a note), and a card that can no
    longer be read. A long card body is shortened, not skipped: the owner's
    answer is the part that matters."""
    from jimmyhq import bench  # noqa: PLC0415
    from jimmyhq.bench.cards import RESERVED_ACTIONS  # noqa: PLC0415

    events, cursor = _after(core, cursor, limit, _BENCH)
    out: list[Item] = []
    for event in events:
        ref = _ref(event)
        if not event.readable:
            out.append(Item.skipped(ref, event.ts, "unreadable"))
            continue
        p = event.payload
        if event.actor != OWNER:
            out.append(Item.skipped(ref, event.ts, "not_owner"))
            continue
        if p["card_kind"].startswith("memory."):
            out.append(Item.skipped(ref, event.ts, "own_card"))
            continue
        if p["action"] in _WORDLESS and not p["note"] and not p["reason"]:
            out.append(Item.skipped(ref, event.ts, "empty"))
            continue
        card = bench.get_card(core, p["card_id"])
        if card is None:
            out.append(Item.skipped(ref, event.ts, "gone"))
            continue
        # Two items, because two people wrote them: the card is its filer's
        # words (an agent's, or a token's), and only the answer is the owner's.
        # A fact has to rest on something the owner wrote.
        lines = [f"A card from {card['source']} asked: {card['title']}"]
        if card["summary"]:
            lines.append(card["summary"])
        if card["body"]:
            body = card["body"]
            lines.append(body if len(body) <= _CARD_BODY else body[:_CARD_BODY] + " […]")
        where = f"Bench: {card['source']}"
        out.append(Item(f"card:{p['card_id']}:{event.id}", event.ts,
                        card["filed_by"] or "unknown", "\n".join(lines), where))
        # Nothing the filer wrote goes into the owner's item: not the card's
        # title, and not the label of a button the filer made up. A fact that
        # cites only this item rests on the owner's words alone.
        own = RESERVED_ACTIONS.get(p["action"])
        answer = ["To the card in the item just before this one the owner answered: "
                  + (f"{own['label']}." if own else "one of the card's own buttons.")]
        if p["reason"]:
            answer.append(f"The owner's reason: {p['reason']}")
        if p["note"]:
            answer.append(f"The owner's note: {p['note']}")
        out.append(Item(ref, event.ts, OWNER, "\n".join(answer), where))
    return out, cursor


register_source(Source(
    id="chat", label="Chat", default_on=True, collect=_collect_chat,
    waiting=_waiting(_CHAT), exists=_on_tape, tape_cursor=True,
    description="What you and the model say in conversations that are on the record. "
                "Conversations kept off the record, and archived ones, are never read; once "
                "a conversation has been switched, only your own on-the-record messages "
                "are read from it, not the model's replies.",
))
register_source(Source(
    id="tasks", label="Tasks", default_on=True, collect=_collect_tasks,
    waiting=_waiting(_TASKS), exists=_on_tape, tape_cursor=True,
    description="The titles and notes of tasks you file and complete. Tasks filed or "
                "edited by an agent or a token are not read.",
))
register_source(Source(
    id="bench", label="Bench decisions", default_on=True, collect=_collect_bench,
    waiting=_waiting(_BENCH), exists=_on_tape, tape_cursor=True,
    description="What you decide on the Bench, with the note or the reason you give, and "
                "the card you were answering. A No with a reason says a lot about what "
                "you want.",
))
