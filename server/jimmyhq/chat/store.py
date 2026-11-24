"""The rules every conversation shares, and how what is on the record is read.

The first part is the same whatever is on or off the record: what may be
sent, the errors, the per-process service and the shapes the API shows. The
rest reads the part of a conversation the Tape holds. The two places a
conversation's words can be kept are behind ``backends``; ``conversations``
puts them together and holds the lifecycle.

What is on the record is a run of events sharing the Tape subject
``chat:<conversation id>``. Two readers fold them with the same small reducer:

- the **lens** keeps the conversation list (compact fields, never message
  text), so the list screen is one read however long the Tape is;
- **thread** folds one conversation's events (an indexed read by subject) to
  rebuild it with its messages. Text is only fetched for the page that is shown.
  Opening a conversation needs only its newest messages, so that page is read
  from the end of its events backwards (``newest_messages``) and stops when full.

Archiving hides a conversation from the main list and deletes nothing, and a
regenerated reply takes the place of the old one on screen while the old one
stays on the Tape. Only deleting the whole conversation removes anything from
the record (see ``deletion``): its events are erased, read back as unreadable,
and both readers here then find no conversation at all.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

from jimmyhq.core import Core
from jimmyhq.ids import now_ms, ulid
from jimmyhq.lens import Lens
from jimmyhq.models.shapes import EFFORTS, ROLES
from jimmyhq.models.validate import ID as PROVIDER_ID
from jimmyhq.models.validate import MAX_MODEL
from jimmyhq.tape.registry import Event

KINDS = ("chat.conversation.started", "chat.conversation.changed",
         "chat.conversation.archived", "chat.conversation.restored",
         "chat.conversation.mode", "chat.message.sent", "chat.message.answered")

ARCHIVED_CAP = 500  # archived conversations kept in the list; older ones open by id
ACTIVE_CAP = 1000  # conversations that can be open at once
MAX_EVENTS = 10_000  # one conversation's story stays one bounded read
READ_EVENTS = 200  # events read at a time when only the newest messages are wanted
INLINE_TEXT = 4096  # bytes; longer message text is stored in the Vault
MAX_TITLE = 120
MAX_QUERY = 120  # a title search is never longer than a title
CUT_TITLE = 60
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
DELETED = "chat.conversation.deleted"
MARKED = "chat.conversation.delete_marked"
# What the list folds beyond a conversation's own story: the marks the job
# that deletes old archived conversations leaves, and the two events that
# open and close a deletion, so one that was interrupted can be found again.
LENS_KINDS = (*KINDS, MARKED, DELETED, "tape.erased")
DEFAULT_TARGET = {"kind": "role", "role": "default"}
CHAT_ROLES = tuple(r for r in ROLES if r != "embeddings")

CONV_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
CURSOR = re.compile(r"(\d{1,16})-([0-9A-HJKMNP-TV-Z]{26})")

# What a stored reply says about itself, beyond its words.
REPLY_FIELDS = ("reply_to", "state", "error", "provider_id", "provider_type", "model", "local",
                "fallback_from", "fallback_reason", "input_tokens", "output_tokens",
                "duration_ms", "omitted", "memory", "web")

KEEP: Any = object()  # "leave this as it is", where None already means something

NOT_FOUND = "There is no conversation with this id. Reload the list."


def quiet_id() -> str:
    """An id for something off the record. It has the shape of every other id
    and none of the time: an ordinary id begins with the moment it was made,
    and one of these can end up on the Tape (a conversation later put on the
    record, a message a recorded reply answers), where it must not say when
    something was said off the record."""
    return ulid(int.from_bytes(os.urandom(6), "big"))


class ChatError(Exception):
    """Something asked for cannot be done. ``detail`` is one sentence that says
    what to do; ``status`` is the HTTP status a route should answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- checking what was sent ----------------------------------------------------


def storable(text: str) -> bool:
    """False for text that cannot be saved, such as half of a surrogate pair."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in text


def clean_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ChatError(422, "title must be text.")
    title = " ".join(value.split())
    if not storable(title) or any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        raise ChatError(422, "title contains characters that cannot be saved.")
    if len(title) > MAX_TITLE:
        raise ChatError(422, f"title cannot be longer than {MAX_TITLE} characters.")
    return title


def cut_title(text: str, limit: int = CUT_TITLE) -> str:
    """The start of a message as a title, cut at a word boundary."""
    words = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    if len(words) <= limit:
        return words
    head = words[:limit]
    space = head.rfind(" ")
    if space >= limit // 2:
        head = head[:space]
    return head.rstrip(" ,;:.-") + "…"


def clean_effort(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in EFFORTS:
        raise ChatError(422, f"effort must be one of: {', '.join(EFFORTS)}.")
    return value


def clean_target(value: Any) -> dict[str, Any]:
    """A target in its stored form, or a refusal that says what to send."""
    shape = ('target must look like {"kind": "role", "role": "default"} or '
             '{"kind": "model", "provider_id": "...", "model": "..."}.')
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ChatError(422, shape)
    which = value.get("kind")
    if which == "agent":
        raise ChatError(422, "Conversations with your agents arrive in a later release. "
                             "Choose a role or a model for now.")
    if which == "role":
        if set(value) != {"kind", "role"}:
            raise ChatError(422, shape)
        role = value["role"]
        if not isinstance(role, str) or role not in CHAT_ROLES:
            raise ChatError(422, f"target: the role must be one of: {', '.join(CHAT_ROLES)}.")
        return {"kind": "role", "role": role}
    if which == "model":
        if set(value) != {"kind", "provider_id", "model"}:
            raise ChatError(422, shape)
        provider_id, model = value["provider_id"], value["model"]
        if not isinstance(provider_id, str) or not PROVIDER_ID.match(provider_id):
            raise ChatError(422, "target: provider_id must be the id of a provider from "
                                 "Settings, Models and roles.")
        if not isinstance(model, str) or not storable(model):
            raise ChatError(422, "target: model must be the name of a model.")
        model = model.strip()
        if (not model or len(model) > MAX_MODEL
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in model)):
            raise ChatError(422, "target: model must be a model name of at most "
                                 f"{MAX_MODEL} characters on one line.")
        return {"kind": "model", "provider_id": provider_id, "model": model}
    raise ChatError(422, shape)


def clean_local_only(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ChatError(422, "local_only must be true or false.")
    return value


def clean_use_memory(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ChatError(422, "use_memory must be true or false.")
    return value


def clean_on_record(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ChatError(422, "on_record must be true or false.")
    return value


def clean_text(core: Core, value: Any) -> str:
    """A message's text: kept exactly as typed, but it must say something."""
    if not isinstance(value, str):
        raise ChatError(422, "text must be text.")
    if not storable(value):
        raise ChatError(422, "text contains characters that cannot be saved.")
    if not value.strip():
        raise ChatError(422, "text cannot be empty. Type a message first.")
    limit = core.settings.get("chat.max_message_chars")
    if len(value) > limit:
        raise ChatError(422, f"This message is {len(value)} characters long and the limit is "
                             f"{limit}. Shorten it, or raise the limit under Settings, Chat.")
    return value


# --- one conversation, folded --------------------------------------------------


def _target(payload: dict[str, Any]) -> dict[str, Any]:
    """A stored target without its unused fields."""
    which = payload["kind"]
    if which == "role":
        return {"kind": "role", "role": payload["role"]}
    if which == "model":
        return {"kind": "model", "provider_id": payload["provider_id"],
                "model": payload["model"]}
    return {"kind": "agent", "agent_id": payload["agent_id"]}


def _new(event: Event) -> dict[str, Any]:
    p = event.payload
    return {
        "id": p["conversation_id"], "title": p["title"], "title_source": p["title_source"],
        "target": _target(p["target"]), "effort": p["effort"], "local_only": p["local_only"],
        "use_memory": p.get("use_memory", True),
        "created": event.ts, "updated": event.ts, "messages": 0, "archived": False,
        "archived_at": None,  # when it was last archived, while it is
        "root": event.id,  # the starting event: the root of everything that follows
    }


def _restored(event: Event) -> dict[str, Any]:
    p = event.payload
    return {
        "id": p["conversation_id"], "title": p["title"], "title_source": p["title_source"],
        "target": _target(p["target"]), "effort": p["effort"], "local_only": p["local_only"],
        "use_memory": p.get("use_memory", True),
        "created": p["created"], "updated": p["updated"], "messages": p["messages"],
        "archived": False, "archived_at": None, "root": p["started_event"],
    }


def _advance(conv: dict[str, Any], event: Event) -> None:
    """Apply one later event to a conversation's list entry."""
    p = event.payload
    if event.kind == "chat.conversation.changed":
        if p["title"] is not None:
            conv["title"] = p["title"]
            conv["title_source"] = p["title_source"] or "owner"
        if p["target"] is not None:
            conv["target"] = _target(p["target"])
        if p["effort_cleared"]:
            conv["effort"] = None
        elif p["effort"] is not None:
            conv["effort"] = p["effort"]
        if p["local_only"] is not None:
            conv["local_only"] = p["local_only"]
        if p.get("use_memory") is not None:
            conv["use_memory"] = p["use_memory"]
    elif event.kind == "chat.conversation.archived":
        if not conv["archived"]:
            conv["archived_at"] = event.ts  # the clock for deleting archived ones starts here
        conv["archived"] = True
    elif event.kind == "chat.conversation.restored":
        conv["archived"], conv["archived_at"] = False, None
    elif event.kind == "chat.conversation.mode":
        conv["on_record"] = p["on_record"]
        # From now on part of it may be kept off the Tape. A conversation that
        # never had this event is read exactly as it always was.
        conv["mixed"] = True
    elif event.kind == "chat.message.sent":
        conv["messages"] += 1
        conv["updated"] = max(conv["updated"], event.ts)
        if p["title"] is not None and conv["title_source"] == "none":
            conv["title"], conv["title_source"] = p["title"], "cut"
    elif event.kind == "chat.message.answered":
        if p["replaces"] is None:
            conv["messages"] += 1
        conv["updated"] = max(conv["updated"], event.ts)


class ChatLens(Lens):
    """The conversation list. No message text, and a capped number of archived
    conversations, so the state stays small however much has been said. Of an
    archived conversation the list has let go, only its id and when it was
    archived are kept (``dropped``), so it can still be deleted when its time
    comes.

    ``marks`` is when each archived conversation was marked for deletion
    (id -> unix ms); restoring or archiving again takes a mark away.
    ``unfinished`` holds the ids of conversations whose events were erased
    and whose ``chat.conversation.deleted`` has not been printed yet: a
    deletion that still has cleaning up to do."""

    name = "chat"
    version = 3  # 3: marks for deletion, and deletions not yet finished
    kinds = LENS_KINDS

    def initial(self) -> dict[str, Any]:
        return {"conversations": {}, "dropped": {}, "marks": {}, "unfinished": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        convs = state["conversations"]
        if event.kind == "tape.erased":
            if event.payload["reason"] == DELETED and (event.subject or "").startswith("chat:"):
                state["unfinished"][(event.subject or "").removeprefix("chat:")] = event.ts
            return state
        conv_id = event.payload["conversation_id"]
        if event.kind == DELETED:
            state["unfinished"].pop(conv_id, None)
            return state
        if event.kind == MARKED:
            if event.payload["at"] is None:
                state["marks"].pop(conv_id, None)
            elif convs.get(conv_id, {}).get("archived") or conv_id in state["dropped"]:
                state["marks"][conv_id] = event.payload["at"]
            return state
        if event.kind in ("chat.conversation.archived", "chat.conversation.restored"):
            state["marks"].pop(conv_id, None)  # a new clock, or none: the mark is void
        if event.kind == "chat.conversation.started":
            convs.setdefault(conv_id, _new(event))
            return state
        conv = convs.get(conv_id)
        if conv is None:
            if event.kind == "chat.conversation.restored":
                convs[conv_id] = _restored(event)  # it had left the list; bring it back
                state["dropped"].pop(conv_id, None)
            return state
        _advance(conv, event)
        if event.kind == "chat.conversation.archived":
            archived = [c for c in convs.values() if c["archived"]]
            if len(archived) > ARCHIVED_CAP:
                oldest = min(archived, key=lambda c: (c["updated"], c["id"]))
                del convs[oldest["id"]]
                state["dropped"][oldest["id"]] = oldest["archived_at"]
        return state


    def erased(self, state: dict[str, Any], event: Event) -> dict[str, Any] | None:
        """A deleted conversation is taken out of the list without replaying
        everything ever said: all of its entry came from its own events, and
        those are what was erased. Any other erasure is replayed."""
        if event.payload["reason"] != "chat.conversation.deleted" or not (
                event.subject or "").startswith("chat:"):
            return None
        conv_id = (event.subject or "").removeprefix("chat:")
        state["conversations"].pop(conv_id, None)
        state["dropped"].pop(conv_id, None)
        state["marks"].pop(conv_id, None)
        return state


# --- the per-process service ---------------------------------------------------


class Service:
    def __init__(self) -> None:
        self.lens = ChatLens()
        # Check-then-print for one conversation must not interleave within
        # this process, and neither must taking the "one reply at a time" slot.
        self.lock = threading.Lock()
        # Replies being written by this process right now, by conversation id.
        # Not a record of anything: if the process dies the slots die with it,
        # and what the Tape holds is a message with no answer yet.
        self.runs: dict[str, Any] = {}


def svc(core: Core) -> Service:
    return core.service("chat", lambda _: Service())


def subject(conv_id: str) -> str:
    return f"chat:{conv_id}"


def events_of(core: Core, conv_id: Any) -> list[Event]:
    """One conversation's events, oldest first, starting with its first. Empty
    when there is no such conversation."""
    if not isinstance(conv_id, str) or not CONV_ID.fullmatch(conv_id):
        return []
    events = [e for e in core.tape.by_subject(subject(conv_id), limit=MAX_EVENTS)
              if e.readable and e.kind in KINDS and e.payload.get("conversation_id") == conv_id]
    if not events or events[0].kind != "chat.conversation.started":
        return []
    return events


def message_of(event: Event) -> dict[str, Any]:
    p = event.payload
    seq = event.seq or 0
    message = {
        "id": p["message_id"], "role": "user", "created": event.ts, "chars": p["chars"],
        "text": p["text"], "text_ref": p["text_ref"], "event": event.id, "seq": seq,
        "on_record": True, "order": (seq, 0, 0),
    }
    if event.kind == "chat.message.answered":
        # A reply from before memory (or web lookup) existed has no "memory"
        # (or "web"): it reads as None.
        message.update(role="assistant", **{name: p.get(name) for name in REPLY_FIELDS})
    return message


def thread(events: list[Event]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fold a conversation's events into its list entry and its visible
    messages, oldest first. A regenerated reply stands where the old one stood."""
    conv = _new(events[0])
    messages: list[dict[str, Any]] = []
    for event in events[1:]:
        if event.kind == "chat.conversation.started":
            continue
        _advance(conv, event)
        if event.kind == "chat.message.sent":
            messages.append(message_of(event))
        elif event.kind == "chat.message.answered":
            asked = event.payload["reply_to"]
            messages = [m for m in messages
                        if not (m["role"] == "assistant" and m["reply_to"] == asked)]
            at = next((i for i, m in enumerate(messages) if m["id"] == asked), None)
            if at is not None:
                messages.insert(at + 1, message_of(event))
    conv["messages"] = len(messages)  # counted, rather than tallied, when we have them all
    return conv, messages


def newest_messages(core: Core, conv_id: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
    """The newest ``limit`` visible messages, oldest first, and whether there
    are older ones. Reads the conversation's events from the newest backwards,
    a batch at a time, and stops as soon as the page is full, so opening a long
    conversation does not read its whole history. Gives what ``thread`` gives:
    going backwards, the first reply met to a message is its newest one, and
    it stands straight after the message it answers."""
    found: list[dict[str, Any]] = []  # newest first
    replies: dict[str, dict[str, Any] | None] = {}  # by the message answered; None once placed
    before, read = None, 0
    while len(found) <= limit and read < MAX_EVENTS:
        batch = core.tape.by_subject(subject(conv_id), limit=READ_EVENTS, before=before,
                                     newest_first=True)
        for event in batch:
            if (not event.readable or event.payload.get("conversation_id") != conv_id
                    or len(found) > limit):
                continue
            if event.kind == "chat.message.answered":
                replies.setdefault(event.payload["reply_to"], message_of(event))
            elif event.kind == "chat.message.sent":
                message = message_of(event)
                reply = replies.get(message["id"])
                if reply is not None:
                    found.append(reply)
                replies[message["id"]] = None  # a reply older than its message is not shown
                found.append(message)
        if len(batch) < READ_EVENTS:
            break
        before, read = batch[-1].seq, read + len(batch)
    return found[:limit][::-1], len(found) > limit


def text_of(core: Core, message: dict[str, Any]) -> str:
    if message["text_ref"]:
        try:
            return core.vault.get_text(message["text_ref"])
        except (OSError, ValueError):
            return ""  # the file is gone; the message is still shown, without its words
    return message["text"]


def stored_text(core: Core, text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > INLINE_TEXT:
        return {"text": "", "text_ref": core.vault.put_text(text), "chars": len(text)}
    return {"text": text, "text_ref": None, "chars": len(text)}


_HIDDEN = ("text_ref", "event", "seq", "chars", "order")


def public_message(core: Core, message: dict[str, Any], text: str | None = None) -> dict:
    """A message as the API shows it."""
    out = {k: v for k, v in message.items() if k not in _HIDDEN}
    out["text"] = text_of(core, message) if text is None else text
    return out


def delete_after_days(core: Core) -> int:
    """How long an archived conversation is kept, in days; 0 for ever."""
    return int(core.settings.get("chat.delete_archived_after_days") or 0)


def delete_grace_ms(core: Core) -> int:
    """How long a conversation stays marked for deletion before it goes."""
    return int(core.settings.get("chat.delete_grace_hours") or 24) * HOUR_MS


def deletion_policy(core: Core) -> dict[str, Any]:
    """What decides when archived conversations are deleted, read once for a
    caller that shows many: the two settings, the marks, and the time."""
    return {"days": delete_after_days(core), "grace_ms": delete_grace_ms(core),
            "marks": marks(core), "now": now_ms()}


def deletes_at(conv: dict[str, Any], policy: dict[str, Any]) -> int | None:
    """The earliest moment an archived conversation can be deleted for good,
    in unix milliseconds, or None when it will not be.

    It is deleted once it has been archived for the set number of days *and*
    has then stayed marked for the grace period. The job marks it within the
    hour of its becoming due; until it has, this counts the grace period from
    when it becomes due, or from now if it already is."""
    if not policy["days"] or not conv.get("archived") or conv.get("archived_at") is None:
        return None
    due = conv["archived_at"] + policy["days"] * DAY_MS
    marked = policy["marks"].get(conv["id"], conv.get("delete_marked_at"))
    if marked is None or marked < due:
        marked = max(due, policy["now"])
    return marked + policy["grace_ms"]


def public_conversation(core: Core, conv: dict[str, Any],
                        policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """A conversation as the API shows it. ``policy`` is ``deletion_policy``,
    passed by a caller that shows many at once."""
    out = {k: v for k, v in conv.items() if k not in ("root", "parts", "delete_marked_at")}
    out["streaming"] = conv["id"] in svc(core).runs
    out["archived_at"] = conv.get("archived_at") if conv.get("archived") else None
    out["deletes_at"] = None
    if conv.get("archived"):  # nothing else can be deleted by the job, so nothing else asks
        out["deletes_at"] = deletes_at(conv, policy or deletion_policy(core))
    return out


def listed(core: Core) -> dict[str, dict[str, Any]]:
    """The conversations the Tape holds, by id, as the lens keeps them."""
    return core.lenses.read(svc(core).lens)["conversations"]


def dropped(core: Core) -> dict[str, int]:
    """Archived conversations the list has let go: id -> when each was archived."""
    return core.lenses.read(svc(core).lens)["dropped"]


def marks(core: Core) -> dict[str, int]:
    """Archived conversations marked for deletion: id -> when each was marked."""
    return core.lenses.read(svc(core).lens)["marks"]


def unfinished(core: Core) -> dict[str, int]:
    """Conversations whose deletion has erased their events and has not yet
    finished cleaning up: id -> when the erase happened."""
    return core.lenses.read(svc(core).lens)["unfinished"]


def differing(conv: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Whatever in ``changes`` (title, title_source, target, effort,
    local_only, use_memory) really differs from a conversation, or one stored
    part of it, as it stands. A part kept since before the memory switch
    existed says nothing about it, which means on."""
    out: dict[str, Any] = {}
    if "title" in changes and changes["title"] != conv["title"]:
        out["title"] = changes["title"]
        out["title_source"] = changes.get("title_source", "owner")
    for name in ("target", "effort", "local_only"):
        if name in changes and changes[name] != conv[name]:
            out[name] = changes[name]
    if "use_memory" in changes and changes["use_memory"] != conv.get("use_memory", True):
        out["use_memory"] = changes["use_memory"]
    return out
