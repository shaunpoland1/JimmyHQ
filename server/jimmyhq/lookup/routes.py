"""Web lookup over HTTP, under ``/api/lookup``. Who may do what:

- reading the status (whether it is on, which provider, whether a key is
  saved; never a key itself): the owner or a read token;
- saving or clearing a provider's key, and trying a search: the signed-in
  owner only.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.lookup import searching
from jimmyhq.lookup.errors import WebError
from jimmyhq.lookup.settings import PROVIDERS
from jimmyhq.web.deps import owner, reader

MAX_KEY = 500


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyBody(_Body):
    key: str = Field(min_length=1, max_length=MAX_KEY)


class TryBody(_Body):
    query: str = Field(min_length=1, max_length=searching.MAX_QUERY)


def status(core: Core) -> dict[str, Any]:
    """What the settings screen and the chat composer need to know. A read of
    settings and of which secrets exist; nothing is contacted."""
    get = core.settings.get
    chosen = get("lookup.search_provider")
    saved = set(core.secrets.names())
    providers = []
    for provider_id, label in PROVIDERS:
        kind = searching.BACKENDS.get(provider_id)
        providers.append({
            "id": provider_id, "label": label,
            "needs_key": bool(kind and kind.needs_key),
            "key_set": bool(kind and kind.needs_key
                            and searching.secret_name(provider_id) in saved),
        })
    problem = None
    try:
        searching.backend(core)
    except WebError as exc:
        problem = exc.message
    current = next(p for p in providers if p["id"] == chosen)
    return {
        "enabled": get("lookup.enabled"),
        "provider": chosen, "provider_label": current["label"],
        "needs_key": current["needs_key"], "key_set": current["key_set"],
        "search_ready": problem is None,
        "search_problem": problem,  # a sentence that says what to set up, or None
        "chat_default": get("lookup.chat_default"),
        "providers": providers,
    }


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/lookup", tags=["lookup"])

    def keyed(provider_id: str) -> str:
        kind = searching.BACKENDS.get(provider_id)
        if kind is None:
            raise HTTPException(404, "There is no search provider with this id. Reload the "
                                     "page.")
        if not kind.needs_key:
            raise HTTPException(422, f"{kind.label} takes no key. Set its address under "
                                     "Settings, Web lookup.")
        return searching.secret_name(provider_id)

    @router.get("/status")
    def read_status(_: Principal = Depends(reader)):  # noqa: B008
        return status(core)

    @router.put("/providers/{provider_id}/key")
    def set_key(provider_id: str, body: KeyBody, _: Principal = Depends(owner)):  # noqa: B008
        name = keyed(provider_id)
        key = body.key.strip()
        if not key or any(ord(ch) <= 32 or ord(ch) == 127 for ch in key):
            raise HTTPException(422, "A key is one run of characters with no spaces. Paste "
                                     "it again.")
        core.secrets.set(name, key)
        return status(core)

    @router.delete("/providers/{provider_id}/key")
    def clear_key(provider_id: str, _: Principal = Depends(owner)):  # noqa: B008
        core.secrets.delete(keyed(provider_id))
        return status(core)

    @router.post("/try")
    async def try_search(body: TryBody, who: Principal = Depends(owner)):  # noqa: B008
        # The owner's own click, so it works before web lookup is switched on
        # for models: it is how the provider and its key are checked first.
        started = time.monotonic()
        try:
            found = await searching.search(core, body.query, actor=who.actor,
                                           caller="lookup.try", owner_asked=True)
        except WebError as exc:
            raise HTTPException(exc.status, exc.message) from None
        current = await run_in_threadpool(status, core)
        return {"provider": current["provider"], "provider_label": current["provider_label"],
                "results": [r.as_dict() for r in found],
                "duration_ms": int((time.monotonic() - started) * 1000)}

    return router
