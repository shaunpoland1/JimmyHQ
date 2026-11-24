"""Deleting a conversation for good, on the record and off it.

Archiving only hides a conversation. This removes it:

- every ``chat.*`` event of the conversation that says anything (messages,
  replies and what each kept about memory, titles, what answered) is erased
  from the Tape with ``Tape.erase``. The events keep their place, so order and
  counts stay true, and read back empty;
- long messages kept in the Vault go, unless another event still names the
  same file;
- a model receipt descended from the conversation that kept its prompt or its
  answer is erased too. Chat never asks for that text to be kept, so today
  there is none and every receipt stays, with its token counts;
- the receipts of its web lookups (``lookup.searched``, ``lookup.fetched``)
  are erased: for a conversation on the record they name what was searched
  for and which pages were read. They are found by their lineage: each
  carries the conversation's first event as its root and the message it was
  looked up for as its cause. A lookup made for words kept off the record is
  tied to nothing and holds only a keyed hash, so it is not found and stays;
- the part kept off the record goes through that store's own delete;
- what memory's mining quoted from it goes (``memory.evidence_erased``).
  Facts already saved from it stay: the owner may have confirmed them.

What is left on the Tape: the emptied events, the ones that say nothing
(archived, the record switch, a mark for deletion), one ``tape.erased``
receipt with counts by kind, and one ``chat.conversation.deleted`` with the
id and how many messages went. A conversation that was never on the record
leaves nothing at all, as before.

**A deletion can be interrupted and is always finished.** The erase is one
transaction. Everything after it (removing Vault files, emptying the Tape's
log, memory's clean-up, the off-the-record part) can fail or be cut short, so
``chat.conversation.deleted`` is printed last, only when all of it has been
done. Erased events with no such event after them mean an unfinished
deletion: asking again finishes it, and so does the worker's hourly job. What
the Tape itself still owes (its log, Vault files) it keeps note of in its own
database (``Tape.pending``).

Only the owner may do this, or the system on the owner's standing instruction
(the ``chat.delete_archived_after_days`` setting): see ``delete_expired``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from jimmyhq.chat import backends, store
from jimmyhq.chat import conversations as convs
from jimmyhq.chat.store import ChatError
from jimmyhq.core import Core
from jimmyhq.ids import now_ms
from jimmyhq.tape.registry import Event
from jimmyhq.tape.store import ERASERS, TapeConflict

log = logging.getLogger("jimmyhq.chat")

REASON = "chat.conversation.deleted"
# Chat events that say nothing beyond the conversation's id. Every other
# ``chat.*`` event of a conversation is erased, including kinds added later.
_SAYS_NOTHING = ("chat.conversation.archived", "chat.conversation.mode", store.MARKED, REASON)
_RECEIPTS = ("model.called", "model.failed")
_LOOKUPS = ("lookup.searched", "lookup.fetched")  # they name the query, the URL and the host
_PAGE = 5000
MAX_PER_RUN = 200  # conversations the job deletes in one go; the rest wait for the next
_SETTINGS = ("chat.delete_archived_after_days", "chat.delete_grace_hours")

BUSY = ("A reply is still being written in this conversation. Stop it or wait for it to "
        "finish, then delete.")
CHANGED = "This conversation changed just now. Reload it and try again."


def _events(core: Core, conv_id: str) -> list[Event]:
    """Every event about the conversation, erased and unreadable ones too."""
    out: list[Event] = []
    while True:
        page = core.tape.by_subject(store.subject(conv_id), limit=_PAGE,
                                    after=out[-1].seq if out else None)
        out += page
        if len(page) < _PAGE:
            return out


def _receipts(core: Core, events: list[Event]) -> list[Event]:
    """Receipts descended from the conversation that say something of it: its
    web lookups, and any model receipt that holds a reference to kept text.

    Found by lineage. Every call made for a reply on the record carries the
    conversation's first event as its root and the message being answered as
    its cause; both are looked at, so a receipt with only one of them is
    still found."""
    found: dict[str, Event] = {}

    def look(receipts: list[Event]) -> None:
        for e in receipts:
            if e.erased:
                continue
            if e.kind in _LOOKUPS or (e.kind in _RECEIPTS and (
                    e.payload.get("prompt_ref") or e.payload.get("completion_ref"))):
                found[e.id] = e

    if events and events[0].kind == "chat.conversation.started":
        look(core.tape.by_root(events[0].id, limit=100_000))
    for event in events:
        if event.kind == "chat.message.sent":
            look(core.tape.caused_by(event.id, limit=10_000))
    return sorted(found.values(), key=lambda e: e.seq or 0)


def _delete(core: Core, conv_id: Any, actor: str,
            eligible: Callable[[dict[str, Any]], bool] | None = None,
            archived_before: int | None = None,
            marked_before: int | None = None) -> dict[str, Any] | None:
    if actor not in ERASERS:
        raise ChatError(403, "Only the signed-in owner can delete a conversation.")
    if not isinstance(conv_id, str) or not store.CONV_ID.fullmatch(conv_id):
        raise ChatError(404, store.NOT_FOUND)
    service = store.svc(core)
    off, _ = backends.both(core)
    with service.lock:
        if conv_id in service.runs:
            raise ChatError(409, BUSY)
        events = _events(core, conv_id)
        ours = [e for e in events if e.kind.startswith("chat.") and e.kind not in _SAYS_NOTHING]
        todo = [e for e in ours if not e.erased]
        begun = any(e.erased for e in ours)  # an earlier try erased some or all of it
        recorded = any(e.kind == REASON for e in events)
        off_part = off.find(conv_id)
        if not todo and off_part is None and not begun:
            raise ChatError(404, store.NOT_FOUND)
        finished = not todo and off_part is None and recorded
        try:
            conv = convs.find(core, conv_id)
        except ChatError:
            conv = None  # nothing readable is left of it: this is a second try
        if eligible is not None and (todo or off_part is not None) and (
                conv is None or not eligible(conv)):
            return None
        messages = 0
        receipts = _receipts(core, events)
        if todo:
            on_part = conv["parts"]["on"] if conv else None
            messages = on_part["messages"] if on_part else 0
            try:
                core.tape.erase(
                    [e.id for e in (*todo, *receipts)], actor=actor,
                    reason=REASON, subject=store.subject(conv_id),
                    expect_subject_seq=events[-1].seq)
            except TapeConflict:
                raise ChatError(409, CHANGED) from None
        elif receipts:
            # Left behind by a deletion made before lookups were erased with
            # the conversation. Its record stands as it is, so this erasure is
            # about no subject: it does not reopen a finished deletion.
            core.tape.erase([e.id for e in receipts], actor=actor, reason=REASON)
        if not messages:
            # A second try no longer has the list's own count: one per message
            # event, which counts a regenerated reply twice.
            messages = sum(1 for e in ours
                           if e.kind in ("chat.message.sent", "chat.message.answered"))
        went_off = 0
        if off_part is not None:
            # One the Tape never knew is deleted only if it still stands as the
            # caller found it; the Tape's own check covered the rest above.
            alone = not todo and not begun
            went_off = off.forget(conv_id, archived_before=archived_before if alone else None,
                                  marked_before=marked_before if alone else None)
            if went_off is None:
                return None
        # The list is rebuilt now, not at its next read, so the title is out
        # of the stored view before this returns.
        store.listed(core)
    if not ours:
        # Never on the record: the other store's delete was all of it.
        return {"deleted": True, "messages": went_off, "evidence": None, "finished": True,
                "settled": True}
    # From here on everything can be done again, and is whenever this is asked
    # for a deletion that has not been recorded as finished.
    evidence, cleared = None, True
    try:
        from jimmyhq import memory  # noqa: PLC0415 - memory imports chat's store

        evidence = memory.evidence_erased(core, [f"tape:{e.id}" for e in ours],
                                          actor=actor, reason=REASON)
    except Exception as exc:  # noqa: BLE001 - noted, and tried again: see below
        cleared = False
        log.warning("chat could not clear what memory quoted from a deleted conversation; "
                    "it is tried again within the hour: %s", type(exc).__name__)
    try:
        owed = core.tape.finish(core.vault)
        settled = not owed["settle"] and not owed["vault"]
    except Exception as exc:  # noqa: BLE001 - the Tape keeps its own note of what it owes
        settled = False
        log.warning("the Tape could not finish an erase; it is tried again within the "
                    "hour: %s", type(exc).__name__)
    if cleared and not recorded:
        # Printed last: its absence is what marks a deletion as unfinished.
        core.tape.print(REASON, {"conversation_id": conv_id, "messages": messages,
                                 "settled": settled},
                        actor=actor, subject=store.subject(conv_id))
        store.listed(core)
    return {"deleted": not finished, "messages": (messages + went_off) if not finished else 0,
            "evidence": evidence, "finished": cleared, "settled": settled}


def delete_conversation(core: Core, conv_id: Any, *, actor: str = "owner") -> dict[str, Any]:
    """Delete a whole conversation for good: what is on the record and what
    is off it. It cannot be undone.

    Returns ``{"deleted", "messages", "evidence", "finished", "settled"}``:

    - ``deleted``: True, or False when it had already been deleted and
      recorded as such (asking again is harmless: it only checks once more
      that memory quotes nothing of it);
    - ``messages``: how many went;
    - ``evidence``: what memory removed of its own (quotes, Bench cards,
      proposals withdrawn), or None for a conversation never on the record,
      or when memory could not be reached;
    - ``finished``: False when memory's clean-up failed. The conversation is
      gone, the worker tries the clean-up again every hour, and asking again
      tries it at once. ``chat.conversation.deleted`` is printed only when it
      has succeeded;
    - ``settled``: True when the erased bytes are out of the Tape's file, its
      write-ahead log and the Vault now. False when another reader held the
      log, or a Vault file was stored too recently to remove safely: the
      worker finishes that within the hour.

    The receipts of its web lookups go with it (they name the searches and
    the pages); asking again for one deleted before that was so erases those
    it left. Facts memory had already saved from the conversation stay. An id that
    never existed is a 404. Refused with a 409 while a reply is being written
    in it. ``actor`` must be ``"owner"`` or ``"system"``: a token cannot delete.
    """
    done = _delete(core, conv_id, actor)
    assert done is not None  # noqa: S101 - only a job's own conditions give None
    return done


def finish_pending(core: Core) -> dict[str, Any]:
    """Finish what earlier deletions left undone: the Tape's own debts (its
    log, Vault files) and any conversation whose events were erased without
    the deletion being recorded as finished. Returns what is still owed."""
    left = 0
    for conv_id in list(store.unfinished(core)):
        try:
            done = _delete(core, conv_id, "system")
        except ChatError as exc:
            log.info("an unfinished deletion could not be finished this time: status=%s",
                     exc.status)
            done = None
        left += not (done and done["finished"])
    return core.tape.finish(core.vault) | {"unfinished": left}


def _settings_changed_at(core: Core) -> int:
    """When either setting that decides deletion was last changed (0: never).
    A mark made before that was made under other rules and does not count."""
    newest = 0
    for kind in ("setting.updated", "setting.reset"):
        before = None
        while True:
            page = core.tape.latest(kind=kind, limit=500, before=before)
            hit = next((e for e in page if e.payload.get("key") in _SETTINGS), None)
            if hit is not None:
                newest = max(newest, hit.ts)
            if hit is not None or len(page) < 500:
                break
            before = page[-1].seq
    return newest


def delete_expired(core: Core, *, now: int | None = None) -> int:
    """The worker's hourly job. Returns how many conversations it deleted.

    First it finishes what earlier deletions left undone (``finish_pending``).
    Then, unless ``chat.delete_archived_after_days`` is 0, it works in two
    steps, never both for one conversation in one run:

    1. **Mark.** A conversation that has been archived for longer than the
       setting is marked for deletion, durably: an event on the Tape, or a
       column in the other store for one the Tape does not know. The archived
       list shows the day it will go.
    2. **Delete.** A conversation is deleted only if it is still due on a run
       at least ``chat.delete_grace_hours`` after it was marked.

    A mark does not survive anything that changes the answer: restoring the
    conversation (archiving it again starts a new clock), either setting being
    changed after the mark was made, the conversation no longer being due, or
    a mark dated in the future (the clock was wrong). So a mistyped number of
    days, or a clock that jumped, deletes nothing for the length of the grace
    period, and setting the number from 0 never deletes sooner than that.

    A conversation restored while this runs is left alone (the Tape refuses
    the erase if anything about the conversation changed since it was looked
    at; the other store checks inside its own transaction).
    """
    now = now_ms() if now is None else now
    finish_pending(core)
    days = store.delete_after_days(core)
    grace = store.delete_grace_ms(core)
    off, _ = backends.both(core)
    off.bring_up_to_date()
    cutoff = now - days * store.DAY_MS
    changed_at = _settings_changed_at(core)
    on_marks = store.marks(core)

    def due(conv: dict[str, Any]) -> bool:
        return days > 0 and bool(conv["archived"]) and conv.get("archived_at") is not None \
            and conv["archived_at"] <= cutoff

    def mark(conv: dict[str, Any], at: int | None) -> None:
        if conv["recorded"]:
            core.tape.print(store.MARKED, {"conversation_id": conv["id"], "at": at},
                            actor="system", subject=store.subject(conv["id"]))
        else:
            off.mark_for_deletion(conv["id"], at)

    waiting = [c for c in convs._entries(core) if c["archived"]]
    waiting += [{"id": conv_id, "archived": True, "archived_at": at, "recorded": True}
                for conv_id, at in store.dropped(core).items()]
    gone = 0
    for conv in waiting:
        marked = on_marks.get(conv["id"]) if conv["recorded"] else conv.get("delete_marked_at")
        if not due(conv):
            if marked is not None:
                mark(conv, None)  # the setting was raised or switched off
            continue
        if marked is None or marked < changed_at or marked > now:
            mark(conv, now)  # the grace period starts, or starts again
            continue
        if now - marked < grace or gone >= MAX_PER_RUN:
            continue
        try:
            done = _delete(core, conv["id"], "system", eligible=due, archived_before=cutoff,
                           marked_before=now - grace)
        except ChatError as exc:
            log.info("an archived conversation was not deleted this time: status=%s",
                     exc.status)
            continue
        gone += bool(done and done["deleted"])
    return gone
