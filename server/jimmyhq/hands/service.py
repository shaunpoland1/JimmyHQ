"""The records of the hands module: providers, accounts and rules.

Providers are records like model providers, with the key in the secret store. An account
is a provider's account under a slug the owner chose. A rule switches one action of one
account on for one agent. All three are events on the Tape, read through one lens.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from jimmyhq import scrub
from jimmyhq.core import Core
from jimmyhq.hands import caps, rules
from jimmyhq.hands import options as record_options
from jimmyhq.hands.provider import (
    ID,
    TYPES,
    Account,
    Action,
    HandProvider,
    ProviderRecord,
    Wiring,
    can_receive,
    can_undo,
    hint_trusted,
)
from jimmyhq.hands.rules import Invalid
from jimmyhq.hands.shape import error_text
from jimmyhq.hands.store import FILE_NAME, HandsStore
from jimmyhq.ids import now_ms
from jimmyhq.lens import Lens
from jimmyhq.models import validate
from jimmyhq.tape.registry import Event

log = logging.getLogger("jimmyhq.hands")

KEY = "key"
SECRET_NAME = ID
MAX_OPTIONS_CHARS = 4000
MAX_FIELDS = 50

# What a rule can be, as the owner's screen shows it.
ON, ACCOUNT_GONE, PROVIDER_OFF, UNREACHABLE, ACTION_GONE, CHANGED, CANNOT_OFFER = (
    "on", "account_gone", "provider_off", "unreachable", "action_gone", "changed",
    "cannot_offer")


def secret_name(provider_id: str, name: str = KEY) -> str:
    return f"hands.{provider_id}.{name}"


class NotReady(Exception):  # noqa: N818 - a state, not a fault
    """A provider cannot be used right now. ``message`` is one sentence for the owner."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


class HandsLens(Lens):
    """Providers, accounts and rules, as last saved."""

    name = "hands.config"
    version = 1
    kinds = ("hands.provider_saved", "hands.provider_removed", "hands.account_connected",
             "hands.account_forgotten", "hands.rule_set", "hands.rule_removed")

    def initial(self) -> dict[str, Any]:
        return {"providers": {}, "accounts": {}, "rules": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        p = event.payload
        if event.kind == "hands.provider_saved":
            state["providers"][p["provider_id"]] = p
        elif event.kind == "hands.provider_removed":
            state["providers"].pop(p["provider_id"], None)
        elif event.kind == "hands.account_connected":
            state["accounts"][p["account"]] = p | {"connected_ms": event.ts}
        elif event.kind == "hands.account_forgotten":
            state["accounts"].pop(p["account"], None)
            # A rule never outlives its account: a later account under the same slug
            # starts with nothing switched on.
            for key in [k for k, r in state["rules"].items() if r["account"] == p["account"]]:
                del state["rules"][key]
        elif event.kind == "hands.rule_set":
            state["rules"][rules.rule_key(p["agent"], p["account"], p["action"])] = p
        elif event.kind == "hands.rule_removed":
            state["rules"].pop(rules.rule_key(p["agent"], p["account"], p["action"]), None)
        return state


@dataclass(frozen=True)
class ToolSpec:
    """One action as a tool an agent can be offered. ``params`` holds the open
    arguments only, in the flat form the tool catalog reads."""

    name: str
    description: str
    params: dict[str, dict[str, Any]]
    required: list[str]
    agent: str
    account: str
    action: str
    label: str
    effect: str
    level: str


class Hands:
    def __init__(self, core: Core):
        self.core = core
        self.store = HandsStore(core.config.data / FILE_NAME)
        self._lens = HandsLens()
        # Test seams: the HTTP client handed to adapters, and the clock.
        self.http: Any = None
        self.now = now_ms

    def state(self) -> dict[str, Any]:
        return self.core.lenses.read(self._lens)

    def close(self) -> None:
        self.store.close()

    # --- providers ------------------------------------------------------------

    def providers(self) -> list[ProviderRecord]:
        found = [_record(p) for p in self.state()["providers"].values()]
        return sorted(found, key=lambda p: p.label.lower())

    def provider(self, provider_id: str) -> ProviderRecord | None:
        payload = self.state()["providers"].get(provider_id)
        return _record(payload) if payload else None

    def describe_provider(self, record: ProviderRecord, names: set[str] | None = None,
                          health: dict[str, dict] | None = None) -> dict[str, Any]:
        """A provider as the API shows it: which secrets are saved, never what they are."""
        names = set(self.core.secrets.names()) if names is None else names
        health = self.store.health() if health is None else health
        kind = TYPES.get(record.type)
        prefix = secret_name(record.id, "")
        return record.as_dict() | {
            "has_key": secret_name(record.id) in names,
            "needs_key": bool(kind and kind.needs_key),
            "secrets": sorted(n[len(prefix):] for n in names
                              if n.startswith(prefix) and n != secret_name(record.id)),
            "type_known": kind is not None,
            # Secrets the record refers to by name, so the screen can say which are missing.
            "secrets_wanted": _wanted(kind, record) if kind else [],
            "health": health.get(record.id) or {"ok": None, "checked_ms": None, "error": None},
        }

    def save_provider(self, record: dict[str, Any], *, actor: str) -> ProviderRecord:
        """Add or change a provider. Raises ``Invalid`` with what to fix."""
        provider = check_provider(record)
        before = self.provider(provider.id)
        if before is not None and before.type != provider.type:
            raise Invalid("A provider's type cannot be changed. Add a new provider instead.")
        payload = provider.as_dict()
        payload["provider_id"] = payload.pop("id")
        self.core.tape.print("hands.provider_saved", payload, actor=actor,
                             subject=f"hand_provider:{provider.id}")
        self.store.forget_provider(provider.id)  # the last check was of the old record
        return provider

    def accounts_of(self, provider_id: str) -> list[str]:
        return sorted(a["account"] for a in self.state()["accounts"].values()
                      if a["provider_id"] == provider_id)

    def remove_provider(self, provider_id: str, *, actor: str) -> None:
        held = self.accounts_of(provider_id)
        if held:
            raise Invalid(f"This provider still has an account connected ({', '.join(held)}). "
                          "Forget the account first.")
        self.core.tape.print("hands.provider_removed", {"provider_id": provider_id},
                             actor=actor, subject=f"hand_provider:{provider_id}")
        prefix = secret_name(provider_id, "")
        for name in self.core.secrets.names():
            if name.startswith(prefix):
                self.core.secrets.delete(name)
        self.store.forget_provider(provider_id)

    def set_secret(self, provider_id: str, value: str, name: str = KEY) -> None:
        self.core.secrets.set(secret_name(provider_id, name), value)
        self.store.forget_provider(provider_id)

    def clear_secret(self, provider_id: str, name: str = KEY) -> bool:
        self.store.forget_provider(provider_id)
        return self.core.secrets.delete(secret_name(provider_id, name))

    def adapter(self, provider_id: str) -> HandProvider:
        """The adapter for a saved provider, wired with its key. Raises ``NotReady``."""
        record = self.provider(provider_id)
        if record is None:
            raise NotReady("not_configured", "That provider is no longer set up.")
        if not record.enabled:
            raise NotReady("disabled", f"{record.label} is switched off.")
        kind = TYPES.get(record.type)
        if kind is None:
            raise NotReady("no_adapter", f"This install has no adapter for the "
                                         f"\"{record.type}\" type of provider.")
        key = self.core.secrets.get(secret_name(record.id))
        if kind.needs_key and not key:
            raise NotReady("no_key", f"{record.label} has no key yet. Save its key under "
                                     "Settings, Integrations.")
        secrets, settings = self.core.secrets, self.core.settings
        return kind.factory(record, Wiring(
            key=key, secret=lambda name: secrets.get(secret_name(record.id, name)),
            http=self.http, setting=lambda name: settings.get(f"hands.{name}")))

    async def check(self, provider_id: str) -> dict[str, Any]:
        """Ask a provider whether it can be reached with the key it has."""
        now = self.now()
        try:
            health = await self.adapter(provider_id).check()
            ok, error = bool(health.ok), (error_text(health.error) if health.error else None)
        except NotReady as exc:
            return {"ok": False, "error": exc.message, "checked_ms": now}
        except Exception as exc:  # noqa: BLE001 - an adapter's fault is an answer here
            log.warning("a provider check failed: %s", type(exc).__name__)
            ok, error = False, "The provider could not be reached."
        self.store.set_health(provider_id, ok, now, error)
        return {"ok": ok, "error": error, "checked_ms": now}

    # --- accounts -------------------------------------------------------------

    def accounts(self) -> list[dict[str, Any]]:
        return sorted(self.state()["accounts"].values(), key=lambda a: a["account"])

    def account(self, slug: str) -> dict[str, Any] | None:
        return self.state()["accounts"].get(slug)

    def provider_account(self, slug: str) -> Account:
        """The account as its provider knows it, from the private store."""
        account, ref = self.account(slug), self.store.ref(slug)
        if account is None or ref is None:
            raise NotReady("no_account", f"The account \"{slug}\" is not connected. Connect "
                                         "it again under Settings, Integrations.")
        return Account(id=ref, label=account["label"], app=account.get("app"))

    def describe_account(self, account: dict[str, Any]) -> dict[str, Any]:
        provider = self.provider(account["provider_id"])
        return account | {
            "provider_label": provider.label if provider else None,
            "connected": self.store.ref(account["account"]) is not None,
            "receives": _supports(self, account["provider_id"], can_receive),
            # The inbox's own address, for an agent's inbox: the owner's to see.
            "address": self.store.address(account["account"]),
        }

    def _connected(self, slug: str, provider_id: str, found: Account, *, label: str,
                   app: str | None, own_inbox: bool, actor: str) -> dict[str, Any]:
        self.store.set_ref(slug, provider_id, found.id)
        self.store.set_address(slug, found.address)
        self.store.drop_connect(slug)
        self.core.tape.print("hands.account_connected", {
            "account": slug, "provider_id": provider_id, "label": label, "app": app,
            "own_inbox": own_inbox}, actor=actor, subject=f"hand_account:{slug}")
        return {"status": "ready", "url": None,
                "account": self.describe_account(self.account(slug))}

    async def connect(self, *, provider_id: str, slug: str, label: str = "",
                      app: str | None = None, own_inbox: bool = False,
                      actor: str) -> dict[str, Any]:
        """Connect an account under the owner's slug. Answers ``ready`` (with the
        account), ``redirect`` (with the provider's page for the owner to open; then
        call ``finish_connect`` until it is ready) or ``needs_key``."""
        if not isinstance(slug, str) or not rules.SLUG.match(slug):
            raise Invalid("An account's short name must start with a letter and use only "
                          "lower-case letters, digits and _ (24 at most).")
        if self.account(slug) is not None:
            raise Invalid(f"There is already an account called \"{slug}\". Choose another "
                          "short name, or forget that one first.")
        label = validate.text(label or slug, "The label", max_len=80)
        app = validate.text(app, "The app", max_len=80) if app else None
        try:
            provider = self.adapter(provider_id)
        except NotReady as exc:
            if exc.kind == "no_key":
                return {"status": "needs_key", "url": None, "account": None}
            raise
        answer = await provider.connect(app)
        if answer.status == "needs_key":
            return {"status": "needs_key", "url": None, "account": None}
        if answer.status == "ready" and answer.account is not None:
            return self._connected(slug, provider_id, answer.account, label=label, app=app,
                                   own_inbox=own_inbox, actor=actor)
        url = answer.url or ""
        if urlsplit(url).scheme not in ("http", "https"):
            raise NotReady("bad_answer", "The provider did not give a page to open. Try "
                                         "again, or check the provider.")
        seen = sorted(a.id for a in await provider.accounts())
        self.store.set_connect(slug, provider_id, {
            "label": label, "app": app, "own_inbox": own_inbox, "seen": seen}, self.now())
        return {"status": "redirect", "url": url, "account": None}

    async def finish_connect(self, slug: str, *, actor: str) -> dict[str, Any]:
        """After a redirect: look for the account the owner just approved at the
        provider. ``waiting`` until it shows up as active."""
        waiting = self.store.connect_of(slug)
        if waiting is None:
            raise Invalid(f"No account called \"{slug}\" is being connected. Start again "
                          "with Connect an account.")
        details, provider_id = waiting["details"], waiting["provider_id"]
        taken = set(details["seen"]) | self.store.refs(provider_id)
        for found in await self.adapter(provider_id).accounts():
            if (found.active and found.id not in taken
                    and (details["app"] is None or found.app in (None, details["app"]))):
                return self._connected(slug, provider_id, found, label=details["label"],
                                       app=details["app"], own_inbox=details["own_inbox"],
                                       actor=actor)
        return {"status": "waiting", "url": None, "account": None}

    def cancel_connect(self, slug: str) -> bool:
        """Drop a connection that was started and never finished. Nothing was recorded
        for it, so nothing is printed: only the waiting row goes. False if none waits."""
        if self.store.connect_of(slug) is None:
            return False
        self.store.drop_connect(slug)
        return True

    def dependents(self, slug: str) -> dict[str, Any]:
        """Who depends on an account: each agent with a rule for it and those rules,
        and how many of its requests still wait for an answer. All of it stops, or is
        closed, when the account is forgotten."""
        by_agent: dict[str, list[dict[str, Any]]] = {}
        for rule in self.rules():
            if rule["account"] == slug:
                by_agent.setdefault(rule["agent"], []).append(
                    {k: rule[k] for k in ("action", "tool", "label", "level", "effect")})
        waiting = [p for p in self.store.pending_list()
                   if p["account"] == slug and p["status"] == "pending"]
        return {"account": slug,
                "agents": [{"agent": agent, "rules": held}
                           for agent, held in sorted(by_agent.items())],
                "rules": sum(len(held) for held in by_agent.values()),
                "waiting_requests": len(waiting)}

    def forget_account(self, slug: str, *, actor: str) -> bool:
        """Forget an account: its rules go with it, and everything private kept for it
        is deleted for good."""
        known = self.account(slug) is not None
        if known:
            self.core.tape.print("hands.account_forgotten", {"account": slug}, actor=actor,
                                 subject=f"hand_account:{slug}")
        had_connect = self.store.connect_of(slug) is not None
        self.store.forget_account(slug)
        return known or had_connect

    async def actions(self, slug: str, *, refresh: bool = False) -> list[Action]:
        """What an account can do. Remembered for ``hands.actions_cache_s``; if the
        provider cannot be asked, the last list known is used."""
        cached = self.store.actions(slug)
        fresh_for = int(self.core.settings.get("hands.actions_cache_s")) * 1000
        if cached and not refresh and self.now() - cached[1] < fresh_for:
            return [Action.from_dict(a) for a in cached[0]]
        account = self.account(slug)
        try:
            if account is None:
                raise NotReady("no_account", f"The account \"{slug}\" is not connected.")
            provider = self.adapter(account["provider_id"])
            found = await provider.actions(self.provider_account(slug))
        except NotReady:
            if cached and account is not None:
                return [Action.from_dict(a) for a in cached[0]]
            raise
        except Exception as exc:  # noqa: BLE001 - an adapter's fault is an answer here
            log.warning("listing an account's actions failed: %s", type(exc).__name__)
            if cached:
                return [Action.from_dict(a) for a in cached[0]]
            raise NotReady("unreachable", "The provider could not be asked what this "
                                          "account can do. Try again later.") from None
        undo = can_undo(provider)
        found = [a if undo or not a.undo else Action.from_dict(a.as_dict() | {"undo": False})
                 for a in found]
        self.store.set_actions(slug, [a.as_dict() for a in found], self.now())
        return found

    def _type_of(self, slug: str) -> str:
        account = self.account(slug)
        provider = self.provider(account["provider_id"]) if account else None
        return provider.type if provider else ""

    def levels_for(self, *, agent: str, account: str, action: Action, effect: str,
                   limits: dict[str, Any], confirmed: bool) -> dict[str, dict[str, Any]]:
        """For each level, whether a rule for this agent and action may take it, and the
        sentence that says why not. Asks the same gate that saving does."""
        out = {}
        for level in rules.LEVELS:
            why = rules.may_set_level(self.core, agent=agent, account=account, action=action,
                                      effect=effect, level=level, limits=limits,
                                      confirmed=confirmed)
            out[level] = {"allowed": why is None, "reason": why}
        return out

    def _start(self, account: str, action: Action, effect: str, trusted: bool,
               agent: str = "") -> str:
        """The level a rule starts at. Mail that leaves an agent's own inbox starts at
        "alone" (its recipients decide), unless "alone" may not be set here: then "ask"."""
        level = rules.start_level(self.core, action, effect, trusted)
        if level == rules.ALONE and action.recipient_classes and rules.may_set_level(
                self.core, agent=agent, account=account, action=action, effect=effect,
                level=level, limits={}, confirmed=False):
            return rules.ASK
        return level

    async def describe_actions(self, slug: str, *, refresh: bool = False
                               ) -> list[dict[str, Any]]:
        out, kind = [], self._type_of(slug)
        for action in await self.actions(slug, refresh=refresh):
            reason = rules.offerable(action)
            trusted = hint_trusted(kind, action.effect_from)
            out.append(action.as_dict() | {
                "tool": rules.tool_name(slug, action.name),
                "can_offer": reason is None, "cannot_offer_reason": reason,
                "default_arguments": rules.default_arguments(action),
                # Whether the stated effect may be relied on to pick a level, the level
                # the form starts at, and what to tell the owner when the two differ.
                "effect_trusted": trusted,
                "default_level": self._start(slug, action, action.effect, trusted),
                "level_note": (rules.MAIL_NOTE if action.recipient_classes else
                               rules.level_note(action.effect, action.effect_from, trusted)),
                "cautious": rules.is_cautious(self.core, action.effect),
                "limits_offered": {"per_day": True, "recipients": bool(action.recipients),
                                   "max_amount": bool(action.amount)},
            })
        return sorted(out, key=lambda a: a["label"].lower())

    async def preflight(self, *, agent: str, account: str, action: str,
                        effect: str | None = None, limits: dict[str, Any] | None = None,
                        confirmed: bool = False) -> dict[str, Any]:
        """Before a rule is saved: which levels it may take for this agent and action,
        and for each one that it may not, why. Changes nothing. Raises ``Invalid``."""
        if not isinstance(agent, str) or not rules.AGENT.match(agent):
            raise Invalid("That is not an agent's id.")
        if self.account(account) is None:
            raise Invalid(f"There is no account called \"{account}\".")
        found = next((a for a in await self.actions(account) if a.name == action), None)
        if found is None:
            raise Invalid("That account has no such action.")
        before = self.rule(agent, account, action) or {}
        if effect is None and before.get("effect_from") == "owner":
            effect = before["effect"]
        effect, effect_from = rules.check_effect(found, effect)
        limits = rules.check_limits(
            found, limits if limits is not None else before.get("limits"))
        trusted = hint_trusted(self._type_of(account), effect_from)
        return {
            "agent": agent, "account": account, "action": action,
            "effect": effect, "effect_from": effect_from, "effect_trusted": trusted,
            "default_level": self._start(account, found, effect, trusted, agent=agent),
            "level_note": (rules.MAIL_NOTE if found.recipient_classes else
                           rules.level_note(effect, effect_from, trusted)),
            "cautious": rules.is_cautious(self.core, effect),
            "levels": self.levels_for(agent=agent, account=account, action=found,
                                      effect=effect, limits=limits, confirmed=confirmed),
        }

    # --- rules ----------------------------------------------------------------

    def rules(self, agent: str | None = None) -> list[dict[str, Any]]:
        found = [r for r in self.state()["rules"].values()
                 if agent is None or r["agent"] == agent]
        return sorted(found, key=lambda r: (r["agent"], r["tool"]))

    def rule(self, agent: str, account: str, action: str) -> dict[str, Any] | None:
        return self.state()["rules"].get(rules.rule_key(agent, account, action))

    def rule_by_tool(self, agent: str, tool: str) -> dict[str, Any] | None:
        return next((r for r in self.rules(agent) if r["tool"] == tool), None)

    async def set_rule(self, *, agent: str, account: str, action: str, actor: str,
                       level: str | None = None, arguments: dict[str, Any] | None = None,
                       limits: dict[str, Any] | None = None,
                       show_addresses: bool | None = None, fields: list[str] | None = None,
                       effect: str | None = None, version: str | None = None,
                       confirmed: bool = False) -> dict[str, Any]:
        """Switch one action on for one agent, or change how. What is not given keeps
        its value from the rule already there, else its default. Raises ``Invalid``."""
        if not isinstance(agent, str) or not rules.AGENT.match(agent):
            raise Invalid("That is not an agent's id.")
        held = self.account(account)
        if held is None:
            raise Invalid(f"There is no account called \"{account}\".")
        try:
            found = next((a for a in await self.actions(account) if a.name == action), None)
        except NotReady as exc:
            raise Invalid(exc.message) from None
        if found is None:
            raise Invalid("That account has no such action.")
        if version is not None and version != found.version:
            raise Invalid("This action changed at its provider since you looked at it. "
                          "Open it again and review it before switching it on.")
        before = self.rule(agent, account, action) or {}
        if effect is None and before.get("effect_from") == "owner":
            effect = before["effect"]
        effect, effect_from = rules.check_effect(found, effect)
        if level is None:
            level = before.get("level") or self._start(
                account, found, effect, hint_trusted(self._type_of(account), effect_from),
                agent=agent)
        if level not in rules.LEVELS:
            raise Invalid(f"The level must be one of: {', '.join(rules.LEVELS)}.")
        not_owner = rules.only_owner_raises(before, level, actor)
        if not_owner:
            raise Invalid(not_owner)
        modes = rules.check_arguments(
            found, arguments if arguments is not None else before.get("arguments"))
        reason = rules.offerable(found, modes)
        if reason:
            raise Invalid(f"This action cannot be offered yet: {reason}")
        for name, entry in modes.items():
            if entry["mode"] == rules.FIXED and scrub.looks_secret(
                    json.dumps(entry["value"], default=str)):
                raise Invalid(f"The fixed value of \"{name}\" looks like a key or a password. "
                              "A rule is not the place for one: save it with the provider.")
        limits = rules.check_limits(
            found, limits if limits is not None else before.get("limits"))
        refusal = rules.may_set_level(self.core, agent=agent, account=account, action=found,
                                      effect=effect, level=level, limits=limits,
                                      confirmed=bool(confirmed))
        if refusal:
            raise Invalid(refusal)
        if fields is None:
            fields = before.get("fields") or []
        if (not isinstance(fields, list) or len(fields) > MAX_FIELDS
                or not all(isinstance(f, str) and 0 < len(f) <= 200 for f in fields)):
            raise Invalid(f"The fields to show must be a list of at most {MAX_FIELDS} names.")
        if show_addresses is None:
            show_addresses = before.get("show_addresses", bool(held.get("own_inbox")))
        tool = rules.tool_name(account, action)
        mine = self.rules(agent)
        if any(r["tool"] == tool and r["action"] != action for r in mine):
            raise Invalid(f"Another action of this agent is already offered as {tool}. "
                          "Switch that one off first.")
        most = int(self.core.settings.get("hands.max_tools_per_agent"))
        if not before and len(mine) >= most:
            raise Invalid(f"Nothing was switched on: this agent already holds {most} "
                          "integration actions, which is the most allowed. Switch one off "
                          "first, or raise the number under Settings, Integrations.")
        rule = {
            "agent": agent, "account": account, "action": action, "tool": tool,
            "label": found.label, "level": level, "effect": effect, "effect_from": effect_from,
            "version": found.version, "arguments": modes,
            "show_addresses": bool(show_addresses), "fields": fields, "limits": limits,
            "confirmed": bool(confirmed),
        }
        self.core.tape.print("hands.rule_set", rule, actor=actor, subject=f"hand_rule:{agent}")
        return self.rule(agent, account, action)

    def lower_rule(self, agent: str, account: str, action: str, level: str, *,
                   actor: str) -> dict[str, Any] | None:
        """Take a rule down to a lower level, and nothing else about it. Anything may
        lower one; this never raises one. None when there was nothing to lower."""
        rule = self.rule(agent, account, action)
        if (rule is None or level not in rules.LEVELS
                or rules.LEVELS.index(level) >= rules.LEVELS.index(rule["level"])):
            return None
        self.core.tape.print("hands.rule_set", rule | {"level": level}, actor=actor,
                             subject=f"hand_rule:{agent}")
        return self.rule(agent, account, action)

    def tally(self, rule: dict[str, Any]) -> dict[str, Any]:
        """The owner's answers to this rule's cards so far, from the Bench's ladder, and
        whether enough yeses in a row suggest "Alone". A suggestion changes nothing."""
        from jimmyhq.bench import ladder  # noqa: PLC0415 - the Bench is only needed here

        where = rules.ladder_row(rule["agent"], rule["tool"], rule["effect"])
        row = ladder.row_of(self.core, where["actor"], where["tool"], where["action"])
        needed = int(self.core.settings.get("bench.promotion_streak"))
        return {"approvals": row["approvals"], "approvals_in_a_row": row["streak"],
                "suggest_after": needed,
                "suggest_alone": rule["level"] == rules.ASK and row["streak"] >= needed}

    def remove_rule(self, agent: str, account: str, action: str, *, actor: str) -> bool:
        if self.rule(agent, account, action) is None:
            return False
        self.core.tape.print("hands.rule_removed", {
            "agent": agent, "account": account, "action": action}, actor=actor,
            subject=f"hand_rule:{agent}")
        return True

    async def rule_state(self, rule: dict[str, Any]
                         ) -> tuple[str, str | None, Action | None]:
        """Whether a rule's tool can be offered right now: ``(state, why not, action)``.

        Rule ``hand_withdrawn_when_changed`` lives here: an action whose version is not
        the one the owner looked at is withdrawn until the owner reviews it."""
        account = self.account(rule["account"])
        if account is None:
            return ACCOUNT_GONE, "The account is no longer connected.", None
        try:
            self.adapter(account["provider_id"])
            self.provider_account(rule["account"])
            actions = await self.actions(rule["account"])
        except NotReady as exc:
            state = UNREACHABLE if exc.kind == "unreachable" else PROVIDER_OFF
            return state, exc.message, None
        action = next((a for a in actions if a.name == rule["action"]), None)
        if action is None:
            return ACTION_GONE, "The provider no longer offers this action.", None
        if action.version != rule["version"]:
            return CHANGED, ("This action changed at its provider after you switched it on. "
                             "Review it and switch it on again."), action
        reason = rules.offerable(action, rule["arguments"])
        if reason or set(rules.properties(action)[0]) != set(rule["arguments"]):
            return CANNOT_OFFER, reason or "Its arguments are not the ones you chose.", action
        return ON, None, action

    async def describe_rules(self, agent: str | None = None) -> list[dict[str, Any]]:
        out, now = [], self.now()
        for rule in self.rules(agent):
            state, reason, _ = await self.rule_state(rule)
            out.append(rule | self.tally(rule) | {
                "state": state, "reason": reason,
                "cautious": rules.is_cautious(self.core, rule["effect"]),
                "today": caps.action_today(self.core, self.store, rule["agent"],
                                           rule["account"], rule["action"], now)})
        return out

    async def tools_for(self, agent: str) -> list[ToolSpec]:
        """Rule ``hand_max_tools_per_agent``: the tools one agent is offered right now.
        Only rules whose action is unchanged and reachable, and never more than
        ``hands.max_tools_per_agent``."""
        out: list[ToolSpec] = []
        most = int(self.core.settings.get("hands.max_tools_per_agent"))
        for rule in self.rules(agent):
            if len(out) >= most:
                break
            state, _, action = await self.rule_state(rule)
            if state != ON or action is None:
                continue
            if rule["effect"] != rules.READ and rule["level"] == rules.READ_ONLY:
                continue  # set to look only: a changing action is not offered at all
            params, required = rules.tool_params(rule, action)
            words = rules.line(f"{action.label}. {action.description}", 600)
            if rule["level"] == rules.ASK:
                words += (" This does not happen at once: it is put to "
                          f"{caps.owner_name(self.core)} for a yes first.")
            out.append(ToolSpec(
                name=rule["tool"], description=words, params=params, required=required,
                agent=agent, account=rule["account"], action=rule["action"],
                label=action.label, effect=rule["effect"], level=rule["level"]))
        return out


def _supports(hands: Hands, provider_id: str, test: Any) -> bool:
    try:
        return bool(test(hands.adapter(provider_id)))
    except NotReady:
        return False


def _wanted(kind: Any, record: ProviderRecord) -> list[str]:
    """The names of the secrets a record refers to: one for each name in a ``names``
    field, and each ``{secret:NAME}`` in a list of arguments."""
    out: list[str] = []
    for spec in kind.fields:
        value = record.options.get(spec.name) if spec.where == "options" else None
        if spec.kind == "names" and isinstance(value, list):
            out += [f"{spec.secret_prefix or ''}{n.lower()}" for n in value
                    if isinstance(n, str)]
        elif spec.kind == "argv":
            out += record_options.secret_names(value)
    return sorted(set(out))


def _record(payload: dict[str, Any]) -> ProviderRecord:
    return ProviderRecord(id=payload["provider_id"],
                          **{k: v for k, v in payload.items() if k != "provider_id"})


def check_provider(record: dict[str, Any]) -> ProviderRecord:
    """Check a whole provider record as the owner typed it."""
    provider_id = record.get("id")
    if not isinstance(provider_id, str) or not ID.match(provider_id):
        raise Invalid("A provider id must start with a letter and use only lower-case "
                      "letters, digits, - and _ (40 at most).")
    kind = TYPES.get(str(record.get("type")))
    if kind is None:
        known = ", ".join(sorted(TYPES)) or "none are installed"
        raise Invalid(f"There is no such type of provider. The types are: {known}.")
    label = validate.text(record.get("label"), "The label", max_len=80)
    url = record.get("base_url")
    url = validate.base_url(url) if url not in (None, "") else None
    if kind.needs_address and url is None:
        raise Invalid(f"{kind.label} needs an address.")
    options = record.get("options") or {}
    try:
        written = json.dumps(options)
    except (TypeError, ValueError):
        written = None
    if not isinstance(options, dict) or written is None or len(written) > MAX_OPTIONS_CHARS:
        raise Invalid("The options must be a small set of plain values.")
    record_options.check_options(kind, options)
    return ProviderRecord(id=provider_id, type=kind.type, label=label, base_url=url,
                          enabled=bool(record.get("enabled", True)), options=options)
