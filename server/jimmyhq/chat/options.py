"""What the composer needs to offer a choice of model, in one call.

Built from the models module's Python API. A provider that is known to be
down is not asked for its model list, so this stays quick.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jimmyhq.chat import recall, store
from jimmyhq.core import Core
from jimmyhq.ids import now_ms
from jimmyhq.models import brain
from jimmyhq.models.shapes import EFFORTS

_LIST_WAIT_S = 3.0
WEB_HELP = ("Lets the model search the web and read public pages for this reply. Searches "
            "go to your search provider, and what a page says is sent to the model that "
            "answers: with a hosted model, that means to its provider. Text from a page is "
            "treated as untrusted, and the sources are listed under the reply.")


def _facts(core: Core) -> tuple[Any, dict[str, dict], dict[str, Any]]:
    get = core.settings.get
    b = brain(core)
    config = b.config()
    # Whether a reply through each role, or by each provider, may be given
    # memory when the conversation is not "local only" (when it is, it always
    # may). The same rule the reply itself applies.
    memory_roles = {}
    for role in store.CHAT_ROLES:
        targets = config.roles.get(role) or config.roles.get("default") or []
        named = [config.providers[t.provider_id] for t in targets
                 if t.provider_id in config.providers]
        memory_roles[role] = recall.may_receive(core, named, False)
    memory_providers = {p.id: recall.may_receive(core, [p], False)
                        for p in config.providers.values()}
    return config, b.last_health(), {
        "use_memory": get("chat.use_memory"),
        "memory_to_hosted": get("chat.memory_to_hosted"),
        "memory_roles": memory_roles, "memory_providers": memory_providers,
        "health_interval_s": get("models.health_interval_s"),
        "local_only": get("chat.local_only"),
        "on_record": get("chat.on_record"),
        "max_message_chars": get("chat.max_message_chars"),
        "model_titles": get("chat.model_titles"),
        "web_enabled": get("lookup.enabled"),
        "web_default": get("lookup.chat_default"),
        "web_provider": get("lookup.search_provider"),
    }


async def _discovered(core: Core, provider_id: str) -> list[str]:
    try:
        # recheck=False: reading options never makes a failing provider be asked again.
        listing = brain(core).list_models(provider_id, recheck=False)
        found = await asyncio.wait_for(listing, _LIST_WAIT_S)
        return [m for m in found["models"] if isinstance(m, str)]
    except Exception:  # noqa: BLE001 - a provider that cannot list still has typed models
        return []


async def composer_options(core: Core) -> dict[str, Any]:
    config, health, settings = await asyncio.to_thread(_facts, core)
    enabled = sorted((p for p in config.providers.values() if p.enabled),
                     key=lambda p: p.label.lower())
    fresh = now_ms() - settings["health_interval_s"] * 1000
    ask = [p for p in enabled
           if not (health.get(p.id, {}).get("ok") is False
                   and (health[p.id].get("checked_ms") or 0) >= fresh)]
    lists = dict(zip((p.id for p in ask),
                     await asyncio.gather(*(_discovered(core, p.id) for p in ask)),
                     strict=True))
    typed: dict[str, set[str]] = {}
    for targets in config.roles.values():
        for target in targets:
            typed.setdefault(target.provider_id, set()).add(target.model)
    roles = []
    for role in store.CHAT_ROLES:
        own = config.roles.get(role)
        targets = own or config.roles.get("default") or []
        first = targets[0] if targets else None
        provider = config.providers.get(first.provider_id) if first else None
        roles.append({
            "role": role,
            "provider_id": first.provider_id if first else None,
            "provider_label": provider.label if provider else None,
            "model": first.model if first else None,
            "effort": first.effort if first else None,
            "local": provider.is_local if provider else None,
            "available": bool(provider and provider.enabled),
            "inherits_default": bool(not own and role != "default" and targets),
            "fallbacks": max(0, len(targets) - 1),
            # Memory may be sent when this role answers (outside "local only").
            "memory": settings["memory_roles"][role],
        })
    return {
        "roles": roles,
        "providers": [{
            "id": p.id, "label": p.label, "type": p.type, "local": p.is_local,
            "models": sorted(set(lists.get(p.id, [])) | typed.get(p.id, set())),
            "listed": bool(lists.get(p.id)),
            "memory": settings["memory_providers"][p.id],
        } for p in enabled],
        "efforts": list(EFFORTS),
        "defaults": {"target": dict(store.DEFAULT_TARGET), "effort": None,
                     "local_only": settings["local_only"],
                     "on_record": settings["on_record"],
                     "use_memory": settings["use_memory"]},
        # Whether the owner lets hosted models see memory. With this off, a
        # target's own "memory" flag says whether it may be sent memory; a
        # "local only" conversation always may.
        "memory": {"to_hosted": settings["memory_to_hosted"]},
        # Web lookup, a choice made with each message: "web": true or false
        # on a send. "enabled" is whether it can have any effect; "default"
        # is how the switch starts out; "search" is whether a search provider
        # is chosen (without one a model can still read a page by address).
        "web": {"enabled": settings["web_enabled"],
                "default": bool(settings["web_enabled"] and settings["web_default"]),
                "search": settings["web_provider"] != "none",
                "help": WEB_HELP},
        "max_message_chars": settings["max_message_chars"],
        "model_titles": settings["model_titles"],
    }
