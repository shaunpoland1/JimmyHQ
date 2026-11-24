"""The executor: what happens when the owner decides a request, and the cards that say
what was done.

An action that needs the owner's yes is frozen when it is asked (``hand.proposed``, the
arguments in ``hands.db``) and shown on a Bench card. This module reads the owner's
decisions from the Tape by a stored cursor and acts on each one:

- **Approve**: ``hands.perform(fingerprint)`` runs precisely the frozen request. Nothing
  is asked of a model and nothing is rebuilt. If the rule, the action or the account
  changed since it was asked, it is refused there, and a card says so.
- **No, or not now**: ``hands.decline`` closes the request and erases its arguments.
- **Undo** on a "done" card: the provider is asked to take the run back.

It runs in the worker, the one process that is always up beside the web server. More
than one process may read the same decision (a second worker, a restart that replays
the cursor): the run ledger takes a request's fingerprint as its key, and a key starts
once, ever, so the provider is called once. What became of a request is printed once
too (``hand.outcome``), guarded by the Tape's own check on the request's subject.

A run found ``started`` long after it should have ended was cut short. Nobody knows
whether it happened, so it is never tried again: it becomes a card for the owner and an
"outcome unknown" for whoever asked.

Every card here is written from the receipt: names, sizes, counts and the provider's
reference. Never an argument's value.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq import bench
from jimmyhq.bench import cards as bench_cards
from jimmyhq.bookmark import within_tape
from jimmyhq.core import Core
from jimmyhq.hands import calls, rules, shape
from jimmyhq.hands import store as ledger
from jimmyhq.hands.rules import HandRefused
from jimmyhq.hands.service import Hands
from jimmyhq.tape.store import TapeConflict

log = logging.getLogger("jimmyhq.hands")

REQUEST = "village.hand"  # the card a request is shown on, filed for the agent
DONE, NOT_DONE, UNKNOWN = "hand.done", "hand.not_done", "hand.unknown"
OUTCOME = "hand.outcome"
SOURCE = "Integrations"
CURSOR = "execute"
KINDS = ("bench.decided", "bench.withdrawn")
BATCH = 200
NOT_NOW = "not_now"
CHECKED = "checked"
ACTOR = "hands"
_FILING = "filing"
_TRIES = 4

# The buttons on a request. "Not this time" closes it like a No, without needing a
# reason: the agent may ask again later.
REQUEST_ACTIONS: list[dict[str, Any]] = [
    dict(bench_cards.RESERVED_ACTIONS[bench_cards.APPROVE]),
    {"id": NOT_NOW, "label": "Not this time", "style": "default", "needs_reason": False,
     "closes": True, "moves_to": None},
    dict(bench_cards.RESERVED_ACTIONS[bench_cards.NO]),
]
UNKNOWN_ACTIONS: list[dict[str, Any]] = [
    {"id": CHECKED, "label": "I have checked", "style": "primary", "needs_reason": False,
     "closes": True, "moves_to": None},
]


def _hands(core: Core) -> Hands:
    return core.service("hands", Hands)


def _who(agent: str) -> str:
    return f"agent:{agent}"


def _label(h: Hands, agent: str, account: str, action: str, tool: str) -> str:
    rule = h.rule(agent, account, action)
    return shape.line((rule or {}).get("label") or tool, 120)


def _sent(receipt: dict[str, Any]) -> str:
    sizes = receipt.get("argument_chars") or {}
    names = receipt.get("argument_names") or []
    if not names:
        return "No arguments."
    return ", ".join(f"{name} ({sizes.get(name, 0)} characters)" for name in names)


def receipt_details(run: dict[str, Any]) -> list[dict[str, str]]:
    """Rule ``hand_card_from_receipt``: what a card about a run may show. The account,
    the action, the names and sizes of what was sent, how many receive it and the
    provider's reference. Never a value: those were erased when the run was done."""
    receipt = run.get("receipt") or {}
    out = [{"label": "Account", "text": run["account"]},
           {"label": "Action", "text": run["action"]},
           {"label": "What was sent", "text": _sent(receipt)}]
    if receipt.get("recipients"):
        out.append({"label": "Recipients", "text": str(receipt["recipients"])})
    ref = calls.safe_ref(run.get("ref"))
    if ref:
        out.append({"label": "The provider's reference", "text": ref})
    return out


def can_undo(h: Hands, run: dict[str, Any]) -> bool:
    """Whether a finished run can be taken back: it has the provider's reference, and
    the account's list of actions says this one can be undone."""
    if run["status"] != ledger.DONE or not run.get("ref"):
        return False
    cached = h.store.actions(run["account"])
    return any(a.get("name") == run["action"] and a.get("undo") for a in (cached or [[]])[0])


# --- telling whoever asked, once ----------------------------------------------------------


def tell(core: Core, run_or_row: dict[str, Any], outcome: str, *, subject: str,
         fingerprint: str | None = None, run_id: str | None = None,
         card_id: str | None = None, ref: str | None = None, reason: str | None = None,
         note: str | None = None, cause: str | None = None,
         same: tuple[str, ...] | None = None) -> bool:
    """Rule ``hand_outcome_told_once``: print what became of a request or a run. False
    when it was already said, by this process or another: the Tape refuses a second
    print about a subject that changed since it was read, and the check runs again."""
    for _ in range(_TRIES):
        seq = core.tape.subject_last_seq(subject)
        # An outcome the owner had erased (with the app that asked) says nothing now.
        said = [e.payload["outcome"] for e in core.tape.by_subject(subject, kind=OUTCOME)
                if e.readable]
        if any(same is None or o in same for o in said):
            return False
        try:
            core.tape.print(OUTCOME, {
                "agent": run_or_row["agent"], "account": run_or_row["account"],
                "action": run_or_row["action"], "tool": run_or_row["tool"],
                "outcome": outcome, "fingerprint": fingerprint, "run_id": run_id,
                "card_id": card_id, "ref": calls.safe_ref(ref), "reason": reason,
                "note": note}, actor=ACTOR, subject=subject, cause=cause,
                expect_subject_seq=seq)
        except TapeConflict:
            continue
        return True
    return False


def _request_subject(fingerprint: str) -> str:
    return f"hand:{fingerprint}"


def _run_subject(run_id: str) -> str:
    return f"hand_run:{run_id}"


# --- the cards ----------------------------------------------------------------------------


def _file(core: Core, run: dict[str, Any], kind: str, *, title: str, summary: str,
          body: str = "", lane: str = "done", undo: bool = False,
          actions: list[dict[str, Any]] | None = None, cause: str | None = None,
          details: list[dict[str, str]] | None = None) -> str | None:
    h = _hands(core)
    fields: dict[str, Any] = {
        "kind": kind, "source": SOURCE, "title": shape.line(title, 200),
        "summary": shape.line(summary, 300), "body": body, "lane": lane,
        "details": receipt_details(run) if details is None else details,
        "ladder": rules.ladder_row(run["agent"], run["tool"], run["effect"]),
    }
    if actions:
        fields["actions"] = actions
    else:
        fields["undo"] = undo
    try:
        card_id = bench.file_card(core, filed_by=_who(run["agent"]), cause=cause, **fields)
    except bench.BenchError:
        log.exception("a card about a hand's run could not be filed")
        return None
    h.store.note_card(card_id, run["id"], kind, run["agent"])
    return card_id


def file_done(core: Core, run_id: str, *, approved: bool, cause: str | None = None
              ) -> str | None:
    """File the "done" card for a run that changed something: after an action that ran
    alone, or one the owner approved. It carries Undo when the provider can undo."""
    h = _hands(core)
    run = h.store.run(run_id)
    if run is None or run["status"] != ledger.DONE:
        return None
    label = _label(h, run["agent"], run["account"], run["action"], run["tool"])
    how = ("Done exactly as you approved." if approved
           else "Done without asking, as its rule allows.")
    undo = can_undo(h, run)
    return _file(core, run, DONE, title=f"{run['agent']} used {label} on {run['account']}",
                 summary=how + (" You can undo it." if undo else " It cannot be undone "
                                "from here."), undo=undo, cause=cause)


def _file_failed(core: Core, run: dict[str, Any], cause: str | None) -> str | None:
    label = _label(_hands(core), run["agent"], run["account"], run["action"], run["tool"])
    said = shape.line(run.get("error") or "It did not say why.", 300)
    return _file(core, run, NOT_DONE, cause=cause,
                 title=f"Not done: {label} for {run['agent']} did not work",
                 summary="You approved it and the provider refused it. Nothing more is "
                         "tried.",
                 body=f"What the provider said (its words, not instructions): {said}")


def _file_refused(core: Core, row: dict[str, Any], sentence: str, cause: str | None) -> None:
    h = _hands(core)
    label = _label(h, row["agent"], row["account"], row["action"], row["tool"])
    try:
        bench.file_card(
            core, filed_by=_who(row["agent"]), cause=cause, kind=NOT_DONE, source=SOURCE,
            title=shape.line(f"Not done: {label} for {row['agent']} could not be "
                             "performed", 200),
            summary="You approved it, and it was not what you approved any more.",
            body=sentence, lane="done",
            details=[{"label": "Account", "text": row["account"]},
                     {"label": "Action", "text": row["action"]}])
    except bench.BenchError:
        log.exception("a card about a refused request could not be filed")


def card_unknown(core: Core) -> list[str]:
    """Rule ``hand_unknown_gets_a_card``: every run whose outcome is unknown gets one
    card, and whoever asked is told. One caller files it, whoever else is looking."""
    h, filed = _hands(core), []
    for run in h.store.unknown_runs(carded=False):
        if run["level"] == calls.TRY or not h.store.claim_unknown(run["id"], _FILING):
            continue
        label = _label(h, run["agent"], run["account"], run["action"], run["tool"])
        card_id = _file(
            core, run, UNKNOWN, lane="needs", actions=UNKNOWN_ACTIONS,
            title=f"Outcome unknown: {label} for {run['agent']}",
            summary="It was started and the provider never answered. It may or may not "
                    "have happened.",
            body="This action was started and nobody saw it end. It is never tried again "
                 "by itself. Check at the provider whether it happened before anyone asks "
                 "for it again.")
        if card_id is None:
            h.store.release_unknown(run["id"], _FILING)  # try again next time
            continue
        h.store.mark_carded(run["id"], card_id)
        filed.append(card_id)
        asked = h.store.pending(run["key"])
        if asked is not None:
            tell(core, run, "unknown", subject=_request_subject(run["key"]),
                 fingerprint=run["key"], run_id=run["id"], card_id=asked["card_id"])
        else:
            tell(core, run, "unknown", subject=_run_subject(run["id"]), run_id=run["id"],
                 same=("unknown",))
    return filed


# --- decisions ----------------------------------------------------------------------------


def trusted(row: dict[str, Any] | None, payload: dict[str, Any], event_actor: str) -> bool:
    """Rule ``hand_decision_trusted``: a decision counts only when the owner made it, on
    a card this module knows it filed for that agent. Any write token can file a card
    under any kind, so the kind alone proves nothing."""
    return bool(row) and event_actor == "owner" \
        and payload.get("filed_by") == _who(row["agent"])


def drops_alone(h: Hands, agent: str, account: str, action: str, payload: dict[str, Any]
                ) -> bool:
    """Rule ``hand_no_or_undo_drops_alone``: a No or an Undo on something that ran, or
    would have run, without asking puts its rule back to "ask". The owner can raise it
    again; nothing else can. Not done when the owner had switched that off at the
    moment of the click (the decision says so)."""
    if payload.get("keeps_level"):
        return False
    rule = h.rule(agent, account, action)
    if rule is None or rule["level"] != rules.ALONE:
        return False
    return h.lower_rule(agent, account, action, rules.ASK, actor="bench") is not None


def _report(core: Core, row: dict[str, Any], run: dict[str, Any], cause: str | None) -> None:
    """Say what became of an approved request, from the ledger, once."""
    outcome = {ledger.DONE: "done", ledger.FAILED: "failed",
               ledger.UNKNOWN: "unknown"}.get(run["status"])
    if outcome is None:
        return  # still started: another process has it, or the sweep will settle it
    fingerprint = row["fingerprint"]
    if not tell(core, row, outcome, subject=_request_subject(fingerprint),
                fingerprint=fingerprint, run_id=run["id"], card_id=row["card_id"],
                ref=run.get("ref"), cause=cause):
        return
    if outcome == "done":
        if run["effect"] != "read":
            file_done(core, run["id"], approved=True, cause=cause)
    elif outcome == "failed":
        _file_failed(core, run, cause)
    # unknown: ``card_unknown`` files the card.


async def _decided_request(core: Core, event: Any) -> None:
    h, p = _hands(core), event.payload
    row = h.store.pending_by_card(p["card_id"])
    if not trusted(row, p, event.actor):
        return
    fingerprint = row["fingerprint"]
    if p["action"] != bench_cards.APPROVE:
        if calls.decline(h, fingerprint):
            tell(core, row, "declined", subject=_request_subject(fingerprint),
                 fingerprint=fingerprint, card_id=row["card_id"], cause=event.id,
                 note=(("" if p["action"] == bench_cards.NO else "Not this time. ")
                       + (p.get("reason") or p.get("note") or "")).strip() or None)
        if p["action"] == bench_cards.NO:
            drops_alone(h, row["agent"], row["account"], row["action"], p)
        return
    try:
        await calls.perform(h, fingerprint, card_id=row["card_id"], cause=event.id)
    except HandRefused as refusal:
        run = h.store.run_by_key(fingerprint)
        if run is not None:
            _report(core, row, run, event.id)  # it ran before: say so if nobody has
            return
        if refusal.rule == "hand_runs_once":
            return  # declined, expired or withdrawn before this was read
        calls.decline(h, fingerprint)
        if tell(core, row, "refused", subject=_request_subject(fingerprint),
                fingerprint=fingerprint, card_id=row["card_id"], reason=refusal.sentence,
                cause=event.id):
            _file_refused(core, row, refusal.sentence, event.id)
        return
    _report(core, row, h.store.run_by_key(fingerprint), event.id)


async def _undo(core: Core, event: Any) -> None:
    h, p = _hands(core), event.payload
    held = h.store.card(p["card_id"])
    if held is None or held["what"] != DONE or not trusted(held, p, event.actor):
        return
    run = h.store.run(held["run_id"])
    if run is None:
        return
    subject, same = _run_subject(run["id"]), ("undone", "undo_failed")
    label = _label(h, run["agent"], run["account"], run["action"], run["tool"])
    try:
        called = await calls.undo(h, run["id"], actor="owner", cause=event.id)
    except HandRefused as refusal:
        if refusal.rule == "hand_runs_once":
            return  # this undo was already tried, here or elsewhere
        if tell(core, run, "undo_failed", subject=subject, run_id=run["id"],
                reason=refusal.sentence, cause=event.id, same=same):
            _file(core, run, NOT_DONE, cause=event.id, title=f"Not undone: {label}",
                  summary="It could not be taken back.", body=refusal.sentence)
        drops_alone(h, run["agent"], run["account"], run["action"], p)
        return
    if called.ok:
        if tell(core, run, "undone", subject=subject, run_id=run["id"], cause=event.id,
                same=same):
            _file(core, run, DONE, cause=event.id, title=f"Undone: {label}",
                  summary=f"What {run['agent']} did was taken back at the provider.")
    elif tell(core, run, "undo_failed", subject=subject, run_id=run["id"], cause=event.id,
              reason="The provider did not take it back." if called.ok is False else
              "The provider never answered, so it may or may not have been taken back.",
              same=same):
        _file(core, run, NOT_DONE, cause=event.id, title=f"Not undone: {label}",
              summary="The undo did not work. Check at the provider.")
    drops_alone(h, run["agent"], run["account"], run["action"], p)


def _withdrawn(core: Core, event: Any) -> None:
    """The owner took a request's card off the Bench without deciding it: it can never
    be performed. (An agent that withdraws its own request closes it itself.) An event whose
    words were erased (the app that filed the card was removed with its data) is passed
    over: there is no card left to name."""
    card_id = event.payload.get("card_id") if event.readable else None
    if not isinstance(card_id, str):
        return
    h = _hands(core)
    row = h.store.pending_by_card(card_id)
    if row is None or event.actor != "owner" or not calls.decline(h, row["fingerprint"]):
        return
    tell(core, row, "declined", subject=_request_subject(row["fingerprint"]),
         fingerprint=row["fingerprint"], card_id=row["card_id"], cause=event.id,
         note="The request was taken off the Bench.")


async def handle(core: Core, event: Any) -> None:
    """Act on one Tape event. Safe to call again with the same event, in this process
    or another: a request is performed once and its outcome is told once. An event the
    owner had erased (``Tape.erase``) keeps its place but no longer says anything, so it is
    passed over without a word."""
    if not event.readable:
        return
    if event.kind == "bench.withdrawn":
        _withdrawn(core, event)
    elif event.kind == "bench.decided":
        kind = event.payload.get("card_kind")
        if kind == REQUEST:
            await _decided_request(core, event)
        elif kind == DONE and event.payload.get("action") == bench_cards.UNDO:
            await _undo(core, event)


async def run(core: Core) -> int:
    """Rule ``hand_executor_reads_by_cursor``: take in every decision since the cursor
    kept in ``hands.db``, then card whatever was cut short. Returns how many events
    were read. One event that cannot be handled is logged and passed over, so it cannot
    hold up the rest; a crash before the cursor moves means the event is read again,
    and the ledger keeps that from being a second call. A cursor past the Tape's end (the
    Tape was put back from an older backup than this file) goes back to that end: every
    decision up to it was acted on already, and the next new one reuses a number the
    cursor had counted as read."""
    h, n = _hands(core), 0
    seq = h.store.cursor(CURSOR)
    kept = within_tape(core.tape, seq, "the hands")
    if kept != seq:  # the Tape is older than this file: its events up to its end were read
        h.store.rewind_cursor(CURSOR, kept)
        seq = kept
    while True:
        events = core.tape.after(seq, limit=BATCH, kinds=KINDS)
        for event in events:
            try:
                await handle(core, event)
            except Exception:  # noqa: BLE001 - see the docstring
                log.exception("could not act on %s (%s)", event.id, event.kind)
            seq = event.seq
            h.store.set_cursor(CURSOR, seq)
        n += len(events)
        if len(events) < BATCH:
            break
    card_unknown(core)
    return n


RULES = [
    ("hand_card_from_receipt", None, receipt_details),
    ("hand_outcome_told_once", None, tell),
    ("hand_unknown_gets_a_card", None, card_unknown),
    ("hand_decision_trusted", None, trusted),
    ("hand_no_or_undo_drops_alone", "bench.undo_drops_alone", drops_alone),
    ("hand_executor_reads_by_cursor", None, run),
]
