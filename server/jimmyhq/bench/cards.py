"""Cards: the owner's one inbox for everything waiting on a decision.

A card's whole life is a handful of events sharing the Tape subject
``bench:<card id>``. Two readers fold them with the same small reducer:

- the **lens** keeps every open card in list form, plus a capped tail of recent
  decisions, so the Bench screen is one read however long the Tape is;
- **get_card** folds just one card's events (an indexed read by subject) to
  rebuild it in full, body and history included. That is how a decided or
  withdrawn card stays readable after it has left the lens.

Bodies never enter lens state: a short one stays in its event, a long one goes
to the Vault and the event keeps the reference.

House rule: only the owner's own click records a decision. ``decide`` always
prints as the owner and is called from the owner-only route, nowhere else.
"""

from __future__ import annotations

import json
import logging
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from jimmyhq.bench import rules
from jimmyhq.bench import snooze as snoozing
from jimmyhq.bench.rules import DAY_MS, BenchError
from jimmyhq.core import Core
from jimmyhq.ids import now_ms, ulid
from jimmyhq.lens import Lens
from jimmyhq.tape import TapeConflict
from jimmyhq.tape.registry import Event

log = logging.getLogger("jimmyhq.bench")

KINDS = ("bench.filed", "bench.updated", "bench.decided", "bench.snoozed",
         "bench.unsnoozed", "bench.withdrawn")

RECENT_CAP = 200  # decisions kept in lens state; the setting shows at most this many
DONE_MAX_DAYS = 365  # a "done" card nobody dismissed leaves the lens after this long
_RECENT_TEXT = 200  # a note or reason is shortened to this in the recent list
_MAX_SNOOZE_MS = 366 * DAY_MS
_HISTORY_LIMIT = 5000
_TRIES = 5  # times a write is retried when another process changes the card first

# Action ids with a fixed meaning. The ladder tallies decisions by these.
APPROVE, NOT_YET, NO, OK, UNDO = "approve", "not_yet", "no", "ok", "undo"

DEFAULT_ACTIONS: dict[str, list[dict[str, Any]]] = {
    "needs": [
        {"id": APPROVE, "label": "Approve", "style": "primary", "closes": True},
        {"id": NOT_YET, "label": "Not yet", "style": "default", "moves_to": "waiting"},
        {"id": NO, "label": "No", "style": "danger", "needs_reason": True, "closes": True},
    ],
    "done": [
        {"id": OK, "label": "Got it", "style": "primary", "closes": True},
    ],
}
UNDO_ACTION: dict[str, Any] = {"id": UNDO, "label": "Undo", "style": "default", "closes": True}

_ACTION_DEFAULTS: dict[str, Any] = {"style": "default", "needs_reason": False, "closes": False,
                                    "moves_to": None}
# The Bench's own buttons, in full. A filer may offer them, never reword them.
RESERVED_ACTIONS: dict[str, dict[str, Any]] = {
    a["id"]: {**_ACTION_DEFAULTS, **a}
    for a in (*DEFAULT_ACTIONS["needs"], *DEFAULT_ACTIONS["done"], UNDO_ACTION)
}


def _label_key(label: str) -> str:
    """A label reduced to what the eye reads: its letters and digits, with case,
    spacing, punctuation, width and invisible characters set aside."""
    folded = unicodedata.normalize("NFKC", label).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


_RESERVED_LABELS = {_label_key(a["label"]) for a in RESERVED_ACTIONS.values()}


def is_bench_action(action: Any) -> bool:
    """True when ``action`` is one of the Bench's own buttons, exactly as the Bench
    defines it (the fields left out are the usual ones). It carries no words of the
    filer's, which is why a private app may offer it."""
    if not isinstance(action, dict) or not isinstance(action.get("id"), str):
        return False
    own = RESERVED_ACTIONS.get(action["id"])
    return own is not None and {**_ACTION_DEFAULTS, **action} == own


def _says_what_it_does(action: dict[str, Any]) -> bool:
    """True when a stored action keeps to the rule ``NewCard`` enforces: one of
    the Bench's own ids exactly as the Bench defines it, or another id that does
    not borrow one of their labels."""
    own = RESERVED_ACTIONS.get(action["id"])
    if own is not None:
        return all(action.get(name) == value for name, value in own.items())
    return _label_key(action["label"]) not in _RESERVED_LABELS


# --- what a filer may send -----------------------------------------------------


def _prose(value: str) -> str:
    if not rules.storable(value):
        raise ValueError("contains characters that cannot be saved")
    return value


def _one_line(value: str) -> str:
    value = _prose(value).strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("must be a single line of ordinary text")
    return value


def _required(value: str) -> str:
    if not value:
        raise ValueError("cannot be empty")
    return value


def _shaped(shape, example: str):
    def check(value: str) -> str:
        if not shape.fullmatch(value):
            raise ValueError(f"must look like '{example}'")
        return value
    return check


def _file_name(value: str) -> str:
    # The name ends up in a download header and on the owner's disk, so it is
    # reduced to a plain file name here, once, rather than trusted later.
    value = "".join("_" if ch in '/\\:"' or ord(ch) < 32 or ord(ch) == 127 else ch
                    for ch in _prose(value)).strip().lstrip(".")
    return _required(value)


Line = Annotated[str, AfterValidator(_one_line)]
Prose = Annotated[str, AfterValidator(_prose)]
Name = Annotated[str, Field(max_length=60),
                 AfterValidator(_shaped(rules.NAME, "posts.publish"))]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionIn(_In):
    id: Annotated[str, Field(max_length=40), AfterValidator(_shaped(rules.SLUG, "approve"))]
    label: Annotated[Line, Field(max_length=40), AfterValidator(_required)]
    style: Literal["primary", "default", "danger"] = "default"
    needs_reason: bool = False
    closes: bool = False
    moves_to: Literal["needs", "done", "waiting"] | None = None


class DetailIn(_In):
    label: Annotated[Line, Field(max_length=80), AfterValidator(_required)]
    text: Annotated[Prose, Field(max_length=rules.MAX_DETAIL_TEXT)]


class FileIn(_In):
    name: Annotated[str, Field(max_length=200), AfterValidator(_file_name)]
    ref: Annotated[str, Field(max_length=71),
                   AfterValidator(_shaped(rules.VAULT_REF, "sha256:<64 hex digits>"))]


class LadderIn(_In):
    actor: Annotated[Line, Field(max_length=80), AfterValidator(_required)]
    tool: Name
    action: Name
    category: Annotated[str, Field(max_length=40), AfterValidator(_shaped(rules.SLUG, "publish"))]


class NewCard(_In):
    """Everything a filer chooses about a card. ``filed_by`` is not here: it is
    who is asking, never something the request gets to claim."""

    kind: Annotated[str, Field(max_length=80), AfterValidator(_shaped(rules.DOTTED, "posts.draft"))]
    source: Annotated[Line, Field(max_length=80), AfterValidator(_required)]
    title: Annotated[Line, Field(max_length=200), AfterValidator(_required)]
    summary: Annotated[Line, Field(max_length=300)] = ""
    body: Annotated[Prose, Field(max_length=rules.MAX_BODY)] = ""
    why: Annotated[Prose, Field(max_length=rules.MAX_NOTE)] | None = None
    details: list[DetailIn] = Field(default_factory=list, max_length=rules.MAX_DETAILS)
    files: list[FileIn] = Field(default_factory=list, max_length=rules.MAX_FILES)
    subject: Annotated[Line, Field(max_length=200),
                       AfterValidator(_shaped(rules.ENTITY, "task:<id>"))] | None = None
    lane: Literal["needs", "done"] = "needs"
    actions: list[ActionIn] | None = Field(None, max_length=rules.MAX_ACTIONS)
    undo: bool = False  # with the default actions on a "done" card, add an Undo button
    ladder: LadderIn | None = None

    @model_validator(mode="after")
    def _actions_make_sense(self) -> NewCard:
        actions = self.actions or []
        if len({a.id for a in actions}) != len(actions):
            raise ValueError("actions: each action needs its own id")
        for a in actions:
            # The owner reads the label and the record keeps the id, so the two
            # must always mean the same thing for the Bench's own buttons.
            own = RESERVED_ACTIONS.get(a.id)
            if own is not None and a.model_dump() != own:
                raise ValueError(
                    f"actions: {a.id!r} is one of the Bench's own buttons and keeps its "
                    f"wording and behaviour. Send it as {json.dumps(own)}, or use another id")
            if own is None and _label_key(a.label) in _RESERVED_LABELS:
                raise ValueError(
                    f"actions: the label {a.label!r} belongs to one of the Bench's own "
                    "buttons. Use that button's id (approve, not_yet, no, ok or undo), or "
                    "another label")
        if any(a.closes and a.moves_to for a in actions):
            raise ValueError("actions: an action either closes the card or moves it, not both")
        if actions and not any(a.closes for a in actions):
            raise ValueError("actions: at least one action must close the card, or it could "
                             "never leave the Bench")
        return self


def _sentence(exc: ValidationError) -> str:
    """The first problem as one readable sentence, without echoing what was sent."""
    first = exc.errors()[0] if exc.errors() else {}
    where = [p for p in first.get("loc", ()) if isinstance(p, str)]
    message = str(first.get("msg", "is not valid")).removeprefix("Value error, ")
    if first.get("type") == "value_error" and not where:
        return f"{message}."  # a whole-card rule already names what it is about
    return f"{where[-1] if where else 'The card'}: {message}."


# --- one card, folded ----------------------------------------------------------


def _new(event: Event) -> dict[str, Any]:
    p = event.payload
    return {
        "id": p["card_id"],
        "kind": p["kind"],
        "source": p["source"],
        "title": p["title"],
        "summary": p["summary"],
        "filed_by": event.actor,
        "subject": p["subject"],
        "lane": p["lane"],
        "actions": p["actions"],
        "filed": event.ts,
        "activity": event.ts,
        "needs_since": event.ts if p["lane"] == "needs" else None,
        "snoozed_until": None,
        "files": len(p["files"]),
        "updates": 0,
        "ladder": p["ladder"],
        "status": "open",
        "event": event.id,  # the filing event: the cause of everything that follows
        "root": event.root or event.id,
    }


def _advance(card: dict[str, Any], event: Event) -> bool:
    """Apply one later event to a card. False when it changes nothing, as with
    anything that arrives after the card has closed."""
    if card["status"] != "open":
        return False
    p, ts = event.payload, event.ts
    # Who may write each kind is checked here as well as in the functions, so an
    # event printed some other way cannot decide a card or speak for its filer.
    if event.kind in ("bench.updated", "bench.withdrawn"):
        if event.actor not in ("owner", card["filed_by"]):
            return False
    elif event.actor != "owner":
        return False
    if event.kind == "bench.updated":
        for name in ("title", "summary"):
            if p[name] is not None:
                card[name] = p[name]
        card["updates"] += 1
        card["activity"] = ts
        card["snoozed_until"] = None  # news is worth seeing now
        if card["lane"] == "waiting":
            card["lane"], card["needs_since"] = "needs", ts
    elif event.kind == "bench.decided":
        card["activity"] = ts
        card["snoozed_until"] = None
        if p["closes"]:
            card["status"] = "decided"
        elif p["moves_to"]:
            card["lane"] = p["moves_to"]
            if p["moves_to"] == "needs":
                card["needs_since"] = ts
    elif event.kind == "bench.snoozed":
        if card["lane"] != "needs":
            return False
        card["snoozed_until"] = p["until"]
    elif event.kind == "bench.unsnoozed":
        if card["snoozed_until"] is None:
            return False
        card["snoozed_until"] = None
    elif event.kind == "bench.withdrawn":
        card["status"] = "withdrawn"
        card["activity"] = ts
    else:
        return False
    return True


class BenchLens(Lens):
    """Open cards in list form, and the last decisions. Nothing else, so the
    state stays small however many cards have come and gone."""

    name = "bench"
    version = 1
    kinds = KINDS

    def initial(self) -> dict[str, Any]:
        return {"open": {}, "recent": [], "swept": 0}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        self._sweep(state, event.ts)
        cards = state["open"]
        card_id = event.payload["card_id"]
        if event.kind == "bench.filed":
            cards.setdefault(card_id, _new(event))
            return state
        card = cards.get(card_id)
        if card is None or not _advance(card, event):
            return state
        if event.kind == "bench.decided":
            p = event.payload
            state["recent"].append({
                "card_id": card_id, "title": card["title"], "kind": card["kind"],
                "source": card["source"], "action": p["action"], "label": p["label"],
                "note": _short(p["note"]), "reason": _short(p["reason"]),
                "ts": event.ts, "closed": p["closes"],
            })
            del state["recent"][:-RECENT_CAP]
        if card["status"] != "open":
            del cards[card_id]
        return state

    @staticmethod
    def _sweep(state: dict[str, Any], ts: int) -> None:
        """Once a day of Tape time, drop "done" cards nobody dismissed for a
        year. Uses the event's own time, so the fold stays pure."""
        day = ts // DAY_MS
        if day <= state["swept"]:
            return
        state["swept"] = day
        cutoff = ts - DONE_MAX_DAYS * DAY_MS
        for card_id in [i for i, c in state["open"].items()
                        if c["lane"] == "done" and c["activity"] < cutoff]:
            del state["open"][card_id]


def _short(text: str | None) -> str | None:
    if text is None or len(text) <= _RECENT_TEXT:
        return text
    return text[:_RECENT_TEXT - 1] + "…"


# --- the per-process service ---------------------------------------------------


class _Service:
    def __init__(self) -> None:
        self.lens = BenchLens()
        # Check-then-print for one card must not interleave within this process,
        # or two quick clicks could both find the card open.
        self.lock = threading.Lock()


def _svc(core: Core) -> _Service:
    return core.service("bench", lambda _: _Service())


def _subject(card_id: str) -> str:
    return f"bench:{card_id}"


def _events(core: Core, card_id: str) -> list[Event]:
    """One card's events, oldest first, starting with its filing. Empty when
    there is no such card."""
    if not isinstance(card_id, str) or not rules.CARD_ID.fullmatch(card_id):
        return []
    events = [e for e in core.tape.by_subject(_subject(card_id), limit=_HISTORY_LIMIT)
              if e.readable and e.kind in KINDS and e.payload.get("card_id") == card_id]
    if not events or events[0].kind != "bench.filed":
        return []
    return events


def _open(core: Core, card_id: str) -> dict[str, Any]:
    """The open card in list form, or the right refusal."""
    card = core.lenses.read(_svc(core).lens)["open"].get(card_id)
    if card is None:
        if _events(core, card_id):
            raise BenchError(409, _CLOSED)
        raise BenchError(404, "There is no card with this id. Reload the Bench.")
    return card


_CLOSED = ("This card is already closed, so nothing more can be done with it. Reload the "
           "Bench to see what is still open.")


def _write[T](core: Core, card_id: str, attempt: Callable[[dict[str, Any], int], T]) -> T:
    """Check a card and print about it as one step, across processes.

    ``attempt(card, seq)`` checks the open card and prints with ``_print``,
    passing ``seq`` on. The Tape refuses that print if anything else was printed
    about the card after ``seq`` was read, so an event that lost a race (the
    owner approves in the web process while the worker withdraws) is never
    written. The check then runs again on the card as it now is, and says what
    is wrong with it.
    """
    with _svc(core).lock:
        for _ in range(_TRIES):
            # Read before the lens, so the card we check is at least this new.
            seq = core.tape.subject_last_seq(_subject(card_id))
            card = _open(core, card_id)
            try:
                return attempt(card, seq)
            except TapeConflict:
                continue
    raise BenchError(409, "This card is being changed by something else right now. Wait a "
                          "moment, read it again and try once more.")


def _print(core: Core, kind: str, card: dict[str, Any], payload: dict, *, actor: str,
           seq: int) -> Event:
    return core.tape.print(kind, {"card_id": card["id"], **payload}, actor=actor,
                           subject=_subject(card["id"]), cause=card["event"], root=card["root"],
                           expect_subject_seq=seq)


def _stored_body(core: Core, body: str) -> dict[str, Any]:
    if len(body.encode("utf-8")) > rules.INLINE_BODY:
        return {"body": "", "body_ref": core.vault.put_text(body)}
    return {"body": body, "body_ref": None}


# --- filing, updating, withdrawing --------------------------------------------


def file_card(core: Core, *, filed_by: str, cause: str | None = None, root: str | None = None,
              **fields: Any) -> str:
    """Put a card on the Bench and return its id.

    ``fields`` are those of ``NewCard``: kind, source and title are required;
    summary, body, why, details, files, subject, lane ("needs" or "done"),
    actions, undo and ladder are optional. Raises ``BenchError`` (status 422)
    with a sentence that says what to fix.
    """
    try:
        new = NewCard(**fields)
    except ValidationError as exc:
        raise BenchError(422, _sentence(exc)) from None
    filed_by = rules.line(filed_by, "filed_by", 80)
    for file in new.files:
        if not core.vault.has(file.ref):
            raise BenchError(422, f"The file {file.name!r} is not in the Vault. Store it "
                                  "there first and pass the sha256 reference it returns.")
    if new.actions:
        actions = [a.model_dump() for a in new.actions]
    else:
        actions = [dict(a) for a in DEFAULT_ACTIONS[new.lane]]
        if new.undo and new.lane == "done":
            actions.append(dict(UNDO_ACTION))
    card_id = ulid()
    core.tape.print("bench.filed", {
        "card_id": card_id, "kind": new.kind, "source": new.source, "title": new.title,
        "summary": new.summary, **_stored_body(core, new.body), "why": new.why or None,
        "details": [d.model_dump() for d in new.details],
        "files": [f.model_dump() for f in new.files],
        "subject": new.subject, "lane": new.lane, "actions": actions,
        "ladder": new.ladder.model_dump() if new.ladder else None,
    }, actor=filed_by, subject=_subject(card_id), cause=cause, root=root)
    return card_id


def _may_change(card: dict[str, Any], by: str) -> None:
    if by not in ("owner", card["filed_by"]):
        raise BenchError(403, "Only whoever filed this card, or the owner, can change it.")


def update_card(core: Core, card_id: str, *, by: str, note: str | None = None,
                title: str | None = None, summary: str | None = None,
                body: str | None = None) -> None:
    """Add a note to a card or revise it. The card's last activity moves, and a
    card that was waiting comes back to "needs". Only its filer or the owner."""
    payload: dict[str, Any] = {
        "note": rules.clean(note, "note", rules.MAX_NOTE),
        "title": None if title is None else rules.line(title, "title", 200),
        "summary": None, "body": None, "body_ref": None,
    }
    if summary is not None:
        blank = isinstance(summary, str) and not summary.strip()
        payload["summary"] = "" if blank else rules.line(summary, "summary", 300)
    if body is not None:
        if not isinstance(body, str) or not rules.storable(body):
            raise BenchError(422, "body contains characters that cannot be saved.")
        if len(body) > rules.MAX_BODY:
            raise BenchError(422, f"body cannot be longer than {rules.MAX_BODY} characters.")
    if body is None and all(v is None for v in payload.values()):
        raise BenchError(422, "An update needs a note, or a revised title, summary or body.")
    def attempt(card: dict[str, Any], seq: int) -> None:
        _may_change(card, by)
        if card["updates"] >= rules.MAX_UPDATES:
            raise BenchError(409, "This card has been updated too many times. Withdraw it "
                                  "and file a new one.")
        if body is not None:
            payload.update(_stored_body(core, body))
        _print(core, "bench.updated", card, payload, actor=by, seq=seq)

    _write(core, card_id, attempt)


def withdraw_card(core: Core, card_id: str, *, by: str, note: str | None = None) -> None:
    """Take a card back before it is decided. Only its filer or the owner."""
    note = rules.clean(note, "note", rules.MAX_NOTE)
    def attempt(card: dict[str, Any], seq: int) -> None:
        _may_change(card, by)
        _print(core, "bench.withdrawn", card, {"note": note}, actor=by, seq=seq)

    _write(core, card_id, attempt)


# --- the owner's side ----------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """What an ``on_decision`` callback is handed."""

    card_id: str
    card_kind: str
    action: str
    note: str | None
    reason: str | None
    closes: bool
    moves_to: str | None
    subject: str | None
    ladder: dict[str, str] | None
    # Who filed the card. Any write token can file a card under any kind, so
    # check this (or that the card is one you filed) before acting on subject.
    filed_by: str | None
    # How many times the card had been updated when the owner clicked: the
    # revision that was decided. None on decisions from before it was recorded.
    updates: int | None
    event_id: str  # the bench.decided event, to use as the cause of what follows
    ts: int


_handlers: list[tuple[str, Callable[[Core, Decision], None]]] = []


def on_decision(kind_prefix: str,
                callback: Callable[[Core, Decision], None]) -> Callable[[], None]:
    """Call ``callback(core, decision)`` after the owner decides a card whose
    kind starts with ``kind_prefix``. Returns a function that removes it.

    This runs only in the process where the click arrived (the web process),
    and only while it is up. Anything that must not be missed, or that runs in
    the worker, should read ``bench.decided`` events from the Tape instead.
    """
    entry = (kind_prefix, callback)
    _handlers.append(entry)

    def remove() -> None:
        if entry in _handlers:
            _handlers.remove(entry)

    return remove


def _notify(core: Core, event: Event) -> None:
    p = event.payload
    decision = Decision(
        card_id=p["card_id"], card_kind=p["card_kind"], action=p["action"], note=p["note"],
        reason=p["reason"], closes=p["closes"], moves_to=p["moves_to"], subject=p["subject"],
        ladder=p["ladder"], filed_by=p.get("filed_by"), updates=p.get("updates"),
        event_id=event.id, ts=event.ts,
    )
    for prefix, callback in list(_handlers):
        if decision.card_kind.startswith(prefix):
            try:
                callback(core, decision)
            except Exception:  # noqa: BLE001 - the decision is already on the record
                log.exception("a bench decision handler for %r failed", prefix)


def decide(core: Core, card_id: str, *, action: str, note: str | None = None,
           reason: str | None = None, seen_updates: int | None = None) -> dict[str, Any]:
    """Record the owner's click. Called by the owner-only route and nothing
    else: an agent, an app or a token never decides.

    ``seen_updates`` is the card's ``updates`` count as the owner saw it. The
    click is for the words that were on screen, so it is refused when the card
    has been updated since."""
    note = rules.clean(note, "note", rules.MAX_NOTE)
    reason = rules.clean(reason, "reason", rules.MAX_NOTE)

    def attempt(card: dict[str, Any], seq: int) -> Event:
        if seen_updates is not None and seen_updates != card["updates"]:
            raise BenchError(409, "This card changed since you read it. Read it again, then "
                                  "decide.")
        if not all(_says_what_it_does(a) for a in card["actions"]):
            # Filed before the Bench checked its buttons: a click here could be
            # recorded as the opposite of what the button said.
            raise BenchError(409, "This card was filed with buttons that may not mean what "
                                  "they say, so it cannot be decided. Ask whoever filed it "
                                  "to withdraw it and file it again, or withdraw it "
                                  "yourself with POST /api/bench/{id}/withdraw.")
        chosen = next((a for a in card["actions"] if a["id"] == action), None)
        if chosen is None:
            offered = ", ".join(a["id"] for a in card["actions"])
            raise BenchError(422, f"This card has no such action. Choose one of: {offered}.")
        if chosen["needs_reason"] and not reason:
            raise BenchError(422, f"A reason is needed for {chosen['label']!r}. Say why in a "
                                  "sentence and try again.")
        return _print(core, "bench.decided", card, {
            "card_kind": card["kind"], "subject": card["subject"], "action": chosen["id"],
            "label": chosen["label"], "note": note, "reason": reason,
            "closes": chosen["closes"], "moves_to": chosen["moves_to"], "ladder": card["ladder"],
            "filed_by": card["filed_by"], "updates": card["updates"],
            "keeps_level": not core.settings.get("bench.undo_drops_alone"),
        }, actor="owner", seq=seq)

    event = _write(core, card_id, attempt)
    _notify(core, event)
    return get_card(core, card_id) or {}


def snooze(core: Core, card_id: str, until: int | None = None) -> dict[str, Any]:
    """Hide a card from "needs" until a time. It comes back on its own."""
    now = now_ms()
    if until is None:
        until = now + core.settings.get("bench.snooze_hours") * 60 * 60 * 1000
    if isinstance(until, bool) or not isinstance(until, int):
        raise BenchError(422, "until must be a time in unix milliseconds.")
    if until <= now:
        raise BenchError(422, "A card can only be snoozed until a time in the future.")
    if until > now + _MAX_SNOOZE_MS:
        raise BenchError(422, "A card cannot be snoozed for more than a year.")
    def attempt(card: dict[str, Any], seq: int) -> None:
        if card["lane"] != "needs":
            raise BenchError(409, "Only a card that is waiting on you can be snoozed.")
        _print(core, "bench.snoozed", card, {"until": until}, actor="owner", seq=seq)

    _write(core, card_id, attempt)
    return get_card(core, card_id) or {}


def unsnooze(core: Core, card_id: str) -> dict[str, Any]:
    def attempt(card: dict[str, Any], seq: int) -> None:
        if card["snoozed_until"] is not None:  # already back: nothing to record
            _print(core, "bench.unsnoozed", card, {}, actor="owner", seq=seq)

    _write(core, card_id, attempt)
    return get_card(core, card_id) or {}


# --- reading -------------------------------------------------------------------


def _public(card: dict[str, Any], now: int) -> dict[str, Any]:
    out = {k: v for k, v in card.items() if k not in ("event", "root")}
    out["snoozed"] = bool(card["snoozed_until"] and card["snoozed_until"] > now)
    if not out["snoozed"]:
        out["snoozed_until"] = None
    return out


def board(core: Core) -> dict[str, Any]:
    """The Bench screen in one read: three lanes of cards in list form, counts,
    the recent decisions, and the quick snooze choices on offer right now."""
    state = core.lenses.read(_svc(core).lens)
    now = now_ms()
    expire_days = core.settings.get("bench.done_expire_days")
    keep = core.settings.get("bench.recent_decisions")
    lanes: dict[str, list[dict[str, Any]]] = {lane: [] for lane in rules.LANES}
    by_kind: dict[str, int] = {}
    by_source: dict[str, int] = {}
    oldest: int | None = None
    for card in state["open"].values():
        view = _public(card, now)
        if view["lane"] == "done" and expire_days and card["activity"] < now - expire_days * DAY_MS:
            continue  # still readable by id, no longer in the way
        if view["snoozed"]:
            lanes["waiting"].append(view)  # shown apart, so it can be brought back
            continue
        lanes[view["lane"]].append(view)
        if view["lane"] == "needs":
            by_kind[view["kind"]] = by_kind.get(view["kind"], 0) + 1
            by_source[view["source"]] = by_source.get(view["source"], 0) + 1
            # A card that came back from a snooze has waited since it came back.
            since = max(card["needs_since"] or card["filed"], card["snoozed_until"] or 0)
            oldest = since if oldest is None else min(oldest, since)
    for cards in lanes.values():
        cards.sort(key=lambda c: (c["activity"], c["id"]), reverse=True)
    return {
        "lanes": lanes,
        "counts": {lane: len(cards) for lane, cards in lanes.items()},
        "needs_by_kind": by_kind,
        "needs_by_source": by_source,
        "oldest_needs_since": oldest,
        "oldest_needs_ms": None if oldest is None else max(0, now - oldest),
        "recent": state["recent"][::-1][:keep],
        "now": now,
        # Worked out here, from the owner's settings and time zone, so every
        # browser offers the same times whatever its own clock and zone say.
        "snooze_choices": snoozing.choices(core, now),
        "timezone": snoozing.zone_name(core),
    }


def read_file(core: Core, card_id: str, index: int) -> tuple[str, bytes]:
    """The name and bytes of one file attached to a card. Only what the card
    itself lists can be fetched this way. That keeps the route from being a door
    to the Vault only as long as whoever filed the card may read every blob it
    lists: ``file_card`` takes any reference that exists, so its callers must be
    trusted code, and the HTTP route refuses attachments from tokens."""
    events = _events(core, card_id)
    if not events:
        raise BenchError(404, "There is no card with this id. Reload the Bench.")
    files = events[0].payload["files"]
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(files):
        raise BenchError(404, "This card has no file at that position. Reload the card.")
    file = files[index]
    try:
        return file["name"], core.vault.get(file["ref"])
    except (ValueError, OSError):
        raise BenchError(404, "This file is no longer in the Vault. Ask whoever filed the "
                              "card to attach it again.") from None


def get_card(core: Core, card_id: str) -> dict[str, Any] | None:
    """The whole card: list fields, body, why, details, files and history.
    None when there is no such card. Works for closed cards too."""
    events = _events(core, card_id)
    if not events:
        return None
    filed = events[0]
    card = _new(filed)
    body, body_ref = filed.payload["body"], filed.payload["body_ref"]
    history: list[dict[str, Any]] = [{"type": "filed", "ts": filed.ts, "actor": filed.actor}]
    decision = None
    for event in events[1:]:
        if not _advance(card, event):
            continue
        p = event.payload
        entry: dict[str, Any] = {"type": event.kind.removeprefix("bench."), "ts": event.ts,
                                 "actor": event.actor}
        if event.kind == "bench.updated":
            revised = [n for n in ("title", "summary") if p[n] is not None]
            if p["body"] is not None or p["body_ref"] is not None:
                body, body_ref = p["body"] or "", p["body_ref"]
                revised.append("body")
            entry.update(note=p["note"], revised=revised)
        elif event.kind == "bench.decided":
            entry.update(action=p["action"], label=p["label"], note=p["note"],
                         reason=p["reason"], closes=p["closes"], moves_to=p["moves_to"])
            decision = entry
        elif event.kind == "bench.snoozed":
            entry["until"] = p["until"]
        elif event.kind == "bench.withdrawn":
            entry["note"] = p["note"]
        history.append(entry)
    # Read after the events, so the lens has seen at least as much as we have.
    if card["status"] == "open" and card_id not in core.lenses.read(_svc(core).lens)["open"]:
        card["status"] = "expired"  # a "done" card nobody dismissed for a year
    body_missing = False
    if body_ref:
        try:
            body = core.vault.get_text(body_ref)
        except (ValueError, OSError):
            body, body_missing = "", True
    out = _public(card, now_ms())
    out.update(
        body=body, body_missing=body_missing, why=filed.payload["why"],
        details=filed.payload["details"],
        files=[{"index": i, "name": f["name"]} for i, f in enumerate(filed.payload["files"])],
        history=history, decision=decision,
    )
    return out
