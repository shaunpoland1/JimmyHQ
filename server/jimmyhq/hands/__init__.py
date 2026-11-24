"""Hands: the one way an agent (and later chat) uses an outside integration.

A *hand* is one action of one connected account, switched on by the owner for one agent.
This package is the provider-neutral core (the protocol an adapter implements, the
records, the rule table, result shaping, caps, the exactly-once run ledger and the
owner's API) and the adapters that ship with JimmyHQ: AgentMail, Composio and MCP
servers. Their
types are registered when this package is imported. Nothing is offered to an agent until
the owner adds a provider, connects an account and saves a rule.

    from jimmyhq import hands

    specs = await hands.tools_for(core, "ada")           # what to offer the agent
    out = await hands.call(core, agent="ada", tool="mail_messages_list", arguments={})
    if isinstance(out, hands.NeedsApproval):             # nothing was called
        card_id = file_the_card(out)                      # shows out.arguments in full
        hands.attach_card(core, out.fingerprint, card_id)
    ...
    done = await hands.perform(core, fingerprint, card_id=card_id)   # after the owner's yes

See ``docs/hands.md``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from jimmyhq.hands import agentmail, calls, caps, composio, execute, mail, mcp, rules, shape, watch
from jimmyhq.hands import options as record_options
from jimmyhq.hands.calls import Called, NeedsApproval
from jimmyhq.hands.provider import (
    Account,
    Action,
    Connect,
    HandProvider,
    Health,
    Inbound,
    Outcome,
    ProviderRecord,
    ProviderType,
    RecordField,
    Wiring,
    can_receive,
    hint_trusted,
    register_type,
)
from jimmyhq.hands.rules import HandRefused, Invalid, may_set_level, set_level_gate
from jimmyhq.hands.service import Hands, NotReady, ToolSpec
from jimmyhq.hands.shape import Shown

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core
    from jimmyhq.scheduler import Scheduler

__all__ = [
    "Account", "Action", "Called", "Connect", "HandProvider", "HandRefused", "Hands", "Health",
    "Inbound", "Invalid", "NeedsApproval", "NotReady", "Outcome", "ProviderRecord",
    "ProviderType", "RULES", "RecordField", "Shown", "ToolSpec", "Wiring", "attach_card",
    "build", "call", "decline", "execute", "hands", "hint_trusted", "inbound", "jobs",
    "mark_carded",
    "may_set_level", "owner_decides", "pending", "perform", "register_builtin", "register_type",
    "set_level_gate", "settle_unknown", "shown_inbound", "tools_for", "undo", "unknown_runs",
]

SETTLE_EVERY_S = 60
EXECUTE_EVERY_S = 3  # how soon after the owner's click an approved action leaves


def register_builtin() -> None:
    """Make the provider types that ship with JimmyHQ available. Run when this package
    is imported. The fake is not one of them: only a test or a demo registers it."""
    agentmail.register()
    composio.register()
    mcp.register()


register_builtin()


def hands(core: Core) -> Hands:
    """This process's hands service. Its state is on the Tape and in ``hands.db``."""
    return core.service("hands", Hands)


async def tools_for(core: Core, agent: str) -> list[ToolSpec]:
    """The tools to offer one agent right now, open arguments only."""
    return await hands(core).tools_for(agent)


async def call(core: Core, *, agent: str, tool: str, arguments: dict[str, Any] | None = None,
               actor: str | None = None, cause: str | None = None, root: str | None = None
               ) -> Called | NeedsApproval:
    """Run one of an agent's tools, or say it needs the owner's yes. See ``calls.call``."""
    return await calls.call(hands(core), agent=agent, tool=tool, arguments=arguments,
                            actor=actor, cause=cause, root=root)


async def perform(core: Core, fingerprint: str, *, card_id: str | None = None,
                  actor: str | None = None, cause: str | None = None,
                  root: str | None = None) -> Called:
    """Run exactly the request with this fingerprint, once. See ``calls.perform``."""
    return await calls.perform(hands(core), fingerprint, card_id=card_id, actor=actor,
                               cause=cause, root=root)


def pending(core: Core, fingerprint: str) -> dict[str, Any] | None:
    """A request as stored: its status, and its arguments while it still waits."""
    return hands(core).store.pending(fingerprint)


def attach_card(core: Core, fingerprint: str, card_id: str) -> None:
    """Note which Bench card shows a request, so its receipt can name the card."""
    hands(core).store.note_pending(fingerprint, card_id=card_id)


def decline(core: Core, fingerprint: str) -> bool:
    """Close a request for good and erase its arguments."""
    return calls.decline(hands(core), fingerprint)


async def undo(core: Core, run_id: str, *, actor: str = "owner", cause: str | None = None,
               root: str | None = None) -> Called:
    """Take back a finished run whose action can be taken back."""
    return await calls.undo(hands(core), run_id, actor=actor, cause=cause, root=root)


def settle_unknown(core: Core) -> list[dict[str, Any]]:
    """Mark runs cut short by a crash as "outcome unknown". Never retries one."""
    return calls.settle_unknown(hands(core))


def unknown_runs(core: Core, *, carded: bool | None = None) -> list[dict[str, Any]]:
    """Runs whose outcome is unknown; ``carded=False`` for those with no card yet."""
    return hands(core).store.unknown_runs(carded=carded)


def mark_carded(core: Core, run_id: str, card_id: str) -> None:
    hands(core).store.mark_carded(run_id, card_id)


async def inbound(core: Core, account: str, cursor: str | None
                  ) -> tuple[list[Inbound], str | None]:
    """What arrived for an account since ``cursor``, straight from its provider. The
    items are data: show them with ``shown_inbound``, and keep their text off the Tape."""
    h = hands(core)
    held = h.account(account)
    if held is None:
        raise NotReady("no_account", f"The account \"{account}\" is not connected.")
    provider = h.adapter(held["provider_id"])
    if not can_receive(provider):
        return [], cursor
    return await provider.inbound(h.provider_account(account), cursor)


def shown_inbound(core: Core, item: Inbound, *, show_addresses: bool = False,
                  start: int = 0) -> Shown:
    """Something that arrived, as a model may see it: unverified sender, untrusted text."""
    get = core.settings.get
    return shape.inbound(item, secret_keys=get("hands.hidden_fields"),
                         show_addresses=show_addresses, start=start,
                         value_chars=int(get("hands.value_chars")),
                         result_chars=int(get("hands.result_chars")))


def owner_decides() -> None:
    """Hand in the ladder's answer to "may this level be set?": the owner may choose
    any level for any action, in one step. A running JimmyHQ calls this on start."""
    from jimmyhq.bench import ladder  # noqa: PLC0415 - the Bench is only needed here

    set_level_gate(ladder.owner_may_choose_any_level)


def build(core: Core) -> APIRouter:
    from jimmyhq.hands.routes import build_router  # noqa: PLC0415 - keeps import light

    owner_decides()
    return build_router(core, hands(core))


def jobs(core: Core, scheduler: Scheduler) -> None:
    """The worker settles runs that were cut short and acts on the owner's decisions."""
    def sweep() -> None:
        settle_unknown(core)
        execute.card_unknown(core)

    scheduler.every("hands.settle", SETTLE_EVERY_S, sweep)
    scheduler.every("hands.execute", EXECUTE_EVERY_S,
                    lambda: asyncio.run(execute.run(core)))
    scheduler.every("hands.mail", int(core.settings.get("hands.mail_poll_s")),
                    lambda: asyncio.run(watch.poll(core, hands(core))))


# Every mechanical rule of this module: (name, the setting that holds its number, function).
RULES = [
    ("hand_needs_a_rule", None, calls.call),
    ("hand_tool_name", None, rules.tool_name),
    ("hand_argument_defaults", None, rules.default_arguments),
    ("hand_flat_arguments_only", None, rules.offerable),
    ("hand_model_sees_open_only", None, rules.tool_params),
    ("hand_arguments_by_mode", None, rules.final_arguments),
    ("hand_default_level", "hands.default_level_change", rules.default_level),
    ("hand_hint_trust", None, hint_trusted),
    ("hand_options_hold_no_secret", None, record_options.check_options),
    ("hand_argv_credentials_refused", None, record_options.credential_in),
    ("hand_secret_reference", None, record_options.with_secrets),
    ("hand_level_gate", None, rules.may_set_level),
    ("hand_only_owner_raises", None, rules.only_owner_raises),
    *execute.RULES,
    ("hand_look_not_do", None, calls.call),
    ("hand_ask_calls_nobody", None, calls._request),
    ("hand_alone_inside_limits", None, rules.outside_limits),
    ("hand_max_tools_per_agent", "hands.max_tools_per_agent", Hands.tools_for),
    ("hand_withdrawn_when_changed", None, Hands.rule_state),
    ("hand_calls_per_day", "hands.calls_per_day", caps.calls_per_day),
    ("hand_calls_per_month", "hands.calls_per_month", caps.calls_per_month),
    ("hand_requests_per_day", "hands.requests_per_day", caps.requests_per_day),
    ("hand_rule_per_day", None, caps.reads_per_day),
    ("hand_repeat_reads_only", "hands.repeat_minutes", calls._repeat),
    ("hand_request_frozen", None, calls.fingerprint_of),
    ("hand_request_expires", "hands.request_expires_h", calls.perform),
    ("hand_runs_once", None, calls._execute),
    ("hand_never_retried", "hands.run_timeout_s", calls.settle_unknown),
    ("hand_undo_known", None, calls.undo),
    ("hand_receipt_has_no_values", None, calls.receipt_of),
    ("hand_secret_keys_dropped", "hands.hidden_fields", shape.drop_secret_keys),
    ("hand_secret_values_scrubbed", None, shape.scrub),
    ("hand_addresses_masked", None, shape.mask_addresses),
    ("hand_fields_allowed", None, shape.allow_fields),
    ("hand_value_cut", "hands.value_chars", shape.cut_values),
    ("hand_result_cut", "hands.result_chars", shape.part),
    ("hand_text_is_data", None, shape.framed),
    ("hand_inbound_is_data", None, shape.inbound),
    ("hand_mail_sender_check", None, mail.sender_check),
    ("hand_mail_unknown_is_failed", "hands.mail_unknown_is_failed", mail.counted_check),
    ("hand_mail_instruction_needs_a_pass", "hands.mail_owner_is_instruction", mail.standing),
    ("hand_mail_recipient_class", "hands.mail_to_new", mail.recipient_class),
    ("hand_mail_most_careful_class", None, mail.most_careful),
    ("hand_mail_starts_by_class", None, rules.start_level),
    ("hand_mail_learns_who_was_written_to", None, calls._learn),
    ("hand_mail_forged_owner_card", "hands.mail_failed_cards_per_day",
     watch.claims_owner_and_failed),
]
