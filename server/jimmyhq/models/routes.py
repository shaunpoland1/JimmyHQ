"""The HTTP API for the models module, under ``/api/models``.

Reading needs a read token or the owner. Everything that changes
configuration, touches a key or spends tokens needs the signed-in owner. No
response ever contains a key: only whether one is saved.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.models import validate
from jimmyhq.models.brain import Brain
from jimmyhq.models.shapes import ROLES, ModelError
from jimmyhq.models.validate import Invalid
from jimmyhq.web.deps import owner, reader

_UPSTREAM = 502  # the provider, not this server, is what failed


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderBody(_Body):
    id: str | None = Field(None, max_length=40)
    type: str = Field(max_length=40)
    label: str = Field(max_length=200)
    base_url: str | None = Field(None, max_length=600)
    enabled: bool = True
    local: bool | None = None
    options: dict[str, Any] | None = None
    region: str | None = Field(None, max_length=100)
    profile: str | None = Field(None, max_length=200)


class ProviderChange(_Body):
    """Only the fields sent are changed."""

    label: str | None = Field(None, max_length=200)
    base_url: str | None = Field(None, max_length=600)
    enabled: bool | None = None
    local: bool | None = None
    options: dict[str, Any] | None = None
    region: str | None = Field(None, max_length=100)
    profile: str | None = Field(None, max_length=200)


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


class RoleBody(_Body):
    primary: dict[str, Any] | None = None
    fallbacks: list[dict[str, Any]] = Field(default_factory=list, max_length=8)


class PricesBody(_Body):
    prices: list[dict[str, Any]] = Field(max_length=500)


class TryBody(_Body):
    role: str | None = Field(None, max_length=40)
    provider_id: str | None = Field(None, max_length=40)
    model: str | None = Field(None, max_length=validate.MAX_MODEL)
    prompt: str = Field(min_length=1, max_length=4000)
    local_only: bool = False

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("contains characters that cannot be sent") from None
        if not value.strip():
            raise ValueError("cannot be empty")
        return value


def build_router(core: Core, brain: Brain) -> APIRouter:
    router = APIRouter(prefix="/api/models", tags=["models"])

    def existing(provider_id: str):
        provider = brain.provider(provider_id) if validate.ID.match(provider_id) else None
        if provider is None:
            raise HTTPException(404, "There is no provider with that id.")
        return provider

    # --- providers ------------------------------------------------------------

    @router.get("/providers")
    def providers_list(_: Principal = Depends(reader)):  # noqa: B008
        # Read once, not once per provider.
        keys, health = set(core.secrets.names()), brain.last_health()
        return {"providers": [brain.describe(p, keys, health) for p in brain.providers()]}

    @router.post("/providers", status_code=201)
    def providers_create(body: ProviderBody, who: Principal = Depends(owner)):  # noqa: B008
        record = body.model_dump()
        try:
            record["id"] = record["id"] or validate.slug(
                validate.text(body.label, "The label", max_len=80))
            if brain.provider(record["id"]) is not None:
                raise HTTPException(
                    409, f"There is already a provider with the id '{record['id']}'. "
                         "Choose another id.")
            saved = brain.save_provider(record, actor=who.actor)
        except Invalid as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"provider": brain.describe(saved)}

    @router.put("/providers/{provider_id}")
    def providers_update(provider_id: str, body: ProviderChange,
                         who: Principal = Depends(owner)):  # noqa: B008
        record = existing(provider_id).as_dict()
        changes = body.model_dump(exclude_unset=True)
        for field in ("label", "enabled"):
            if field in changes and changes[field] is None:
                del changes[field]  # these cannot be cleared, so null means "leave it"
        moved = ("base_url" in changes
                 and (changes["base_url"] or "").strip().rstrip("/") != (record["base_url"] or ""))
        if moved and "local" not in changes:
            # A new address may be on someone else's machines. The old answer
            # to "does data stay home?" must not be carried over to it.
            changes["local"] = None
        record.update(changes)
        try:
            saved = brain.save_provider(record, actor=who.actor)
        except Invalid as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"provider": brain.describe(saved)}

    @router.delete("/providers/{provider_id}")
    def providers_delete(provider_id: str, who: Principal = Depends(owner)):  # noqa: B008
        provider = existing(provider_id)
        used_by = brain.roles_using(provider_id)
        if used_by:
            raise HTTPException(
                409, f"{provider.label} is used by the {', '.join(sorted(used_by))} role. "
                     "Point that role at another model first.")
        brain.remove_provider(provider_id, actor=who.actor)
        return {"ok": True}

    @router.put("/providers/{provider_id}/key")
    def key_set(provider_id: str, body: KeyBody, _: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        brain.set_key(provider_id, body.key)
        return {"id": provider_id, "has_key": True}

    @router.delete("/providers/{provider_id}/key")
    def key_clear(provider_id: str, _: Principal = Depends(owner)):  # noqa: B008
        existing(provider_id)
        brain.clear_key(provider_id)
        return {"id": provider_id, "has_key": False}

    @router.post("/providers/{provider_id}/test")
    async def providers_test(provider_id: str, who: Principal = Depends(owner)):  # noqa: B008
        try:
            return (await brain.test(provider_id, actor=who.actor)
                    if validate.ID.match(provider_id) else _gone())
        except KeyError:
            _gone()

    @router.get("/providers/{provider_id}/models")
    async def providers_models(
        provider_id: str, refresh: bool = Query(False),
        who: Principal = Depends(reader),  # noqa: B008
    ):
        try:
            if not validate.ID.match(provider_id):
                _gone()
            # Only the owner may force a fresh list: a refresh is an outbound
            # call carrying the provider's key. A read token gets the cached
            # list, renewed at most once per models.models_cache_s, and cannot
            # make a provider that was just checked be asked again.
            return await brain.list_models(provider_id, refresh=refresh and who.is_owner,
                                           recheck=who.is_owner)
        except KeyError:
            _gone()
        except ModelError as exc:
            # A provider with no key was never asked, so nothing upstream failed.
            status = 409 if exc.kind == "not_configured" else _UPSTREAM
            raise HTTPException(status, exc.message) from exc

    # --- roles ----------------------------------------------------------------

    @router.get("/roles")
    def roles_list(_: Principal = Depends(reader)):  # noqa: B008
        roles = brain.config().roles
        out = []
        for role in ROLES:
            targets = roles.get(role)
            out.append({
                "role": role,
                "primary": targets[0].as_dict() if targets else None,
                "fallbacks": [t.as_dict() for t in targets[1:]] if targets else [],
                # Unset roles other than embeddings use the default role's models.
                "inherits_default": (not targets and role not in ("default", "embeddings")
                                     and "default" in roles),
            })
        return {"roles": out}

    @router.put("/roles/{role}")
    def roles_set(role: str, body: RoleBody, who: Principal = Depends(owner)):  # noqa: B008
        if role not in ROLES:
            raise HTTPException(404, f"There is no role called that. Roles are: "
                                     f"{', '.join(ROLES)}.")
        try:
            primary = (validate.target(body.primary, "The first choice")
                       if body.primary is not None else None)
            fallbacks = [validate.target(f, "A fallback") for f in body.fallbacks]
            brain.set_role(role, primary, fallbacks, actor=who.actor)
        except Invalid as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"role": role, "primary": primary.as_dict() if primary else None,
                "fallbacks": [t.as_dict() for t in fallbacks]}

    # --- health, usage, prices ------------------------------------------------

    @router.get("/health")
    async def health(_: Principal = Depends(reader)):  # noqa: B008
        return await brain.health()

    @router.get("/usage")
    def usage(_: Principal = Depends(reader)):  # noqa: B008
        return brain.usage()

    @router.get("/prices")
    def prices_get(_: Principal = Depends(reader)):  # noqa: B008
        config = brain.config()
        return {"prices": config.prices, "is_default": config.prices_are_default}

    @router.put("/prices")
    def prices_set(body: PricesBody, who: Principal = Depends(owner)):  # noqa: B008
        try:
            return {"prices": brain.set_prices(body.prices, actor=who.actor),
                    "is_default": False}
        except Invalid as exc:
            raise HTTPException(422, str(exc)) from exc

    # --- trying a model -------------------------------------------------------

    @router.post("/try")
    async def try_model(body: TryBody, who: Principal = Depends(owner)):  # noqa: B008
        direct = body.provider_id is not None or body.model is not None
        if direct and (not body.provider_id or not body.model or not body.model.strip()):
            raise HTTPException(422, "Name both a provider and a model, or a role.")
        if direct and body.role is not None:
            raise HTTPException(422, "Name a role, or a provider and a model, not both.")
        if not direct and (body.role or "default") not in ROLES:
            raise HTTPException(422, f"There is no role called that. Roles are: "
                                     f"{', '.join(ROLES)}.")
        options: dict[str, Any] = (
            {"provider_id": body.provider_id, "model": body.model.strip()} if direct
            else {"role": body.role or "default"}
        )
        try:
            # A test prompt is not work worth keeping: only its counts are recorded.
            reply = await brain.chat(
                [{"role": "user", "content": body.prompt}], actor=who.actor,
                caller="models.try", keep_text=False, local_only=body.local_only, **options)
        except ModelError as exc:
            return {"ok": False, "error": exc.message, "reason": exc.kind, "reply": None}
        shown = reply.as_dict()
        shown.pop("data")
        return {"ok": True, "error": None, "reason": None, "reply": shown}

    return router


def _gone():
    raise HTTPException(404, "There is no provider with that id.")
