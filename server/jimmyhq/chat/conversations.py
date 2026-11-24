"""A conversation as the owner sees it: one thread, whatever is on or off
the record.

Reading puts the two stored parts together (see ``backends``). Writing
decides which part a change belongs to. The rules:

- **Messages** are kept according to the switch when they are sent.
- **The switch** is recorded only on the Tape. A conversation with no part
  there is off the record; turning it on the record for the first time is
  what starts its part there, with an empty title.
- **A title** given while off the record (by the owner, cut from a message,
  or written by a model) is kept off the record and is the one shown. A
  rename while on the record goes to the Tape and takes its place.
- **What answers** (the model, the effort, local only, use memory) holds no words, but
  when it is changed says when the owner was here. The Tape keeps it while
  the conversation is on the record; while it is off, a change is kept off
  the record too, and the Tape hears of it only when the switch goes back on.
- **Whether it is archived** the Tape keeps once it knows the conversation,
  the other store until then.
- **Who may see it**: what is off the record is the owner's alone. Reading
  with ``off_record=False`` (a read token) gives the Tape's part and nothing
  else, as if the other store were not there.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.chat import backends, recall, store
from jimmyhq.chat.offrecord import NO_TITLE
from jimmyhq.chat.store import KEEP, ChatError
from jimmyhq.core import Core
from jimmyhq.ids import ulid

NOTHING_OFF = ("Nothing in this conversation is off the record, so this has nothing to "
               "delete. To remove what is on the record too, delete the whole conversation "
               "for good.")
Conv = dict[str, Any]
_ANSWERS = ("target", "effort", "local_only", "use_memory")


class _Unseen:
    """Stands in for the off-the-record store for someone who may not see it."""

    def entries(self) -> list[Conv]:
        return []

    def find(self, conv_id: str) -> None:
        return None

    def story(self, conv_id: str) -> None:
        return None

    def newest(self, conv_id: str, limit: int) -> tuple[list[Conv], bool]:
        return [], False


_UNSEEN = _Unseen()


def _stores(core: Core, off_record: bool = True) -> tuple[Any, Any]:
    """The two places to read from; without ``off_record``, only the Tape's."""
    off, on = backends.both(core)
    return (off if off_record else _UNSEEN), on


def _busy(core: Core, conv: Conv, off_record: bool) -> bool:
    """Whether a reply is being written here, as far as this reader may know:
    the switch cannot be flipped while one is, so it says how that reply is kept."""
    return conv["id"] in store.svc(core).runs and (off_record or conv["on_record"])


def _shown(core: Core, conv: Conv, off_record: bool = True,
           policy: dict[str, Any] | None = None) -> Conv:
    return (store.public_conversation(core, conv, policy)
            | {"streaming": _busy(core, conv, off_record)})


# --- one conversation from its two parts -------------------------------------------


def _whole(on: Conv | None, off: Conv | None) -> Conv:
    """The conversation as shown, from the part on the Tape and the part off it."""
    main = on or off
    assert main is not None  # noqa: S101 - callers only pass a conversation that exists
    named = off if off is not None and (on is None or off["title_source"] != "none") else on
    assert named is not None  # noqa: S101
    # While the switch is off, what answers is whatever was last chosen off the record.
    answers = off if on is not None and off is not None and not on.get("on_record", True) else main
    return {
        "id": main["id"], "title": named["title"], "title_source": named["title_source"],
        "target": answers["target"], "effort": answers["effort"],
        "local_only": answers["local_only"],
        # One from before the memory switch existed says nothing, which means on.
        "use_memory": answers.get("use_memory", True),
        "created": min(p["created"] for p in (on, off) if p),
        "updated": max(p["updated"] for p in (on, off) if p),
        "messages": sum(p["messages"] for p in (on, off) if p),
        "archived": main["archived"],
        "archived_at": main.get("archived_at") if main["archived"] else None,
        # Only for one the Tape does not know; the Tape's marks are in the lens.
        "delete_marked_at": off.get("delete_marked_at") if off and not on else None,
        # How new messages are kept. Every conversation from before the switch
        # existed is on the Tape and says nothing, which means on.
        "on_record": on.get("on_record", True) if on else False,
        "recorded": on is not None,  # some of it is on the Tape, and stays there
        "off_record_messages": off["messages"] if off else 0,
        "off_record_title": bool(off and off["title_source"] != "none"),
        "root": on["root"] if on else None,  # the Tape event its receipts descend from
        "parts": {"on": on, "off": off},
    }


def _valid(conv_id: Any) -> bool:
    return isinstance(conv_id, str) and store.CONV_ID.fullmatch(conv_id) is not None


def find(core: Core, conv_id: Any, *, off_record: bool = True) -> Conv:
    """A conversation without its messages, or a 404."""
    if _valid(conv_id):
        off, on = _stores(core, off_record)
        parts = on.find(conv_id), off.find(conv_id)
        if any(parts):
            return _whole(*parts)
    raise ChatError(404, store.NOT_FOUND)


def _one_thread(on: list[Conv], off: list[Conv]) -> list[Conv]:
    """Both parts' messages in the order things happened. Where each place
    holds a reply to the same message, the later one is the one shown."""
    if not off:
        return on
    replies = {m["reply_to"]: m for m in on if m["role"] == "assistant"}
    hidden = set()
    for message in off:
        other = replies.get(message.get("reply_to"))
        if other is not None:
            hidden.add(min(message, other, key=lambda m: m["order"])["id"])
    return sorted((m for m in (*on, *off) if m["id"] not in hidden), key=lambda m: m["order"])


def story(core: Core, conv_id: Any, *, off_record: bool = True
          ) -> tuple[Conv, list[Conv], bool]:
    """A conversation, every visible message of it oldest first, and whether
    it has grown too long to add to. Or a 404."""
    if _valid(conv_id):
        off, on = _stores(core, off_record)
        recorded, unrecorded = on.story(conv_id), off.story(conv_id)
        if recorded or unrecorded:
            on_part, on_messages, on_full = recorded or (None, [], False)
            off_part, off_messages, off_full = unrecorded or (None, [], False)
            return (_whole(on_part, off_part), _one_thread(on_messages, off_messages),
                    on_full or off_full)
    raise ChatError(404, store.NOT_FOUND)


def _entries(core: Core, off_record: bool = True) -> list[Conv]:
    off, on = _stores(core, off_record)
    recorded = {c["id"]: c for c in on.entries()}
    unrecorded = {c["id"]: c for c in off.entries()}
    out = [_whole(conv, unrecorded.get(conv_id)) for conv_id, conv in recorded.items()]
    # One the Tape knows but its list has let go (archived long ago) is not
    # listed from here either; it still opens by id.
    out += [_whole(None, conv) for conv_id, conv in unrecorded.items()
            if conv_id not in recorded and not conv["recorded"]]
    return out


def _open_count(core: Core) -> int:
    return sum(1 for c in _entries(core) if not c["archived"])


def _page_size(limit: Any) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ChatError(422, "limit must be a whole number from 1 to 200.")


# --- reading -------------------------------------------------------------------------


def list_conversations(core: Core, *, archived: bool = False, limit: int = 50,
                       before: str | None = None, q: str | None = None,
                       off_record: bool = True) -> dict[str, Any]:
    """The conversation list, newest activity first. ``on_record`` on each
    says how its next message will be kept.

    ``off_record=False`` is for a reader who is not the owner: conversations
    that have never been on the record are left out, and the rest are shown
    as the Tape has them (its title, its count), with nothing kept off it.

    Returns ``{"conversations": [...], "next": cursor or None}``. Pass ``next``
    back as ``before`` for the page after. ``q`` keeps only conversations whose
    title contains every word in it, whatever the case; it searches the whole
    list, not one page.
    """
    _page_size(limit)
    if q is not None and (not isinstance(q, str) or len(q) > store.MAX_QUERY):
        raise ChatError(422, f"q must be text of at most {store.MAX_QUERY} characters.")
    convs = [c for c in _entries(core, off_record) if c["archived"] == bool(archived)]
    words = q.casefold().split() if q else []
    if words:
        convs = [c for c in convs if all(w in c["title"].casefold() for w in words)]
    convs.sort(key=lambda c: (c["updated"], c["id"]), reverse=True)
    if before is not None:
        cursor = store.CURSOR.fullmatch(before) if isinstance(before, str) else None
        if cursor is None:
            raise ChatError(422, "before must be the 'next' value from an earlier page.")
        edge = (int(cursor.group(1)), cursor.group(2))
        convs = [c for c in convs if (c["updated"], c["id"]) < edge]
    page = convs[:limit]
    more = len(convs) > limit
    policy = store.deletion_policy(core) if archived else None
    return {
        "conversations": [_shown(core, c, off_record, policy) for c in page],
        "next": f"{page[-1]['updated']}-{page[-1]['id']}" if more else None,
    }


def _newest(core: Core, conv: Conv, limit: int, off_record: bool
            ) -> tuple[list[Conv], bool] | None:
    """The newest page straight from the one place that holds the whole
    conversation, read from its end. None when it is spread over both."""
    off, on = _stores(core, off_record)
    parts = conv["parts"]
    if parts["off"] is None and not parts["on"].get("mixed"):
        return on.newest(conv["id"], limit)
    if parts["on"] is None:
        return off.newest(conv["id"], limit)
    return None


def get_conversation(core: Core, conv_id: Any, *, limit: int = 50,
                     before: str | None = None, off_record: bool = True) -> dict[str, Any]:
    """A conversation and one page of its messages, oldest first within the
    page. Each message says in ``on_record`` where it is kept.

    ``off_record=False`` is for a reader who is not the owner: only what is on
    the Tape is shown, and a conversation with nothing there is a 404.

    ``older`` is the cursor for the page before this one, or None at the start.
    """
    _page_size(limit)
    conv = find(core, conv_id, off_record=off_record) if before is None else None
    newest = _newest(core, conv, limit, off_record) if conv is not None else None
    if conv is not None and newest is not None:
        # What opening a conversation asks for: read from the end of its
        # story, not the whole of it.
        page, more = newest
        if not more:
            conv["messages"] = len(page)  # counted, rather than tallied, when we have them all
        last = page[-1] if page else None
    else:
        # An older page is found by the message it ends before, and a
        # conversation kept in both places is put together first, so the whole
        # (bounded) story is read.
        conv, messages, _ = story(core, conv_id, off_record=off_record)
        end = len(messages)
        if before is not None:
            end = next((i for i, m in enumerate(messages) if m["id"] == before), -1)
            if end < 0:
                raise ChatError(422, "before must be the 'older' value from an earlier page "
                                     "of this conversation. Reload the conversation.")
        start = max(0, end - limit)
        page, more = messages[start:end], start > 0
        last = messages[-1] if messages else None
        conv["messages"] = len(messages)
    streaming = _busy(core, conv, off_record)
    return {
        "conversation": _shown(core, conv, off_record),
        "messages": [store.public_message(core, m) for m in page],
        "older": page[0]["id"] if page and more else None,
        # True when the newest message is the person's and nothing is writing an
        # answer: the reply failed to start, or the server stopped part-way.
        "unanswered": bool(last and last["role"] == "user" and not streaming),
        "can_regenerate": last is not None and not streaming and not conv["archived"],
    }


def memory_of(core: Core, conv_id: Any, message_id: Any) -> dict[str, Any]:
    """The memory one reply was given, with the text each passage holds today.

    The reply keeps only where its passages are, so this reads them from the
    Library as they now stand: ``{"memory": ..., "passages": [{id, note,
    fact_id, chars, text, forgotten}]}``. A passage that has since been
    forgotten, or whose words have changed, has ``forgotten`` true and no text.
    For the owner only: it answers with words from memory."""
    _, messages, _ = story(core, conv_id)
    reply = next((m for m in messages if m["id"] == message_id and m["role"] == "assistant"),
                 None) if isinstance(message_id, str) else None
    if reply is None:
        raise ChatError(404, "There is no reply with this id in this conversation. Reload "
                             "the conversation.")
    used = reply.get("memory")
    try:
        passages = recall.passages_now(core, used)
    except Exception as exc:  # noqa: BLE001 - memory's trouble is said plainly, never a 500
        recall.log.warning("chat could not read memory passages: %s", type(exc).__name__)
        raise ChatError(503, "Memory could not be read just now. Try again in a moment."
                        ) from None
    return {"memory": used, "passages": passages}


# --- writing: which part a change belongs to -----------------------------------------


def apply(core: Core, conv: Conv, changes: dict[str, Any], *, actor: str,
          title_on_record: bool | None = None) -> None:
    """Keep whatever in ``changes`` (title, title_source, target, effort,
    local_only, use_memory) really differs, each in the place it belongs. A title goes to
    the Tape only when ``title_on_record`` (by default, the switch) says so."""
    off, on = backends.both(core)
    on_part = conv["parts"]["on"]
    title = {k: changes[k] for k in ("title", "title_source") if k in changes}
    rest = {k: v for k, v in changes.items() if k not in title}
    if title_on_record is None:
        title_on_record = conv["on_record"]
    if on_part is None:
        diff = store.differing(conv, changes)
        if diff:
            off.change(conv, diff, actor=actor)
        return
    if not conv["on_record"]:
        # Off the record, a new choice of what answers is kept off it too:
        # the moment it was made is part of what was done here.
        diff = store.differing(conv, rest)
        if diff:
            off.change(conv, diff, actor=actor)
        rest = {}
    diff = store.differing(on_part, rest | title if title_on_record else rest)
    if diff:
        on.change(on_part, diff, actor=actor)
    if not title:
        return
    if not title_on_record:
        diff = store.differing(conv, title)
        if diff:
            off.change(conv, diff, actor=actor)
    elif conv["off_record_title"]:
        # The new name is on the record and is the one to show from now on.
        off.change(conv, dict(NO_TITLE), actor=actor)


def new_id(on_record: bool) -> str:
    """An id for a message or a reply: one kept off the record does not carry
    the time it was made (see ``store.quiet_id``)."""
    return ulid() if on_record else store.quiet_id()


def say(core: Core, conv: Conv, text: str, *, actor: str) -> tuple[Conv, Conv]:
    """Keep a message from the person where the switch says. Returns the
    message and the conversation as it now stands."""
    here, _ = backends.pick(core, conv["on_record"])
    cut = store.cut_title(text) or None  # nothing printable names nothing; the next one will
    # The part on the Tape takes its title from the first message kept there;
    # off the record a message names the conversation only if nothing has.
    part = conv["parts"]["on"] if conv["on_record"] else conv
    title = cut if part["title_source"] == "none" else None
    message = here.say(conv, new_id(conv["on_record"]), text, title, actor=actor)
    if title and conv["title_source"] == "none":
        conv = conv | {"title": title, "title_source": "cut"}
    return message, conv


def answer(core: Core, conv: Conv, reply: dict[str, Any], text: str, *, on_record: bool,
           cause: str | None, replaces_on_record: bool = True) -> int:
    """Keep a reply that has ended, in the place decided when it began."""
    here, other = backends.pick(core, on_record)
    if on_record and reply["replaces"] is not None and not replaces_on_record:
        # The reply on screen until now was off the record, and the Tape is
        # told nothing of it: what this one replaces there is the last reply
        # the Tape itself holds to the same message, if it holds one.
        _, on = backends.both(core)
        reply = reply | {"replaces": on.last_reply(conv["id"], reply["reply_to"])}
    created = here.answer(conv, reply, text, cause=cause)
    other.retract(conv["id"], reply["reply_to"])
    return created


def _switch(core: Core, conv: Conv, on_record: bool, *, actor: str) -> None:
    off, on = backends.both(core)
    part, kept_off = conv["parts"]["on"], conv["parts"]["off"]
    now = {name: conv[name] for name in _ANSWERS}
    if part is None:
        # The first time on the record: the Tape learns of the conversation
        # now, and of nothing that was said or named before.
        part = on.create(conv["id"], NO_TITLE | now, actor=actor)
        off.mark_recorded(conv["id"])
        kept_off = None
    if kept_off is not None and not on_record:
        # Going off: what answers starts out as the Tape has it, whatever an
        # earlier time off the record left behind. Settled before the flip,
        # so a stop between the two leaves nothing stale in charge.
        diff = store.differing(kept_off, now)
        if diff:
            off.change(conv, diff, actor=actor)
    on.set_mode(part, on_record, actor=actor)
    if kept_off is not None and on_record:
        # Back on the record: what was chosen to answer while off it carries
        # on, and the Tape is told now, at the flip, not when it was chosen.
        diff = store.differing(part, now)
        if diff:
            on.change(part, diff, actor=actor)


# --- lifecycle -------------------------------------------------------------------------


def start_conversation(core: Core, *, title: str | None = None,
                       target: dict[str, Any] | None = None, effort: str | None = None,
                       local_only: bool | None = None, on_record: bool | None = None,
                       use_memory: bool | None = None,
                       actor: str = "owner") -> dict[str, Any]:
    """Start a conversation and return it. Nothing is sent to a model yet.

    ``target`` is ``{"kind": "role", "role": ...}`` or ``{"kind": "model",
    "provider_id": ..., "model": ...}``; it defaults to the default role.
    ``local_only`` defaults to the ``chat.local_only`` setting.
    ``on_record`` is how the conversation starts out: True keeps what is said
    on the Tape, False keeps it in the off-the-record store and leaves the
    Tape knowing nothing of the conversation. It defaults to the
    ``chat.on_record`` setting and can be flipped later with
    ``change_conversation``.
    ``use_memory`` is whether memory is searched before each reply; it
    defaults to the ``chat.use_memory`` setting.
    Raises ``ChatError`` with a sentence that says what to fix.
    """
    title = store.clean_title(title) if title is not None else ""
    target = store.clean_target(target) if target is not None else dict(store.DEFAULT_TARGET)
    effort = store.clean_effort(effort)
    if local_only is None:
        local_only = core.settings.get("chat.local_only")
    local_only = store.clean_local_only(local_only)
    if on_record is None:
        on_record = core.settings.get("chat.on_record")
    on_record = store.clean_on_record(on_record)
    if use_memory is None:
        use_memory = core.settings.get("chat.use_memory")
    use_memory = store.clean_use_memory(use_memory)
    off, on = backends.both(core)
    with store.svc(core).lock:
        if _open_count(core) >= store.ACTIVE_CAP:
            raise ChatError(409, f"There are already {store.ACTIVE_CAP} open conversations. "
                                 "Archive some before starting another.")
        conv_id = new_id(on_record)
        while on.find(conv_id) or off.find(conv_id):  # one id, one conversation
            conv_id = new_id(on_record)
        fields = {"title": title, "title_source": "owner" if title else "none",
                  "target": target, "effort": effort, "local_only": local_only,
                  "use_memory": use_memory}
        if on_record:
            on.create(conv_id, fields, actor=actor)
        else:
            off.create(conv_id, fields, actor=actor)
    return store.public_conversation(core, find(core, conv_id))


def change_conversation(core: Core, conv_id: Any, *, title: Any = KEEP, target: Any = KEEP,
                        effort: Any = KEEP, local_only: Any = KEEP, on_record: Any = KEEP,
                        use_memory: Any = KEEP, actor: str = "owner") -> dict[str, Any]:
    """Rename a conversation, change what answers in it from now on, or flip
    whether it is on the record.

    ``on_record`` decides how messages sent from now on are kept; what was
    already said stays where it is. Flipping is refused while a reply is being
    written. A title sent together with a flip is kept off the record.

    ``use_memory`` is recorded the way ``local_only`` is: on the Tape while
    the conversation is on the record, and while it is off, kept off the
    record and told to the Tape only when the switch goes back on."""
    changes: dict[str, Any] = {}
    if title is not KEEP:
        changes["title"] = store.clean_title(title)
        if not changes["title"]:
            raise ChatError(422, "title cannot be empty.")
    if target is not KEEP:
        changes["target"] = store.clean_target(target)
    if effort is not KEEP:
        changes["effort"] = store.clean_effort(effort)
    if local_only is not KEEP:
        changes["local_only"] = store.clean_local_only(local_only)
    if use_memory is not KEEP:
        changes["use_memory"] = store.clean_use_memory(use_memory)
    if on_record is not KEEP:
        on_record = store.clean_on_record(on_record)
    elif not changes:
        raise ChatError(422, "Send at least one of title, target, effort, local_only, "
                             "use_memory or on_record.")
    service = store.svc(core)
    with service.lock:
        conv = find(core, conv_id)
        flip = on_record is not KEEP and on_record != conv["on_record"]
        if conv["archived"] and (changes or flip):
            raise ChatError(409, "This conversation is archived. Restore it before changing it.")
        if flip:
            if conv["id"] in service.runs:
                raise ChatError(409, "A reply is still being written in this conversation. "
                                     "Stop it or wait for it to finish, then switch.")
            _switch(core, conv, on_record, actor=actor)
            conv = find(core, conv_id)
        # Named in the same breath as a flip, a title stays off the record.
        apply(core, conv, changes, actor=actor,
              title_on_record=conv["on_record"] and not flip)
    return store.public_conversation(core, find(core, conv_id))


def _set_archived(core: Core, conv: Conv, archived: bool, *, actor: str) -> None:
    off, on = backends.both(core)
    if conv["recorded"]:
        on.set_archived(conv["parts"]["on"], archived, actor=actor)
    else:
        off.set_archived(conv, archived, actor=actor)


def archive_conversation(core: Core, conv_id: Any, *, actor: str = "owner") -> dict[str, Any]:
    """Move a conversation out of the main list. Nothing is deleted by this.
    If the owner has set ``chat.delete_archived_after_days``, the conversation
    is marked for deletion that many days from now and deleted for good once
    the grace period has passed after that, unless it is restored first
    (``deletes_at`` on the answer says when)."""
    service = store.svc(core)
    with service.lock:
        conv = find(core, conv_id)
        if conv["id"] in service.runs:
            raise ChatError(409, "A reply is still being written in this conversation. Stop "
                                 "it or wait for it to finish, then archive.")
        if not conv["archived"]:
            _set_archived(core, conv, True, actor=actor)
            conv = find(core, conv_id)
    return store.public_conversation(core, conv)


def restore_conversation(core: Core, conv_id: Any, *, actor: str = "owner") -> dict[str, Any]:
    """Bring an archived conversation back to the main list."""
    with store.svc(core).lock:
        conv = find(core, conv_id)
        if conv["archived"]:
            if _open_count(core) >= store.ACTIVE_CAP:
                raise ChatError(409, f"There are already {store.ACTIVE_CAP} open conversations. "
                                     "Archive some before restoring this one.")
            _set_archived(core, conv, False, actor=actor)
    return store.public_conversation(core, conv | {"archived": False})


def delete_off_record(core: Core, conv_id: Any) -> dict[str, Any]:
    """Delete, for good, everything of a conversation that is off the record:
    those messages and replies, and a title given off the record. It cannot
    be undone, and nothing about it is recorded.

    What is on the record is untouched; ``delete_conversation`` is the one
    that removes that too. Returns
    ``{"deleted": n, "conversation": ...}``: how many messages went, and the
    conversation as it now stands, or None when nothing of it was on the
    record and so it is gone altogether."""
    service = store.svc(core)
    off, _ = backends.both(core)
    with service.lock:
        conv = find(core, conv_id)
        if conv["id"] in service.runs:
            raise ChatError(409, "A reply is still being written in this conversation. Stop "
                                 "it or wait for it to finish, then delete.")
        if conv["parts"]["off"] is None or (
                conv["recorded"] and not conv["off_record_messages"]
                and not conv["off_record_title"]):
            raise ChatError(409, NOTHING_OFF)
        deleted = off.forget(conv["id"])
    left = store.public_conversation(core, find(core, conv_id)) if conv["recorded"] else None
    return {"deleted": deleted, "conversation": left}
