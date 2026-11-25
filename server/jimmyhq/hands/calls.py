"""Running an action: the one way anything leaves through a hand.

``call`` is the entry for a caller acting for an agent. In order, it:

1. finds the rule that switches this tool on for this agent (no rule: refused);
2. checks the action is still the version the owner looked at (changed: withdrawn);
3. builds the final arguments: the caller's open ones, checked, plus the owner's fixed
   ones (the caller can never set a fixed or hidden argument);
4. reads the level: a changing action at ``read`` is refused; ``ask`` returns
   ``NeedsApproval`` and calls nobody; ``alone`` outside its limits does the same;
5. for a reading, answers a repeat from the kept copy, with no new call;
6. checks the day's and the month's caps;
7. writes the run ``started`` in the ledger, calls the provider once, settles the run;
8. shapes the answer and prints the ``hand.called`` receipt.

``perform`` runs a request the owner approved, by its fingerprint, exactly once, and
only if nothing about it changed since it was asked.

A run whose process died between ``started`` and the answer is found by
``settle_unknown``. Nobody knows whether it happened, so it is recorded as "outcome
unknown" and never tried again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from jimmyhq.hands import caps, mail, rules, shape
from jimmyhq.hands import store as ledger
from jimmyhq.hands.provider import READ, Action, Outcome, can_undo
from jimmyhq.hands.rules import HandRefused, Invalid
from jimmyhq.hands.service import ON, UNREACHABLE, Hands, NotReady
from jimmyhq.ids import ulid

log = logging.getLogger("jimmyhq.hands")

OWNER = "owner"
TRY = "try"
GRACE_S = 60  # past the timeout before a started run is taken for dead
REF_CHARS = 200
EXPIRED = "expired"


@dataclass(frozen=True)
class Called:
    """A call that ran, or was answered from a kept copy.

    ``result`` is a line this module wrote: safe to log and to show in a list of
    actions. ``shown`` is the content, framed as untrusted, for the model to see once.
    ``ok`` is None when it is not known whether the action happened."""

    ok: bool | None
    result: str
    shown: str
    tool: str
    effect: str
    level: str
    run_id: str | None = None
    ref: str | None = None
    cached: bool = False
    more: bool = False
    can_undo: bool = False


@dataclass(frozen=True)
class NeedsApproval:
    """The action was not run: it waits for the owner. ``arguments`` are the exact,
    final arguments (fixed values included) that ``perform(fingerprint)`` would send,
    for the card to show in full. They are private: show them to the owner, never
    print them."""

    fingerprint: str
    agent: str
    account: str
    action: str
    tool: str
    label: str
    effect: str
    why: str  # "ask", or the limit the call was outside of
    arguments: dict[str, Any] = field(default_factory=dict)
    recipients: tuple[str, ...] = ()
    amount: Any = None
    can_undo: bool = False
    expires_ms: int = 0
    event_id: str | None = None


def fingerprint_of(*, request_id: str, agent: str, account: str, action: str, version: str,
                   rule_hash: str, arguments: dict[str, Any]) -> str:
    """Rule ``hand_request_frozen``: the fingerprint of one request. It covers who asked,
    the action and its version, the whole rule and every argument, so nothing about a
    request can change without the fingerprint changing."""
    frozen = {"request": request_id, "agent": agent, "account": account, "action": action,
              "version": version, "rule": rule_hash, "arguments": arguments}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def kept_name(core: Any, rule: dict[str, Any], final: dict[str, Any]) -> str:
    """Rule ``hand_kept_copy_follows_the_rule``: the name a reading's kept copy is filed
    under. The copy is the answer after shaping, so the name covers everything that
    shaped it: the whole rule (agent, account, action, version, addresses shown, fields
    kept) and the settings that hide fields and cut values. When the owner tightens any
    of them, the old copy is never found again and the next reading is a fresh call."""
    get = core.settings.get
    said = json.dumps([rules.rule_hash(rule), final, get("hands.hidden_fields"),
                       get("hands.value_chars")], sort_keys=True, default=str)
    return hashlib.sha256(said.encode("utf-8")).hexdigest()


def receipt_of(action: Action, final: dict[str, Any]) -> dict[str, Any]:
    """Rule ``hand_receipt_has_no_values``: what of the arguments a receipt keeps. Their
    names, the size of each and how many recipients: never a value."""
    def size(value: Any) -> int:
        return len(value if isinstance(value, str) else json.dumps(value, default=str))

    return {"argument_names": sorted(final),
            "argument_chars": {name: size(value) for name, value in final.items()},
            "recipients": len(rules.recipients_of(action, final))}


def safe_ref(ref: str | None) -> str | None:
    """A provider's reference as the Tape may hold it: one line, with any address in it
    hidden. The whole reference stays in the private ledger."""
    return shape.mask_text(shape.line(ref, REF_CHARS)) if ref else None


def _owner_fix(h: Hands, tool: str, why: str | None) -> HandRefused:
    return HandRefused(
        "hand_withdrawn_when_changed",
        f"Nothing was done: {tool} cannot be used for now. {why or ''} Carry on without it; "
        f"only {caps.owner_name(h.core)} can put it right.".replace("  ", " "),
        frustrate=False)


async def _resolve(h: Hands, agent: str, tool: str) -> tuple[dict[str, Any], Action]:
    rule = h.rule_by_tool(agent, tool) if isinstance(tool, str) else None
    if rule is None:
        raise HandRefused(
            "hand_needs_a_rule",
            f"Nothing was done: you have no tool called \"{shape.line(tool, 80)}\". Use "
            "only the tools you are offered.")
    state, why, action = await h.rule_state(rule, fresh=True)
    if state != ON or action is None:
        raise _owner_fix(h, tool, why)
    return rule, action


async def call(h: Hands, *, agent: str, tool: str, arguments: dict[str, Any] | None = None,
               actor: str | None = None, cause: str | None = None, root: str | None = None
               ) -> Called | NeedsApproval:
    """Run a tool for an agent, or say that it needs the owner's yes.

    Raises ``HandRefused`` (nothing happened) with the name of the rule that said no."""
    rule, action = await _resolve(h, agent, tool)
    final, start = rules.final_arguments(rule, action, arguments)
    now = h.now()
    reading, level = rule["effect"] == READ, rule["level"]
    if not reading and level == rules.READ_ONLY:
        raise HandRefused(
            "hand_look_not_do",
            f"Nothing was done: on {rule['account']} you may look, not do this. {tool} "
            f"changes something, and {caps.owner_name(h.core)} has set it to look only. "
            "Carry on without it.", frustrate=False)
    mailed = None
    if action.recipient_classes and not reading:
        final, mailed = await _mail_class(h, rule, action, final)
    if level == rules.ASK:
        return _request(h, rule, action, final, "ask", actor or f"agent:{agent}", cause, root)
    if not reading:
        outside = rules.outside_limits(h.core, rule, action, final, caps.action_today(
            h.core, h.store, agent, rule["account"], rule["action"], now))
        if not outside and mailed is not None:
            outside = mail.needs_asking(h.core, mailed)
        if outside:
            return _request(h, rule, action, final, outside, actor or f"agent:{agent}",
                            cause, root)
    name = None
    if reading:
        name = kept_name(h.core, rule, final)
        again = _repeat(h, rule, name, start)
        if again is not None:
            return again
        caps.reads_per_day(h.core, h.store, rule, now)
    account = h.account(rule["account"])
    provider = h.provider(account["provider_id"])
    caps.calls_per_day(h.core, h.store, agent, rule["account"], now)
    caps.calls_per_month(h.core, h.store, provider.id, provider.label, now)
    return await _execute(
        h, key=ulid(), agent=agent, actor=actor or f"agent:{agent}", account=rule["account"],
        action=action, tool=tool, effect=rule["effect"], level=level, final=final,
        show_addresses=rule["show_addresses"], fields=rule["fields"], start=start,
        start_name=rules.start_name(rule) if reading else None, keep_as=name, cause=cause,
        root=root)


async def _mail_class(h: Hands, rule: dict[str, Any], action: Action, final: dict[str, Any]
                      ) -> tuple[dict[str, Any], str]:
    """Rule ``hand_mail_recipient_class``, applied: who a message from an agent's own
    inbox would go to, settled BEFORE anything is asked or sent, and its class.

    A provider that can say who receives it (a reply goes to the message's sender) is
    asked, and the addresses it names are put into the arguments, so a card shows them
    and an approval sends to exactly them. Who sent the message being replied to, and
    whether their sender check passed, is the provider's word; nothing the message says
    is read."""
    tool = rule["tool"]
    try:
        provider = h.adapter(h.account(rule["account"])["provider_id"])
        there = h.provider_account(rule["account"])
    except (NotReady, TypeError) as exc:
        raise _owner_fix(h, tool, getattr(exc, "message", None)) from None
    resolve = getattr(provider, "recipients", None)
    found: list[tuple[str, str | None]]
    if callable(resolve):
        try:
            found, final = await resolve(there, action.name, dict(final))
        except LookupError as exc:
            raise HandRefused("hand_mail_recipient_class",
                              f"Nothing was sent: {shape.line(exc, 200)}") from None
        except Exception as exc:  # noqa: BLE001 - an adapter's fault is an answer here
            log.warning("finding who a message goes to broke: %s", type(exc).__name__)
            raise HandRefused(
                "hand_mail_recipient_class",
                f"Nothing was sent: who {tool} would go to could not be worked out, because "
                "the provider did not answer. Try again later.", frustrate=False) from None
    else:
        found = [(a, None) for a in rules.recipients_of(action, final)]
    if not found:
        raise HandRefused("hand_mail_recipient_class",
                          f"Nothing was sent: {tool} names nobody to send to. Call it again "
                          "with who it is for.")
    return final, mail.classify(h.core, h.store, account=rule["account"], agent=rule["agent"],
                                recipients=found)


def _repeat(h: Hands, rule: dict[str, Any], name: str, start: int) -> Called | None:
    """Rule ``hand_repeat_reads_only``: the same reading asked for again within
    ``hands.repeat_minutes`` is answered from the copy that was kept, with no new call.
    Only ``call`` asks, and only for an action whose effect is ``read``."""
    window = int(h.core.settings.get("hands.repeat_minutes")) * 60_000
    copy = h.store.kept(name, h.now() - window) if window > 0 else None
    if copy is None:
        return None
    at = caps.local_time(h.core, copy["ts_ms"])
    seen = shape.shown(copy["body"], start=start, start_name=rules.start_name(rule),
                       result_chars=int(h.core.settings.get("hands.result_chars")))
    result = (f"{rule['tool']}: answered from the copy kept at {at:%H:%M}, with no new call: "
              f"characters {seen.start} to {seen.end} of {seen.total}")
    return Called(ok=True, result=result, shown=f"{result}.\n\n{seen.text}", tool=rule["tool"],
                  effect=rule["effect"], level=rule["level"], cached=True, more=seen.more)


def drop_old_copies(h: Hands) -> int:
    """Rule ``hand_old_copies_dropped``: a kept copy older than ``hands.repeat_minutes``
    can never answer again, so it is deleted, whether or not another reading follows.
    With the window at 0 every copy goes. Run by the worker's ``hands.settle`` job."""
    window = int(h.core.settings.get("hands.repeat_minutes")) * 60_000
    return h.store.drop_kept(h.now() - window + (0 if window > 0 else 1))


def _request(h: Hands, rule: dict[str, Any], action: Action, final: dict[str, Any], why: str,
             actor: str, cause: str | None, root: str | None) -> NeedsApproval:
    """Rule ``hand_ask_calls_nobody``: an action that needs the owner's yes is frozen
    and recorded, and no provider is called."""
    now = h.now()
    caps.requests_per_day(h.core, h.store, rule["agent"], now)
    request_id, rule_hash = ulid(), rules.rule_hash(rule)
    fingerprint = fingerprint_of(
        request_id=request_id, agent=rule["agent"], account=rule["account"],
        action=rule["action"], version=rule["version"], rule_hash=rule_hash, arguments=final)
    h.store.add_pending(
        fingerprint=fingerprint, request_id=request_id, agent=rule["agent"],
        account=rule["account"], action=rule["action"], tool=rule["tool"],
        effect=rule["effect"], version=rule["version"], rule_hash=rule_hash, arguments=final,
        why=why, now=now)
    recipients = rules.recipients_of(action, final)
    event = h.core.tape.print("hand.proposed", {
        "fingerprint": fingerprint, "agent": rule["agent"], "account": rule["account"],
        "action": rule["action"], "tool": rule["tool"], "effect": rule["effect"], "why": why,
        "argument_names": sorted(final), "recipients": len(recipients),
    }, actor=actor, cause=cause, root=root, subject=f"hand:{fingerprint}")
    h.store.note_pending(fingerprint, event_id=event.id)
    return NeedsApproval(
        fingerprint=fingerprint, agent=rule["agent"], account=rule["account"],
        action=rule["action"], tool=rule["tool"], label=action.label, effect=rule["effect"],
        why=why, arguments=final, recipients=tuple(recipients),
        amount=final.get(action.amount) if action.amount else None, can_undo=action.undo,
        expires_ms=now + _expires_ms(h), event_id=event.id)


_CARD_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


def request_values(h: Hands, card_id: str) -> dict[str, Any] | None:
    """Rule ``hand_values_only_for_the_owner``: a request as the owner's Bench card shows
    it. Its card names each argument and its size, never a value, because a card is
    kept on the Tape for ever. The values are here, in ``hands.db``, only while the
    request waits: once it is performed, declined, withdrawn or gone with its account
    they were erased, and ``arguments`` is None. None when no request is tied to that
    card. The API gives this to the signed-in owner only."""
    card_id = str(card_id or "").strip().upper()
    row = h.store.pending_by_card(card_id) if _CARD_ID.fullmatch(card_id) else None
    if row is None:
        return None
    waiting = row["status"] == ledger.PENDING and row["arguments"] is not None
    return {"card_id": card_id, "agent": row["agent"], "account": row["account"],
            "action": row["action"], "tool": row["tool"], "waiting": waiting,
            "arguments": row["arguments"] if waiting else None}


def _expires_ms(h: Hands) -> int:
    return int(h.core.settings.get("hands.request_expires_h")) * 3_600_000


def _not_again(what: str) -> HandRefused:
    return HandRefused("hand_runs_once",
                       f"Nothing was done: {what}. It is never run a second time. If it is "
                       "still wanted, it has to be asked for again.", frustrate=False)


def _state_words(run: dict[str, Any]) -> str:
    return {ledger.DONE: "this request was already performed",
            ledger.FAILED: "this request was already tried, and it failed",
            ledger.UNKNOWN: "this request was started before and its outcome is unknown",
            ledger.STARTED: "this request is being performed right now, or was cut short"
            }[run["status"]]


async def perform(h: Hands, fingerprint: str, *, card_id: str | None = None,
                  actor: str | None = None, cause: str | None = None,
                  root: str | None = None) -> Called:
    """Run exactly the request the owner approved, once.

    The caller is responsible for the approval itself (it read the owner's decision).
    This checks everything else: the request is still waiting and not too old, its
    stored arguments still match the fingerprint printed when it was asked, the rule is
    the same rule, and the action is the version the rule pinned. Any difference:
    ``HandRefused``, nothing happened, and the agent has to ask again."""
    row = h.store.pending(fingerprint) if isinstance(fingerprint, str) else None
    if row is None:
        raise HandRefused("hand_request_frozen",
                          "Nothing was done: there is no request with that fingerprint.",
                          frustrate=False)
    used = h.store.run_by_key(fingerprint)
    if used is not None:
        raise _not_again(_state_words(used))
    if row["status"] != ledger.PENDING or row["arguments"] is None:
        raise _not_again(f"this request was already {row['status']}")
    if h.now() - row["created_ms"] > _expires_ms(h):
        h.store.close_pending(fingerprint, EXPIRED)
        raise HandRefused(
            "hand_request_expires",
            "Nothing was done: this request waited longer than "
            f"{h.core.settings.get('hands.request_expires_h')} hours, so it can no longer "
            "be performed. It has to be asked for again.", frustrate=False)
    changed = HandRefused(
        "hand_request_frozen",
        "Nothing was done: the rule, the action or the arguments changed after this was "
        "asked, so it is no longer what was approved. It has to be asked for again.",
        frustrate=False)
    final = row["arguments"]
    proposed = h.core.tape.get(row["event_id"]) if row["event_id"] else None
    if (proposed is None or proposed.kind != "hand.proposed"
            or (proposed.payload or {}).get("fingerprint") != fingerprint
            or fingerprint_of(request_id=row["request_id"], agent=row["agent"],
                              account=row["account"], action=row["action"],
                              version=row["version"], rule_hash=row["rule_hash"],
                              arguments=final) != fingerprint):
        raise changed
    rule = h.rule(row["agent"], row["account"], row["action"])
    if rule is None or rules.rule_hash(rule) != row["rule_hash"]:
        raise changed
    state, _, action = await h.rule_state(rule, fresh=True)
    if state == UNREACHABLE:
        raise HandRefused(
            "hand_withdrawn_when_changed",
            "Nothing was done: the provider could not be asked whether the action is still "
            "the one that was approved, so it was not performed. It has to be asked for "
            "again.", frustrate=False)
    if state != ON or action is None or action.version != row["version"]:
        raise changed
    called = await _execute(
        h, key=fingerprint, agent=row["agent"], actor=actor or f"agent:{row['agent']}",
        account=row["account"], action=action, tool=row["tool"], effect=rule["effect"],
        level=rule["level"], final=final, show_addresses=rule["show_addresses"],
        fields=rule["fields"], card_id=card_id or row["card_id"], cause=cause, root=root)
    h.store.close_pending(fingerprint, ledger.PERFORMED)  # and its arguments are erased
    return called


def decline(h: Hands, fingerprint: str) -> bool:
    """The owner said no, or the card was withdrawn: the request can never be performed,
    and its arguments are erased."""
    return h.store.close_pending(fingerprint, ledger.DECLINED)


async def undo(h: Hands, run_id: str, *, actor: str = OWNER, cause: str | None = None,
               root: str | None = None) -> Called:
    """Take back a run that can be taken back. Once: an undo is a run of its own."""
    run = h.store.run(run_id) if isinstance(run_id, str) else None
    if run is None or run["status"] != ledger.DONE or not run["ref"]:
        raise HandRefused("hand_undo_known", "Nothing was undone: there is no finished "
                                             "action with that id to take back.",
                          frustrate=False)
    try:
        provider = h.adapter(h.account(run["account"])["provider_id"]) \
            if h.account(run["account"]) else None
        actions = await h.actions(run["account"]) if provider else []
    except NotReady as exc:
        raise HandRefused("hand_undo_known", f"Nothing was undone: {exc.message}",
                          frustrate=False) from None
    action = next((a for a in actions if a.name == run["action"]), None)
    if provider is None or action is None or not action.undo or not can_undo(provider):
        raise HandRefused("hand_undo_known", "Nothing was undone: this action cannot be "
                                             "taken back.", frustrate=False)
    return await _execute(
        h, key=f"undo:{run_id}", agent=run["agent"], actor=actor, account=run["account"],
        action=action, tool=run["tool"], effect=run["effect"], level=run["level"], final={},
        card_id=run["card_id"], cause=cause, root=root, undo_ref=run["ref"])


async def try_read(h: Hands, account: str, action_name: str,
                   arguments: dict[str, Any] | None = None, *, actor: str = OWNER
                   ) -> dict[str, Any]:
    """The owner's "try it", for an action that only reads. An action that changes
    something, or whose effect is only a guess, cannot be tried from here."""
    held = h.account(account)
    if held is None:
        raise Invalid(f"There is no account called \"{account}\".")
    found = next((a for a in await h.actions(account) if a.name == action_name), None)
    if found is None:
        raise Invalid("That account has no such action.")
    if found.effect != READ or found.effect_from not in ("provider", "adapter"):
        raise Invalid("Nothing was tried: only an action that is known to just read can be "
                      "tried from here. This one changes something, or nobody is sure.")
    props, required = rules.properties(found)
    arguments = dict(arguments or {})
    unknown = sorted(set(arguments) - set(props))
    if unknown:
        raise Invalid(f"This action has no argument called \"{unknown[0]}\".")
    missing = [n for n in required if n not in arguments]
    if missing:
        raise Invalid(f"This action needs {', '.join(missing)}.")
    provider = h.provider(held["provider_id"])
    try:
        caps.calls_per_month(h.core, h.store, provider.id, provider.label, h.now())
        called = await _execute(
            h, key=ulid(), agent=OWNER, actor=actor, account=account, action=found,
            tool=rules.tool_name(account, action_name), effect=READ, level=TRY,
            final=arguments, framed=False)
    except HandRefused as exc:
        return {"ok": False, "text": exc.sentence, "more": False, "run_id": None}
    return {"ok": called.ok, "text": called.shown, "more": called.more,
            "run_id": called.run_id}


async def _execute(h: Hands, *, key: str, agent: str, actor: str, account: str, action: Action,
                   tool: str, effect: str, level: str, final: dict[str, Any],
                   show_addresses: bool = False, fields: Any = (), start: int = 0,
                   start_name: str | None = None, keep_as: str | None = None,
                   card_id: str | None = None, cause: str | None = None,
                   root: str | None = None, undo_ref: str | None = None,
                   framed: bool = True) -> Called:
    """Rule ``hand_runs_once``: one run. The ledger row is written before the provider
    is called and settled after; a key that ever started is never started again."""
    core, undoing = h.core, undo_ref is not None
    try:
        provider_id = h.account(account)["provider_id"]
        provider, there = h.adapter(provider_id), h.provider_account(account)
    except NotReady as exc:
        raise _owner_fix(h, tool, exc.message) from None
    run_id, began = ulid(), time.monotonic()
    receipt = {"run_id": run_id, "agent": agent, "account": account,
               "provider_id": provider_id, "action": action.name, "tool": tool,
               "effect": effect, "level": level, "card_id": card_id, "undo": undoing,
               **receipt_of(action, final)}
    try:
        h.store.start_run(run_id=run_id, key=key, agent=agent, account=account,
                          provider_id=provider_id, action=action.name, tool=tool,
                          effect=effect, level=level, now=h.now(), receipt=receipt,
                          card_id=card_id)
    except ledger.KeyUsed as exc:
        raise _not_again(_state_words(exc.run)) from None

    def closed(status: str, ok: bool | None, *, ref: str | None = None,
               error: str | None = None, error_kind: str | None = None,
               result_chars: int = 0) -> bool:
        """Settle the run and print its receipt. False if a sweep settled it first."""
        if not h.store.settle_run(run_id, status, now=h.now(), ref=ref, error=error,
                                  error_kind=error_kind):
            return False
        core.tape.print("hand.called", receipt | {
            "ok": ok, "ref": safe_ref(ref), "error_kind": error_kind,
            "result_chars": result_chars,
            "duration_ms": int((time.monotonic() - began) * 1000),
        }, actor=actor, cause=cause, root=root, subject=f"hand_run:{run_id}")
        return True

    def lost(kind: str) -> Called:
        # A reading that broke changed nothing. Anything else may have happened.
        if effect == READ and not undoing:
            closed(ledger.FAILED, False, error_kind=kind)
            said = f"{tool} did not work: the provider did not answer"
            return Called(ok=False, result=said, shown=said + ".", tool=tool, effect=effect,
                          level=level, run_id=run_id)
        closed(ledger.UNKNOWN, None, error_kind=kind)
        said = (f"{tool}: the outcome is unknown. The provider did not answer, so it may or "
                "may not have happened. It will not be tried again")
        return Called(ok=None, result=said, shown=said + ". Do not repeat it yourself: "
                      f"{caps.owner_name(core)} has been told and will check.", tool=tool,
                      effect=effect, level=level, run_id=run_id)

    timeout = int(core.settings.get("hands.run_timeout_s"))
    try:
        doing = (provider.undo(there, undo_ref) if undoing
                 else provider.run(there, action.name, dict(final), key=key))
        outcome: Outcome = await asyncio.wait_for(doing, timeout)
    except asyncio.CancelledError:
        lost("cancelled")
        raise
    except TimeoutError:
        return lost("timeout")
    except Exception as exc:  # noqa: BLE001 - whatever an adapter raises is an answer here
        log.warning("a hand's call broke: %s", type(exc).__name__)
        return lost("adapter_error")

    if not outcome.ok:
        error = shape.error_text(outcome.error or "it did not say why", show_addresses)
        closed(ledger.FAILED, False, ref=outcome.ref, error=error, error_kind="provider")
        said = f"{tool} did not work: {error}"
        return Called(ok=False, result=said, shown=said, tool=tool, effect=effect, level=level,
                      run_id=run_id, ref=outcome.ref)

    body = shape.shaped(outcome.data, secret_keys=core.settings.get("hands.hidden_fields"),
                        show_addresses=show_addresses, fields=fields,
                        value_chars=int(core.settings.get("hands.value_chars")))
    closed(ledger.DONE, True, ref=outcome.ref, result_chars=len(body))
    if action.recipient_classes and not undoing:
        _learn(h, agent, account, action, final)
    note = shape.line(outcome.note, 1500) if outcome.note else ""
    if keep_as and not note:  # a note is written fresh each time, never kept
        window = int(core.settings.get("hands.repeat_minutes")) * 60_000
        if window > 0:
            h.store.keep(keep_as, agent, account, body, h.now(), h.now() - window)
    result_chars = int(core.settings.get("hands.result_chars"))
    if not framed:
        piece, _, end, total = shape.part(body, start, result_chars)
        return Called(ok=True, result=f"{tool}: {total} characters", shown=piece, tool=tool,
                      effect=effect, level=level, run_id=run_id, ref=outcome.ref,
                      more=end < total)
    seen = shape.shown(body, start=start, result_chars=result_chars, start_name=start_name)
    if undoing:
        result = f"{tool}: taken back"
    elif effect == READ:
        result = f"{tool}: characters {seen.start} to {seen.end} of {seen.total} came back"
    else:
        result = f"{tool}: done"
    head = f"{result}.\n{note}" if note else f"{result}."
    return Called(ok=True, result=result, shown=f"{head}\n\n{seen.text}", tool=tool,
                  effect=effect, level=level, run_id=run_id, ref=outcome.ref, more=seen.more,
                  can_undo=action.undo and not undoing and bool(outcome.ref))


def _learn(h: Hands, agent: str, account: str, action: Action, final: dict[str, Any]) -> None:
    """Rule ``hand_mail_learns_who_was_written_to``: after mail left an agent's inbox,
    its recipients count as written to before, for that agent and inbox. Only a digest
    of each address is kept."""
    found = [mail.address_of(a) or a.strip().casefold()
             for a in rules.recipients_of(action, final)]
    h.store.learn_sent(account, agent, [mail.digest(a) for a in found if a], h.now())


def settle_unknown(h: Hands) -> list[dict[str, Any]]:
    """Rule ``hand_never_retried``: a run still ``started`` long after its call must
    have ended was cut short by a crash. Nobody knows whether it happened. It is
    recorded as "outcome unknown", its receipt says so, and it is never run again: its
    key stays used. Returns the runs just settled, for a card to be filed about each."""
    wait_ms = (int(h.core.settings.get("hands.run_timeout_s")) + GRACE_S) * 1000
    settled = []
    for run in h.store.stale_runs(h.now() - wait_ms):
        if not h.store.settle_run(run["id"], ledger.UNKNOWN, now=h.now(),
                                  error_kind="interrupted"):
            continue
        actor = OWNER if run["agent"] == OWNER else f"agent:{run['agent']}"
        h.core.tape.print("hand.called", run["receipt"] | {
            "ok": None, "ref": None, "error_kind": "interrupted", "result_chars": 0,
            "duration_ms": 0}, actor=actor, subject=f"hand_run:{run['id']}")
        settled.append(h.store.run(run["id"]))
    return settled
