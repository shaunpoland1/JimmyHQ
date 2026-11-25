"""The owner's API for integrations, under ``/api/hands``.

Lists need a read token or the owner. Everything that changes a provider, an account or
a rule, touches a key or makes a call needs the signed-in owner. No response ever
contains a key: only whether one is saved.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.hands import calls
from jimmyhq.hands.provider import ID, TYPES
from jimmyhq.hands.rules import AGENT, SLUG, Invalid
from jimmyhq.hands.service import KEY, SECRET_NAME, Hands, NotReady
from jimmyhq.models import validate
from jimmyhq.web.deps import owner, reader

log = logging.getLogger("jimmyhq.hands")

_UPSTREAM = 502  # the provider, not this server, is what failed
_NOT_READY = 409
MAX_ACTION = 300


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderBody(_Body):
    id: str | None = Field(None, max_length=40)
    type: str = Field(max_length=40)
    label: str = Field(max_length=200)
    base_url: str | None = Field(None, max_length=600)
    enabled: bool = True
    options: dict[str, Any] | None = None


class ProviderChange(_Body):
    """Only the fields sent are changed."""

    label: str | None = Field(None, max_length=200)
    base_url: str | None = Field(None, max_length=600)
    enabled: bool | None = None
    options: dict[str, Any] | None = None


class KeyBody(_Body):
    key: str = Field(min_length=1, max_length=8192)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        value = value.strip()
        # A key travels in an HTTP header, so it must be plain visible ASCII.
        if not value or any(not 0x21 <= ord(c) <= 0x7E for c in value):
            raise ValueError("cannot be empty or contain spaces or unusual characters")
        return value


class ConnectBody(_Body):
    provider_id: str = Field(max_length=40)
    account: str = Field(max_length=24)
    label: str = Field("", max_length=200)
    app: str | None = Field(None, max_length=80)
    own_inbox: bool = False


class TryBody(_Body):
    action: str = Field(min_length=1, max_length=MAX_ACTION)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PreflightBody(_Body):
    """A rule as the form has it so far. Nothing is saved."""

    agent: str = Field(max_length=64)
    account: str = Field(max_length=24)
    action: str = Field(min_length=1, max_length=MAX_ACTION)
    limits: dict[str, Any] | None = None
    effect: str | None = Field(None, max_length=20)
    confirmed: bool = False


class RuleBody(_Body):
    """What is left out keeps its value from the rule already there, else its default."""

    agent: str = Field(max_length=64)
    account: str = Field(max_length=24)
    action: str = Field(min_length=1, max_length=MAX_ACTION)
    level: str | None = Field(None, max_length=10)
    arguments: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    show_addresses: bool | None = None
    fields: list[str] | None = None
    effect: str | None = Field(None, max_length=20)
    version: str | None = Field(None, max_length=200)
    confirmed: bool = False


def build_router(core: Core, hands: Hands) -> APIRouter:  # noqa: C901, PLR0915 - one router
    router = APIRouter(prefix="/api/hands", tags=["hands"])

    def existing(provider_id: str):
        record = hands.provider(provider_id) if ID.match(provider_id) else None
        if record is None:
            raise HTTPException(404, "There is no provider with that id.")
        return record

    def held(account: str) -> dict[str, Any]:
        found = hands.account(account) if SLUG.match(account) else None
        if found is None:
            raise HTTPException(404, "There is no account with that name.")
        return found

    async def asked(doing: Any) -> Any:
        """Run something that asks a provider, turning its failures into answers."""
        try:
            return await doing
        except Invalid as exc:
            raise HTTPException(422, str(exc)) from exc
        except NotReady as exc:
            raise HTTPException(_NOT_READY, exc.message) from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - an adapter's fault, not this server's
            log.warning("a provider call from the API failed: %s", type(exc).__name__)
            raise HTTPException(_UPSTREAM, "The provider could not be reached, or gave an "
                                           "answer that made no sense. Try again.") from exc

    # --- providers ------------------------------------------------------------

    @router.get("/types")
    def types_list(_: Principal = Depends(reader)):  # noqa: B008
        """Each type with what the screen needs to offer it: its label and description,
        the fields its record takes, whether it needs a key, where sign-in tokens live."""
        return {"types": [t.as_dict()
                          for t in sorted(TYPES.values(), key=lambda t: t.label.lower())]}

    @router.get("/providers")
    def providers_list(_: Principal = Depends(reader)):  # noqa: B008
        names, health = set(core.secrets.names()), hands.store.health()
        return {"providers": [hands.describe_provider(p, names, health)
                              for p in hands.providers()]}

    @router.post("/providers", status_code=201)
    def providers_create(body: ProviderBody, who: Principal = Depends(owner)):  # noqa: B008
        record = body.model_dump()
        try:
            record["id"] = record["id"] or validate.slug(
                validate.text(body.label, "The label", max_len=80))
            if ID.match(record["id"]) and hands.provider(record["id"]) is not None:
                raise HTTPException(
                    409, f"There is already a provider with the id '{record['id']}'. "
                         "Choose another id.")
            saved = hands.save_provider(record, actor=who.actor)
        except Invalid as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"provider": hands.describe_provider(saved)}

    @router.put("/providers/{provider_id}")
    def providers_update(provider_id: str, body: ProviderChange,
                         who: Principal = Depends(owner)):  # noqa: B008
        record = existing(provider_id).as_dict()
        changes = body.model_dump(exclude_unset=True)
        for name in ("label", "enabled", "options"):
            if name in changes and changes[name] is None:
                del changes[name]  # these cannot be cleared, so null means "leave it"
        record.update(changes)
        try:
            saved = hands.save_provider(record, actor=who.actor)
        except Invalid as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"provider": hands.describe_provider(saved)}

    @router.delete("/providers/{provider_id}")
    def providers_delete(provider_id: str, who: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        try:
            hands.remove_provider(provider_id, actor=who.actor)
        except Invalid as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    def secret(name: str) -> str:
        if not SECRET_NAME.match(name):
            raise HTTPException(404, "A secret's name uses lower-case letters, digits, - "
                                     "and _.")
        return name

    @router.put("/providers/{provider_id}/key")
    def key_set(provider_id: str, body: KeyBody, _: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        hands.set_secret(provider_id, body.key)
        return {"id": provider_id, "has_key": True}

    @router.delete("/providers/{provider_id}/key")
    def key_clear(provider_id: str, _: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        hands.clear_secret(provider_id)
        return {"id": provider_id, "has_key": False}

    @router.put("/providers/{provider_id}/secrets/{name}")
    def secret_set(provider_id: str, name: str, body: KeyBody,
                   _: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        if secret(name) == KEY:
            raise HTTPException(422, "The provider's own key is saved with its key field.")
        hands.set_secret(provider_id, body.key, name)
        return {"id": provider_id, "name": name, "saved": True}

    @router.delete("/providers/{provider_id}/secrets/{name}")
    def secret_clear(provider_id: str, name: str, _: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        hands.clear_secret(provider_id, secret(name))
        return {"id": provider_id, "name": name, "saved": False}

    @router.post("/providers/{provider_id}/check")
    async def providers_check(provider_id: str, _: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        return await hands.check(provider_id)

    # --- accounts -------------------------------------------------------------

    @router.get("/accounts")
    def accounts_list(_: Principal = Depends(reader)):  # noqa: B008
        return {"accounts": [hands.describe_account(a) for a in hands.accounts()],
                "connecting": [{"account": c["account"], "provider_id": c["provider_id"],
                                "label": c["details"]["label"], "started_ms": c["created_ms"]}
                               for c in hands.store.connects()]}

    @router.post("/accounts")
    async def accounts_connect(body: ConnectBody, who: Principal = Depends(owner)):  # noqa: B008
        existing(body.provider_id)
        return await asked(hands.connect(
            provider_id=body.provider_id, slug=body.account, label=body.label, app=body.app,
            own_inbox=body.own_inbox, actor=who.actor))

    @router.post("/accounts/{account}/finish")
    async def accounts_finish(account: str, who: Principal = Depends(owner)):  # noqa: B008
        if not SLUG.match(account):
            raise HTTPException(404, "There is no account with that name.")
        return await asked(hands.finish_connect(account, actor=who.actor))

    @router.delete("/accounts/{account}/connect")
    def accounts_cancel(account: str, _: Principal = Depends(owner)):  # noqa: B008
        """Give up a connection that was started and never finished. An account that is
        already connected is not touched: forget it instead."""
        if not SLUG.match(account) or not hands.cancel_connect(account):
            raise HTTPException(404, "No account with that name is being connected.")
        return {"ok": True}

    @router.get("/accounts/{account}/dependents")
    def accounts_dependents(account: str, _: Principal = Depends(reader)):  # noqa: B008
        """Which agents' rules depend on this account: what forgetting it would stop."""
        held(account)
        return hands.dependents(account)

    @router.delete("/accounts/{account}")
    def accounts_forget(account: str, who: Principal = Depends(owner)):  # noqa: B008
        if not SLUG.match(account) or not hands.forget_account(account, actor=who.actor):
            raise HTTPException(404, "There is no account with that name.")
        return {"ok": True}

    @router.get("/accounts/{account}/actions")
    async def accounts_actions(
        account: str, refresh: bool = Query(False),
        who: Principal = Depends(reader),  # noqa: B008
    ):
        held(account)
        # Only the owner may force a fresh list: a refresh is an outbound call carrying
        # the provider's key.
        return {"actions": await asked(hands.describe_actions(
            account, refresh=refresh and who.is_owner))}

    @router.post("/accounts/{account}/try")
    async def accounts_try(account: str, body: TryBody,
                           who: Principal = Depends(owner)):  # noqa: B008
        held(account)
        return await asked(calls.try_read(hands, account, body.action, body.arguments,
                                          actor=who.actor))

    # --- rules ----------------------------------------------------------------

    @router.get("/rules")
    async def rules_list(agent: str | None = Query(None, max_length=64),
                         _: Principal = Depends(reader)):  # noqa: B008
        if agent is not None and not AGENT.match(agent):
            raise HTTPException(422, "That is not an agent's id.")
        return {"rules": await hands.describe_rules(agent),
                "max_tools_per_agent": core.settings.get("hands.max_tools_per_agent")}

    @router.post("/rules/preflight")
    async def rules_preflight(body: PreflightBody, _: Principal = Depends(owner)):  # noqa: B008
        """Which levels this agent's rule may take for this action, and if not, why.
        Saves nothing; the form asks before the owner does."""
        return await asked(hands.preflight(**body.model_dump()))

    @router.put("/rules")
    async def rules_set(body: RuleBody, who: Principal = Depends(owner)):  # noqa: B008
        return {"rule": await asked(hands.set_rule(actor=who.actor, **body.model_dump()))}

    @router.delete("/rules")
    def rules_remove(agent: str = Query(max_length=64), account: str = Query(max_length=24),
                     action: str = Query(max_length=MAX_ACTION),
                     who: Principal = Depends(owner)):  # noqa: B008
        if not hands.remove_rule(agent, account, action, actor=who.actor):
            raise HTTPException(404, "That action is not switched on for that agent.")
        return {"ok": True}

    # --- the ledger -----------------------------------------------------------

    @router.get("/runs")
    def runs_list(agent: str | None = Query(None, max_length=64),
                  limit: int = Query(50, ge=1, le=500),
                  _: Principal = Depends(reader)):  # noqa: B008
        """Recent runs, newest first: names, times and how each ended. Never a value."""
        shown = ("id", "agent", "account", "provider_id", "action", "tool", "effect", "level",
                 "status", "started_ms", "done_ms", "error_kind", "card_id", "unknown_card")
        found = hands.store.runs(agent=agent, limit=limit)
        done = hands.store.cards_of([r["id"] for r in found], "hand.done")
        # ``card`` is the one card to open for this run: what it left on the Bench.
        return {"runs": [{k: r[k] for k in shown} | {
            "done_card": done.get(r["id"]),
            "card": done.get(r["id"]) or r["unknown_card"] or r["card_id"]} for r in found]}

    # --- requests that wait for the owner -------------------------------------

    @router.get("/requests/by-card/{card_id}")
    def request_values(card_id: str, _: Principal = Depends(owner)):  # noqa: B008
        """What a waiting request would send, for the owner's screen beside its Bench
        card (``calls.request_values``). Only the signed-in owner: a token is refused."""
        found = calls.request_values(hands, card_id)
        if found is None:
            raise HTTPException(404, "No request is tied to that card. Open the card from "
                                     "the Bench to see what it says.")
        return found

    return router

