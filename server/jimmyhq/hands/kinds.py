"""Event kinds for the hands module.

Configuration (providers, accounts, rules) and receipts are events. Keys are never
here: they live in the secret store. A receipt carries names, sizes and counts, never an
argument's value, an address or a body. The provider's own reference for an account can
be an address, so it is kept in the private store, not on the Tape.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

_EFFECT = Literal["read", "send", "create", "update", "delete", "spend", "access", "change"]
_LEVEL = Literal["read", "ask", "alone"]
_FP = "9f2c1e5a7b3d4c6e8f0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f506"


@kind("hands.provider_saved", example={
    "provider_id": "mailbox", "type": "example", "label": "Mailbox",
    "base_url": "https://api.example.com/v1", "enabled": True, "options": {},
})
class ProviderSaved(Payload):
    """A provider was added or changed. The whole record, so the latest event wins."""

    provider_id: str
    type: str
    label: str
    base_url: str | None = None
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


@kind("hands.provider_removed", example={"provider_id": "mailbox"})
class ProviderRemoved(Payload):
    provider_id: str


@kind("hands.account_connected", example={
    "account": "mail", "provider_id": "mailbox", "label": "Agent mail", "app": None,
    "own_inbox": False,
})
class AccountConnected(Payload):
    """An account was connected under the owner's slug. Ids and labels only."""

    account: str
    provider_id: str
    label: str
    app: str | None = None
    # True for an inbox made for an agent's own use: its rules show addresses by default.
    own_inbox: bool = False


@kind("hands.account_forgotten", example={"account": "mail"})
class AccountForgotten(Payload):
    """An account was forgotten. Every rule for it goes with it."""

    account: str


class ArgumentRule(Payload):
    mode: Literal["open", "fixed", "hidden"]
    value: Any = None  # the owner's value, for "fixed" only


class Limits(Payload):
    per_day: int | None = Field(None, ge=1, le=100_000)
    recipients: Literal["owner", "known", "anyone"] | None = None
    max_amount: float | None = Field(None, ge=0)


@kind("hands.rule_set", example={
    "agent": "ada", "account": "mail", "action": "messages.list", "tool": "mail_messages_list",
    "label": "List messages", "level": "alone", "effect": "read", "effect_from": "adapter",
    "version": "1", "arguments": {"limit": {"mode": "open", "value": None}},
    "show_addresses": False, "fields": [], "limits": {"per_day": 50}, "confirmed": False,
})
class RuleSet(Payload):
    """One action of one account is switched on for one agent. The whole rule."""

    agent: str
    account: str
    action: str
    tool: str
    label: str = ""
    level: _LEVEL
    effect: _EFFECT
    effect_from: str
    version: str = ""  # the action's version when the owner looked at it
    arguments: dict[str, ArgumentRule] = Field(default_factory=dict)
    show_addresses: bool = False
    fields: list[str] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    confirmed: bool = False


@kind("hands.rule_removed", example={"agent": "ada", "account": "mail",
                                     "action": "messages.list"})
class RuleRemoved(Payload):
    agent: str
    account: str
    action: str


@kind("hand.proposed", example={
    "fingerprint": _FP, "agent": "ada", "account": "mail", "action": "messages.send",
    "tool": "mail_messages_send", "effect": "send", "why": "ask",
    "argument_names": ["subject", "text", "to"], "recipients": 1,
})
class HandProposed(Payload):
    """An agent asked for an action that needs the owner's yes. The fingerprint covers
    the exact arguments, the rule and the action's version; the arguments themselves
    wait in the private store."""

    fingerprint: str
    agent: str
    account: str
    action: str
    tool: str
    effect: _EFFECT
    why: str
    argument_names: list[str] = Field(default_factory=list)
    recipients: int = 0


@kind("hand.outcome", example={
    "agent": "ada", "account": "mail", "action": "messages.send",
    "tool": "mail_messages_send", "outcome": "done", "fingerprint": _FP,
    "run_id": "01JD3M4N5P6Q7R8S9T0V1W2X3Y", "card_id": "01JD3M4N5P6Q7R8S9T0V1W2X3Z",
    "ref": "msg-204", "reason": None, "note": None,
})
class HandOutcome(Payload):
    """What became of a request the owner decided, or of a run nobody saw end. Printed
    once by the executor, for whoever asked to read. ``reason`` is a sentence JimmyHQ
    wrote and ``note`` is the owner's own; nothing a provider said is here."""

    agent: str
    account: str
    action: str
    tool: str
    outcome: Literal["done", "failed", "unknown", "refused", "declined", "undone",
                     "undo_failed"]
    fingerprint: str | None = None
    run_id: str | None = None
    card_id: str | None = None  # the card the request was shown on
    ref: str | None = None  # the provider's reference, as a receipt keeps it
    reason: str | None = None
    note: str | None = None


@kind("hand.called", example={
    "run_id": "01JD3M4N5P6Q7R8S9T0V1W2X3Y", "agent": "ada", "account": "mail",
    "provider_id": "mailbox", "action": "messages.list", "tool": "mail_messages_list",
    "effect": "read", "level": "alone", "argument_names": ["limit"],
    "argument_chars": {"limit": 2}, "recipients": 0, "ok": True, "result_chars": 1840,
    "duration_ms": 420, "ref": None, "card_id": None, "error_kind": None, "undo": False,
})
class HandCalled(Payload):
    """The receipt for one call that left the machine. Names, sizes and counts only."""

    run_id: str
    agent: str
    account: str
    provider_id: str
    action: str
    tool: str
    effect: _EFFECT
    level: str  # read | ask | alone, or "try" for the owner's own test
    argument_names: list[str] = Field(default_factory=list)
    argument_chars: dict[str, int] = Field(default_factory=dict)
    recipients: int = Field(0, ge=0)
    ok: bool | None  # None: it is not known whether it happened
    result_chars: int = Field(0, ge=0)
    duration_ms: int = Field(0, ge=0)
    ref: str | None = None  # the provider's reference
    card_id: str | None = None
    error_kind: str | None = None
    undo: bool = False
