"""The Brain: the one place the product talks to language models.

Other modules ask for a role and get an answer and a receipt. The Brain picks
the provider, falls back when that is allowed, and prints what happened to the
Tape. Nothing here is kept in memory between calls: configuration is read
through a lens, keys from the secret store, and health from a small shared
file, so the web and worker processes always agree.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from jimmyhq.core import Core
from jimmyhq.ids import now_ms
from jimmyhq.models import prices as pricing
from jimmyhq.models import validate
from jimmyhq.models.adapters import ADAPTERS, Adapter, Timeouts, mask
from jimmyhq.models.adapters.bedrock import BedrockAdapter, ClientFactory
from jimmyhq.models.lenses import ConfigLens, UsageLens
from jimmyhq.models.shapes import (
    IMAGE_TYPES,
    KEY_REQUIRED_TYPES,
    ROLES,
    TOOL_CHOICES,
    ChatRequest,
    EmbedReply,
    Message,
    ModelError,
    Provider,
    RawReply,
    Reply,
    Target,
)
from jimmyhq.models.state import StateStore
from jimmyhq.models.validate import Invalid

log = logging.getLogger("jimmyhq.models")

MAX_MESSAGES = 2000
MAX_CHARS = 8_000_000  # all message text in one request
MAX_EMBED_TEXTS = 4096  # texts in one embed call
EMBED_BATCH = 64  # texts sent to a provider in one request
EMBED_BATCH_CHARS = 200_000  # and at most this much text in one request
MAX_IMAGES = 100  # images in one request, counting those inside tool results
MAX_TOOLS = 128  # tools offered in one request
MAX_TOOL_CHARS = 1_000_000  # all tool definitions in one request, as JSON
# What every provider accepts as a tool name.
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOOL_ID = re.compile(r"^[\x21-\x7e]{1,256}$")
# How each image type's file begins, so a mislabelled file is caught here
# with a clear message instead of at the provider with an unclear one.
_IMAGE_STARTS = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff",
                 "image/webp": b"RIFF"}
# A rejection that says the model or its server cannot do something, in the words
# providers use for it (OpenAI-compatible servers such as vLLM, Ollama and llama.cpp,
# Anthropic, Google, Bedrock). The mere mention of a tool, function or image is not
# enough: a context-length, size, rate or key error mentions them too, and reading one
# of those as "cannot" would send the owner's content to another provider.
_CANNOT = r"(?:(?:does|do|did)\s+not|doesn't|don't|is\s+not|isn't|are\s+not|aren't|cannot|can't)"
_IS_NOT = r"(?:(?:is|are)\s+not|isn't|aren't)\s+(?:currently\s+)?(?:supported|enabled)"
_THE = r"(?:(?:for|the|any|native|parallel|forced|streaming)\s+)*"
_TOOL_WORDS = (r"(?:tool\s+(?:use|calling|calls?|choice)\b|tool[\w.]*"
               r"|function[\s_-]?(?:calling|calls?)\b|functions?\b)")
_IMAGE_WORDS = (r"(?:images?(?![\w/])(?!\s+(?:format|type|size|dimension|file|url|data|media))"
                r"|vision|multi-?modal\w*)")
_ABOUT_TOOLS = re.compile(rf"""(?xi)
      {_CANNOT}\s+(?:currently\s+|yet\s+)?support(?:ed)?\s+{_THE}{_TOOL_WORDS}
    | {_TOOL_WORDS}[^.\n]{{0,60}}?(?:{_IS_NOT}|(?:is|are)\s+unsupported)
    | requires\s+--(?:enable-auto-tool-choice|tool-call-parser|jinja)
    | forces\s+tool\s+use
""")
_ABOUT_IMAGES = re.compile(rf"""(?xi)
      {_CANNOT}\s+(?:currently\s+|yet\s+)?support(?:ed)?\s+{_THE}{_IMAGE_WORDS}
    | (?:images|vision|multi-?modal\w*
        |image\s+(?:input|inputs|content|blocks?|understanding)(?:\s+modality)?)
      \s+(?:{_IS_NOT}|(?:is|are)\s+unsupported)
    | at\s+most\s+0\s+image
    | missing\s+data\s+required\s+for\s+image\s+input
    | image_url\W+is\s+only\s+supported\s+by\s+certain\s+models
""")
_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*)\n\s*```\s*$", re.DOTALL)
_JSON_ONLY = ("Answer with only a JSON value that matches this JSON Schema. No explanation, "
              "no code fence, nothing before or after it.\n")


# Where a provider is: what was learned about one place says nothing about another.
_PLACE = ("type", "base_url", "region", "profile")


def secret_name(provider_id: str) -> str:
    return f"models.{provider_id}.api_key"


@dataclass(frozen=True)
class Config:
    providers: dict[str, Provider]
    roles: dict[str, list[Target]]  # primary first, then the fallbacks in order
    prices: list[dict[str, Any]]
    prices_are_default: bool


@dataclass(frozen=True)
class _Plan:
    """One request, checked and resolved, before any provider is contacted."""

    messages: list[tuple[str, str | list[dict[str, Any]]]]
    system: str | None
    targets: list[Target]
    providers: dict[str, Provider]
    role: str | None
    effort: str | None
    max_tokens: int | None
    temperature: float | None
    json_schema: dict | None
    actor: str
    caller: str
    local_only: bool
    no_fallback: bool
    keep_text: bool
    cause: str | None
    root: str | None
    timeouts: Timeouts
    default_max_tokens: int
    embedding: bool = False
    tools: tuple[dict[str, Any], ...] = ()
    tool_choice: str | dict[str, str] = "auto"
    images: int = 0
    image_bytes: int = 0


def _bad(message: str) -> ModelError:
    return ModelError("bad_request", message)


class Brain:
    def __init__(self, core: Core):
        self.core = core
        self._config_lens = ConfigLens()
        self._usage_lens = UsageLens()
        self._state = StateStore(core.config.data / "models.db")
        # Test seams: a fake HTTP transport, and a fake boto3 client factory.
        self.transport: httpx.AsyncBaseTransport | None = None
        self.bedrock_clients: ClientFactory | None = None

    # --- configuration --------------------------------------------------------

    def config(self) -> Config:
        state = self.core.lenses.read(self._config_lens)
        providers = {
            pid: Provider(id=pid, **{k: v for k, v in p.items() if k != "provider_id"})
            for pid, p in state["providers"].items()
        }
        roles = {
            role: [Target(**t) for t in (entry["primary"], *entry["fallbacks"])]
            for role, entry in state["roles"].items()
        }
        own = state["prices"]
        return Config(providers, roles, pricing.default_prices() if own is None else own,
                      own is None)

    def providers(self) -> list[Provider]:
        return sorted(self.config().providers.values(), key=lambda p: p.label.lower())

    def provider(self, provider_id: str) -> Provider | None:
        return self.config().providers.get(provider_id)

    def last_health(self) -> dict[str, dict]:
        """What is known about each provider's reachability, without checking again."""
        return self._state.health()

    def describe(self, provider: Provider, keys: set[str] | None = None,
                 health: dict[str, dict] | None = None) -> dict[str, Any]:
        """A provider as the API shows it: whether a key is set, never the key."""
        keys = set(self.core.secrets.names()) if keys is None else keys
        has_key = secret_name(provider.id) in keys
        needs_key = provider.type in KEY_REQUIRED_TYPES
        if needs_key and not has_key:
            seen = _not_set_up(provider)
        else:
            health = self._state.health() if health is None else health
            seen = health.get(provider.id) or {"ok": None, "checked_ms": None, "error": None}
        return provider.as_dict() | {"has_key": has_key, "needs_key": needs_key, "health": seen}

    def missing_key(self, provider: Provider) -> ModelError | None:
        """The error for a provider that needs a key and has none, else None.

        Such a provider is never contacted: not for health, a model list, a
        test or a chat.
        """
        if provider.type in KEY_REQUIRED_TYPES and not self.core.secrets.get(
                secret_name(provider.id)):
            return _no_key(provider)
        return None

    def save_provider(self, record: dict[str, Any], *, actor: str) -> Provider:
        """Add or change a provider. Raises ``Invalid`` with what to fix."""
        provider = validate.provider(record)
        before = self.provider(provider.id)
        payload = provider.as_dict()
        payload["provider_id"] = payload.pop("id")
        self.core.tape.print("model.provider.saved", payload, actor=actor,
                             subject=f"model_provider:{provider.id}")
        if before is not None and any(getattr(before, f) != getattr(provider, f)
                                      for f in _PLACE):
            # What was learned about the old address or account says nothing
            # about the new one.
            self._state.forget(provider.id)
        return provider

    def roles_using(self, provider_id: str) -> list[str]:
        return [role for role, targets in self.config().roles.items()
                if any(t.provider_id == provider_id for t in targets)]

    def remove_provider(self, provider_id: str, *, actor: str) -> None:
        self.core.tape.print("model.provider.removed", {"provider_id": provider_id}, actor=actor,
                             subject=f"model_provider:{provider_id}")
        self.core.secrets.delete(secret_name(provider_id))
        self._state.forget(provider_id)

    def set_key(self, provider_id: str, key: str) -> None:
        self.core.secrets.set(secret_name(provider_id), key)
        self._state.forget(provider_id)  # the last check was made with the old key

    def clear_key(self, provider_id: str) -> bool:
        removed = self.core.secrets.delete(secret_name(provider_id))
        self._state.forget(provider_id)
        return removed

    def set_role(self, role: str, primary: Target | None, fallbacks: Sequence[Target] = (),
                 *, actor: str) -> None:
        """Point a role at a model. Every provider named must exist and be on."""
        if role not in ROLES:
            raise Invalid(f"There is no role called '{role}'. Roles are: {', '.join(ROLES)}.")
        if primary is None and fallbacks:
            raise Invalid("A role needs a first choice before it can have fallbacks.")
        if len(fallbacks) > 8:
            raise Invalid("A role can have at most 8 fallbacks.")
        known = self.config().providers
        for target in (*([primary] if primary else []), *fallbacks):
            provider = known.get(target.provider_id)
            if provider is None:
                raise Invalid(f"There is no provider called '{target.provider_id}'.")
            if not provider.enabled:
                raise Invalid(f"{provider.label} is switched off. Switch it on before "
                              "giving it a role.")
        self.core.tape.print("model.role.set", {
            "role": role,
            "primary": primary.as_dict() if primary else None,
            "fallbacks": [t.as_dict() for t in fallbacks],
        }, actor=actor, subject=f"model_role:{role}")

    def set_prices(self, entries: Any, *, actor: str) -> list[dict[str, Any]]:
        table = validate.prices(entries)
        self.core.tape.print("model.prices.set", {"prices": table}, actor=actor)
        return table

    def timeouts(self) -> Timeouts:
        get = self.core.settings.get
        return Timeouts(get("models.connect_timeout_s"), get("models.read_timeout_s"))

    def adapter(self, provider: Provider, timeouts: Timeouts | None = None) -> Adapter:
        key = self.core.secrets.get(secret_name(provider.id))
        timeouts = timeouts or self.timeouts()
        if provider.type == "bedrock":
            return BedrockAdapter(provider, key, timeouts, client_factory=self.bedrock_clients)
        return ADAPTERS[provider.type](provider, key, timeouts, self.transport)

    # --- discovery and health -------------------------------------------------

    async def list_models(self, provider_id: str, *, refresh: bool = False,
                          recheck: bool = True) -> dict[str, Any]:
        """A provider's model ids, from a short-lived cache unless ``refresh``.

        ``recheck=False`` is for a caller who is not the owner. A provider that
        was checked within the health interval is not asked again for them:
        they get the list already held, however old, or the last error. So
        they cannot make a provider that is failing be asked over and over.
        """
        provider, ttl_s, cached, seen, interval_s = await asyncio.to_thread(
            self._models_lookup, provider_id)
        now = now_ms()
        if cached and not refresh and now - cached[1] < ttl_s * 1000:
            return {"provider_id": provider_id, "models": cached[0], "cached": True,
                    "fetched_ms": cached[1]}
        if not recheck and seen and now - seen["checked_ms"] < interval_s * 1000:
            if cached:
                return {"provider_id": provider_id, "models": cached[0], "cached": True,
                        "fetched_ms": cached[1]}
            raise ModelError(
                "unavailable",
                seen["error"] or f"{provider.label} was checked a moment ago and gave no "
                                 "model list. Try again shortly.")
        models = await self._discover(provider)
        fetched = await asyncio.to_thread(self._state.models, provider_id)
        return {"provider_id": provider_id, "models": models, "cached": False,
                "fetched_ms": fetched[1] if fetched else now_ms()}

    def _models_lookup(self, provider_id: str):
        provider = self.provider(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        no_key = self.missing_key(provider)
        if no_key is not None:
            raise no_key
        get = self.core.settings.get
        return (provider, get("models.models_cache_s"), self._state.models(provider_id),
                self._state.health().get(provider_id), get("models.health_interval_s"))

    async def _discover(self, provider: Provider, timeouts: Timeouts | None = None) -> list[str]:
        """List models for real, and remember both the list and that it worked."""
        no_key = await asyncio.to_thread(self.missing_key, provider)
        if no_key is not None:
            raise no_key
        key = await asyncio.to_thread(self.core.secrets.get, secret_name(provider.id))
        adapter = await asyncio.to_thread(self.adapter, provider, timeouts)
        try:
            models = await adapter.list_models()
        except ModelError as exc:
            # A provider that answers "no such address" is up; it just has no
            # model list. Calling that unreachable would show a working
            # provider as down on every check.
            reachable = exc.kind == "not_found"
            await asyncio.to_thread(self._learned, provider, key, reachable,
                                    None if reachable else exc.message)
            raise
        except Exception as exc:  # noqa: BLE001 - an odd reply must not become a 500
            log.warning("listing models on %s failed: %s", provider.id, type(exc).__name__)
            error = adapter.error("bad_reply")
            await asyncio.to_thread(self._learned, provider, key, False, error.message)
            raise error from None
        await asyncio.to_thread(self._learned, provider, key, True, None, models)
        return models

    def _moved(self, provider: Provider) -> bool:
        """Whether a provider was removed or pointed somewhere else since it was read."""
        now = self.provider(provider.id)
        return now is None or any(getattr(now, f) != getattr(provider, f) for f in _PLACE)

    def _learned(self, provider: Provider, key: str | None, ok: bool, error: str | None = None,
                 models: list[str] | None = None) -> None:
        """Remember what a check found, unless it is no longer about this provider.

        A check can still be on its way when the owner changes the address or
        the key. Its answer is about the old one and is dropped.
        """
        if self._moved(provider) or self.core.secrets.get(secret_name(provider.id)) != key:
            return
        stamp = now_ms()
        if models is not None:
            self._state.set_models(provider.id, models, stamp)
        self._state.set_health(provider.id, ok, stamp, error)

    async def health(self) -> dict[str, Any]:
        """Each enabled provider: reachable or not, and when that was last checked.

        A check lists models and never sends a prompt. It runs when this is
        read, not in the background, and results are reused for the health
        interval, so polling this is cheap. A provider that needs a key and
        has none is not contacted: it is reported as not set up.
        """
        providers, seen, interval_s, timeouts, keyless = await asyncio.to_thread(
            self._health_lookup)
        now = now_ms()
        stale = [p for p in providers if p.id not in keyless
                 and (p.id not in seen or now - seen[p.id]["checked_ms"] >= interval_s * 1000)]
        if stale:
            # A health check should fail fast: a provider that is down must
            # not hold the status bar for the full answer timeout.
            quick = Timeouts(timeouts.connect, min(timeouts.read, timeouts.connect * 2))
            await asyncio.gather(*(self._discover(p, quick) for p in stale),
                                 return_exceptions=True)
            seen = await asyncio.to_thread(self._state.health)
        return {
            "interval_s": interval_s,
            "providers": [
                {"id": p.id, "label": p.label, "type": p.type, "local": p.is_local,
                 **(_not_set_up(p) if p.id in keyless else
                    seen.get(p.id) or {"ok": None, "checked_ms": None, "error": None})}
                for p in providers
            ],
        }

    def _health_lookup(self):
        providers = [p for p in self.providers() if p.enabled]
        keyless = {p.id for p in providers if self.missing_key(p) is not None}
        return (providers, self._state.health(),
                self.core.settings.get("models.health_interval_s"), self.timeouts(), keyless)

    async def test(self, provider_id: str, *, actor: str = "owner") -> dict[str, Any]:
        """Prove a provider works: list its models, or failing that say one word.

        The one word is a real call, so it goes the way every call goes and
        leaves a receipt, with the caller ``models.test``.
        """
        provider = await asyncio.to_thread(self.provider, provider_id)
        if provider is None:
            raise KeyError(provider_id)
        no_key = await asyncio.to_thread(self.missing_key, provider)
        if no_key is not None:
            return {"ok": False, "error": no_key.message, "models": None,
                    "checked_ms": now_ms()}
        try:
            models = await self._discover(provider)
            return {"ok": True, "error": None, "models": len(models), "checked_ms": now_ms()}
        except ModelError as exc:
            listing_error = exc
        if listing_error.kind != "not_found":
            return {"ok": False, "error": listing_error.message, "models": None,
                    "checked_ms": now_ms()}
        # Some endpoints have no model list. A one-token chat proves the same thing.
        config = await asyncio.to_thread(self.config)
        model = next((t.model for targets in config.roles.values() for t in targets
                      if t.provider_id == provider_id), None)
        if model is None:
            return {"ok": False, "models": None, "checked_ms": now_ms(),
                    "error": f"{provider.label} cannot list its models. Give it a role with "
                             "a model name typed in, then test again."}
        try:
            await self.chat([{"role": "user", "content": "Hi"}], provider_id=provider.id,
                            model=model, max_tokens=1, no_fallback=True, keep_text=False,
                            actor=actor, caller="models.test")
        except ModelError as exc:
            if exc.kind != "truncated":  # being cut off after one token is the point
                if exc.kind != "not_configured":  # switched off: nothing was learned
                    await asyncio.to_thread(self._state.set_health, provider.id, False,
                                            now_ms(), exc.message)
                return {"ok": False, "error": exc.message, "models": None,
                        "checked_ms": now_ms()}
        await asyncio.to_thread(self._state.set_health, provider.id, True, now_ms())
        return {"ok": True, "error": None, "models": None, "checked_ms": now_ms()}

    # --- usage ----------------------------------------------------------------

    def usage(self) -> dict[str, Any]:
        """Totals, estimated cost and what running locally saved. One lens read."""
        config = self.config()
        wanted = self.core.settings.get("models.savings_reference").strip()
        reference = next((p for p in config.prices if p["id"] == wanted), None)
        report = pricing.usage_report(self.core.lenses.read(self._usage_lens), config.prices,
                                      reference, now_ms())
        return report | {
            "currency": self.core.settings.get("models.currency"),
            "savings_reference": reference,
            "prices_are_default": config.prices_are_default,
        }

    # --- calling a model ------------------------------------------------------

    async def chat(self, messages: Sequence[Message], **options: Any) -> Reply:
        """Ask a model and wait for the whole answer. See ``stream`` for the options."""
        reply: Reply | None = None
        async for item in self._run(messages, False, options):
            if isinstance(item, Reply):
                reply = item
        assert reply is not None  # noqa: S101 - _run always ends with a Reply or raises
        return reply

    async def stream(self, messages: Sequence[Message], **options: Any
                     ) -> AsyncIterator[str | Reply]:
        """Ask a model and yield text as it arrives, then one final ``Reply``.

        Keyword options, all optional except ``actor`` and ``caller``:

        - ``role``: which role's model to use ("default" if not given).
        - ``provider_id`` and ``model``: name a model directly instead of a role.
        - ``effort``, ``system``, ``max_tokens``, ``temperature``.
        - ``json_schema``: ask for JSON matching this schema; ``Reply.data`` is
          the parsed value, and a reply that does not parse raises.
        - ``tools``: tools the model may call, each ``{"name", "description",
          "parameters"}`` with ``parameters`` a JSON Schema object. The calls
          come back in ``Reply.tool_calls``. ``stream`` yields the text as it
          arrives and the calls on the final ``Reply``; for a provider type
          whose adapter cannot stream tool calls (Bedrock), ``stream`` makes
          an ordinary call instead and yields its text in one piece.
        - ``tool_choice``: "auto" (the default), "required", "none", or
          ``{"name": ...}`` to make the model call that one tool.
        - ``actor``: who is asking (the Tape actor). ``caller``: a dotted name
          such as "chat.reply" saying which feature is asking.
        - ``local_only``: only providers marked local may answer.
        - ``no_fallback``: fail rather than try a fallback.
        - ``keep_text``: False records token counts only, never the text.
        - ``cause``, ``root``: Tape lineage for the receipt.

        Once any text has been yielded a failure is raised, not retried
        elsewhere: half an answer from one model cannot be continued by another.
        """
        async for item in self._run(messages, True, options):
            yield item

    def chat_sync(self, messages: Sequence[Message], **options: Any) -> Reply:
        """``chat`` for code that is not async, such as a worker job."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.chat(messages, **options))
        raise RuntimeError("chat_sync was called from async code. Use `await brain.chat(...)`.")

    def _plan(self, messages: Sequence[Message], role: str = "default",
              provider_id: str | None = None, model: str | None = None,
              effort: str | None = None, system: str | None = None,
              max_tokens: int | None = None, temperature: float | None = None,
              json_schema: dict | None = None, tools: Any = None, tool_choice: Any = None,
              *, actor: str, caller: str,
              local_only: bool = False, no_fallback: bool = False, keep_text: bool = True,
              cause: str | None = None, root: str | None = None) -> _Plan:
        get = self.core.settings.get
        turns, images, image_bytes, used_tools = _turns(
            messages, get("models.max_image_bytes"), get("models.max_total_image_bytes"))
        offered, choice = _tools(tools, tool_choice)
        if used_tools and not offered:
            raise _bad("This conversation has tool calls in it, so the model must be offered "
                       "the tools again. Pass the same `tools` as on the earlier call.")
        if offered and json_schema is not None:
            raise _bad("Ask for tools or for a json_schema, not both in one call. To get "
                       "structured data alongside tools, make it a tool's parameters.")
        if not isinstance(actor, str) or not actor:
            raise _bad("A model call needs an actor.")
        if not isinstance(caller, str) or not validate.CALLER.match(caller) or len(caller) > 100:
            raise _bad("A model call needs a caller: a dotted lower-case name such as "
                       "chat.reply.")
        if system is not None:
            system = _clean(system, "The system prompt")
        try:
            effort = validate.effort(effort)
        except Invalid as exc:
            raise _bad(str(exc)) from None
        if max_tokens is not None and (isinstance(max_tokens, bool)
                                       or not isinstance(max_tokens, int)
                                       or not 1 <= max_tokens <= 1_000_000):
            raise _bad("max_tokens must be a whole number from 1 to 1000000.")
        if temperature is not None and (isinstance(temperature, bool)
                                        or not isinstance(temperature, int | float)
                                        or not 0 <= temperature <= 2):
            raise _bad("temperature must be a number from 0 to 2.")
        if json_schema is not None and not isinstance(json_schema, dict):
            raise _bad("json_schema must be a JSON Schema object.")
        config = self.config()
        if provider_id is not None or model is not None:
            if not provider_id or not model:
                raise _bad("Name both a provider and a model, or neither and use a role.")
            if (not isinstance(provider_id, str) or not isinstance(model, str)
                    or not model.strip() or len(model) > validate.MAX_MODEL):
                raise _bad("A provider id and a model name must be text, and the model "
                           f"name at most {validate.MAX_MODEL} characters.")
            model = model.strip()
            if provider_id not in config.providers:
                raise ModelError("not_configured",
                                 f"There is no provider called '{provider_id}'.")
            targets, used_role = [Target(provider_id, model)], None
        else:
            if role not in ROLES:
                raise _bad(f"There is no role called '{role}'. Roles are: {', '.join(ROLES)}.")
            # A role nobody has set uses the default role's models, so one
            # choice is enough to get going. Embeddings models are a different
            # kind of model, so that role never borrows.
            targets = config.roles.get(role) or (
                config.roles.get("default") if role != "embeddings" else None) or []
            if not targets:
                raise ModelError(
                    "not_configured",
                    f"No model is chosen for the '{role}' role. Choose one under Settings, "
                    "Models.")
            used_role = role
        return _Plan(turns, system, list(targets), config.providers, used_role, effort,
                     max_tokens, temperature, json_schema, actor, caller, bool(local_only),
                     bool(no_fallback), bool(keep_text), cause, root, self.timeouts(),
                     get("models.default_max_tokens"), tools=offered, tool_choice=choice,
                     images=images, image_bytes=image_bytes)

    async def _run(self, messages: Sequence[Message], streaming: bool,
                   options: dict[str, Any]) -> AsyncIterator[str | Reply]:
        try:
            plan = await asyncio.to_thread(lambda: self._plan(messages, **options))
        except TypeError as exc:  # a missing or misspelt keyword
            raise _bad(f"The model call was not made correctly: {exc}.") from None
        primary = plan.targets[0]
        primary_reason: str | None = None
        failure: ModelError | None = None  # the last provider that was tried and failed
        skipped: ModelError | None = None  # the last one passed over without trying
        only_local_skips = True
        for index, target in enumerate(plan.targets):
            if index and plan.no_fallback:
                break
            provider = plan.providers.get(target.provider_id)
            skip = _why_skip(provider, target, plan.local_only)
            if skip is None and provider is not None:
                # A hosted provider with no key is passed over like one that
                # is switched off: the prompt is never sent to it.
                skip = await asyncio.to_thread(self.missing_key, provider)
            if skip is not None:
                only_local_skips &= skip.kind == "local_only"
                skipped = skip
                primary_reason = primary_reason or (skip.public if index == 0 else None)
                continue
            only_local_skips = False
            assert provider is not None  # noqa: S101 - _why_skip covers the None case
            started = time.monotonic()
            yielded = False
            try:
                raw: RawReply | None = None
                data: Any = None
                streamed = streaming
                async with contextlib.aclosing(
                        self._leg(plan, provider, target, streaming)) as leg:
                    async for item in leg:
                        if isinstance(item, str):
                            yielded = True
                            yield item
                        elif isinstance(item, bool):
                            streamed = item  # how this leg was really asked
                        else:
                            raw, data = item
                if raw is None:
                    raise ModelError("bad_reply", f"{provider.label} sent back nothing.")
            except ModelError as exc:
                duration = int((time.monotonic() - started) * 1000)
                await asyncio.to_thread(self._failed, plan, provider, target, exc, duration)
                failure = exc
                primary_reason = primary_reason or (exc.public if index == 0 else None)
                if yielded or not exc.fallback_ok:
                    raise
                continue
            except (GeneratorExit, asyncio.CancelledError):
                # The caller stopped listening or was cancelled. The provider
                # was still asked, so the leg gets a receipt; the token counts
                # are not known. Nothing may be awaited or yielded here.
                duration = int((time.monotonic() - started) * 1000)
                stopped = ModelError(
                    "abandoned", f"The call to {provider.label} was stopped before it finished.")
                try:
                    self._failed(plan, provider, target, stopped, duration)
                except Exception as exc:  # noqa: BLE001 - never mask the cancellation
                    log.warning("an abandoned call left no receipt: %s", type(exc).__name__)
                raise
            duration = int((time.monotonic() - started) * 1000)
            reply = Reply(
                text=raw.text, provider_id=provider.id, provider_type=provider.type,
                model=target.model, input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens, duration_ms=duration,
                fallback_from=primary.name if index else None,
                fallback_reason=primary_reason if index else None,
                local=provider.is_local, data=data, tool_calls=raw.tool_calls,
            )
            await asyncio.to_thread(self._called, plan, reply, streamed)
            yield reply
            return
        if failure is not None:
            # What went wrong with a provider that was tried says more than
            # the reason another was never tried.
            raise failure
        if plan.local_only and only_local_skips:
            what = f"the '{plan.role}' role" if plan.role else "this request"
            raise ModelError(
                "local_only",
                f"This must stay on your own machines, but {what} has no local model to use. "
                "Add a provider marked local to it under Settings, Models.")
        assert skipped is not None  # noqa: S101 - there is always at least one target
        raise skipped

    async def _leg(self, plan: _Plan, provider: Provider, target: Target, streaming: bool):
        """One attempt on one provider. Yields text pieces, then ``(RawReply, data)``.
        Before either, it yields False when a streamed call had to be made
        as an ordinary one: tools were offered and this adapter cannot stream
        tool calls."""
        adapter = await asyncio.to_thread(self.adapter, provider, plan.timeouts)
        system = plan.system
        if plan.json_schema is not None and not adapter.wants_native_json:
            ask = _JSON_ONLY + json.dumps(plan.json_schema, ensure_ascii=False)
            system = f"{system}\n\n{ask}" if system else ask
        options = provider.options
        request = ChatRequest(
            model=target.model, messages=plan.messages, system=system,
            max_tokens=plan.max_tokens or options.get("max_tokens") or plan.default_max_tokens,
            temperature=(plan.temperature if plan.temperature is not None
                         else options.get("temperature")),
            effort=plan.effort or target.effort or options.get("effort"),
            json_schema=plan.json_schema, tools=plan.tools, tool_choice=plan.tool_choice,
        )
        in_one_piece = streaming and bool(request.tools) and not adapter.streams_tools
        if in_one_piece:
            streaming = False
            yield False
        try:
            if streaming:
                raw = None
                async with contextlib.aclosing(adapter.stream(request)) as pieces:
                    async for piece in pieces:
                        if isinstance(piece, str):
                            yield piece
                        else:
                            raw = piece
            else:
                raw = await adapter.chat(request)
                if in_one_piece and raw.text:
                    yield raw.text  # asked for as a stream: the text, in one piece
        except ModelError as exc:
            raise _cannot(adapter, request, plan.images, exc) or exc from None
        except Exception as exc:  # noqa: BLE001 - an odd reply must not become a crash
            log.warning("a call to %s failed unexpectedly: %s", provider.id, type(exc).__name__)
            raise adapter.error("bad_reply") from None
        if raw is None:
            raise adapter.error("bad_reply")
        if request.tools:
            known = {tool["name"] for tool in request.tools}
            if any(call.name not in known for call in raw.tool_calls):
                raise adapter.error(
                    "bad_tool_call", detail="It asked for a tool that was not offered.")
            if raw.tool_calls and request.tool_choice == "none":
                raise adapter.error(
                    "bad_tool_call", detail="It called a tool after being told not to.")
            if request.forced and not raw.tool_calls:
                # Some servers accept a forced choice and then ignore it.
                raise adapter.error("no_tools")
        data = None
        if plan.json_schema is not None:
            data = _parse_json(raw.text, adapter)
        yield raw, data

    # --- embeddings -----------------------------------------------------------

    def embeddings_target(self, *, local_only: bool = False
                          ) -> tuple[Provider | None, Target | None, str | None]:
        """Which model ``embed`` would ask first, without contacting anyone.

        Returns ``(provider, target, None)``, or ``(None, None, sentence)``
        saying why there is none and what to do about it.
        """
        config = self.config()
        targets = config.roles.get("embeddings") or []
        if not targets:
            return None, None, ("No embeddings model is chosen. Choose one for the "
                                "'embeddings' role under Settings, Models.")
        reason: ModelError | None = None
        all_local_skips = True
        for target in targets:
            provider = config.providers.get(target.provider_id)
            skip = _why_skip(provider, target, local_only)
            if skip is None and provider is not None:
                skip = self.missing_key(provider)
            if skip is None:
                return provider, target, None
            all_local_skips &= skip.kind == "local_only"
            reason = skip
        if local_only and all_local_skips:
            return None, None, ("This must stay on your own machines, but the 'embeddings' "
                                "role has no local model to use. Add a provider marked local "
                                "to it under Settings, Models.")
        return None, None, reason.message if reason else None

    async def embed(self, texts: Sequence[str], *, actor: str, caller: str,
                    local_only: bool = False, no_fallback: bool = False,
                    cause: str | None = None, root: str | None = None) -> EmbedReply:
        """Turn texts into vectors with the 'embeddings' role's model.

        One vector per text, in order, all from one model: when the first
        choice fails partway, the whole list is asked again of the next, since
        vectors from two models cannot be compared. Texts are sent in batches.
        One receipt is printed per call (``model.called`` with ``embedding``
        true, or ``model.failed`` per provider that was tried and failed). The
        texts themselves are never stored anywhere.

        - ``local_only``: only providers marked local may be asked.
        - ``no_fallback``: when the first model that can be asked fails, raise
          instead of asking the next. For a caller that compares these vectors
          with ones it stored earlier, which must come from the same model.
        - Raises ``ModelError``: ``not_configured`` when no model is chosen,
          ``local_only`` when only hosted ones are, ``no_embeddings`` for a
          provider type that cannot make embeddings yet.
        """
        plan, batches = await asyncio.to_thread(
            self._embed_plan, texts, actor, caller, local_only, cause, root)
        failure: ModelError | None = None
        skipped: ModelError | None = None
        only_local_skips = True
        for target in plan.targets:
            provider = plan.providers.get(target.provider_id)
            skip = _why_skip(provider, target, plan.local_only)
            if skip is None and provider is not None:
                skip = await asyncio.to_thread(self.missing_key, provider)
            if skip is not None:
                only_local_skips &= skip.kind == "local_only"
                skipped = skip
                continue
            only_local_skips = False
            assert provider is not None  # noqa: S101 - _why_skip covers the None case
            started = time.monotonic()
            adapter = await asyncio.to_thread(self.adapter, provider, plan.timeouts)
            vectors: list[list[float]] = []
            tokens = 0
            try:
                for batch in batches:
                    try:
                        raw = await adapter.embed(target.model, batch)
                    except ModelError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - an odd reply must not crash
                        log.warning("an embeddings call to %s failed unexpectedly: %s",
                                    provider.id, type(exc).__name__)
                        raise adapter.error("bad_reply") from None
                    if len(raw.vectors) != len(batch) or (
                            vectors and len(raw.vectors[0]) != len(vectors[0])):
                        raise adapter.error("bad_reply")
                    vectors.extend(raw.vectors)
                    tokens += raw.input_tokens
            except ModelError as exc:
                duration = int((time.monotonic() - started) * 1000)
                await asyncio.to_thread(self._failed, plan, provider, target, exc, duration)
                failure = exc
                if not exc.fallback_ok or no_fallback:
                    raise
                continue
            except asyncio.CancelledError:
                duration = int((time.monotonic() - started) * 1000)
                stopped = ModelError(
                    "abandoned", f"The call to {provider.label} was stopped before it finished.")
                try:
                    self._failed(plan, provider, target, stopped, duration)
                except Exception as exc:  # noqa: BLE001 - never mask the cancellation
                    log.warning("an abandoned call left no receipt: %s", type(exc).__name__)
                raise
            duration = int((time.monotonic() - started) * 1000)
            reply = EmbedReply(vectors, provider.id, target.model, tokens, provider.is_local)
            await asyncio.to_thread(self._embedded, plan, provider, reply, duration)
            return reply
        if failure is not None:
            raise failure
        if plan.local_only and only_local_skips:
            raise ModelError(
                "local_only",
                "This must stay on your own machines, but the 'embeddings' role has no local "
                "model to use. Add a provider marked local to it under Settings, Models.")
        assert skipped is not None  # noqa: S101 - there is always at least one target
        raise skipped

    def embed_sync(self, texts: Sequence[str], **options: Any) -> EmbedReply:
        """``embed`` for code that is not async, such as a worker job."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.embed(texts, **options))
        raise RuntimeError("embed_sync was called from async code. Use `await brain.embed(...)`.")

    def _embed_plan(self, texts: Any, actor: Any, caller: Any, local_only: Any,
                    cause: str | None, root: str | None) -> tuple[_Plan, list[list[str]]]:
        if isinstance(texts, str) or not isinstance(texts, Sequence) or not texts:
            raise _bad("An embeddings call needs a list with at least one text.")
        if len(texts) > MAX_EMBED_TEXTS:
            raise _bad(f"An embeddings call can carry at most {MAX_EMBED_TEXTS} texts.")
        size = 0
        batches: list[list[str]] = [[]]
        room = EMBED_BATCH_CHARS
        for text in texts:
            text = _clean(text, "A text to embed")
            if not text.strip():
                raise _bad("A text to embed cannot be empty.")
            size += len(text)
            if batches[-1] and (len(batches[-1]) >= EMBED_BATCH or len(text) > room):
                batches.append([])
                room = EMBED_BATCH_CHARS
            batches[-1].append(text)
            room -= len(text)
        if size > MAX_CHARS:
            raise _bad("There is too much text to embed in one call.")
        if not isinstance(actor, str) or not actor:
            raise _bad("A model call needs an actor.")
        if not isinstance(caller, str) or not validate.CALLER.match(caller) or len(caller) > 100:
            raise _bad("A model call needs a caller: a dotted lower-case name such as "
                       "memory.index.")
        config = self.config()
        targets = config.roles.get("embeddings") or []
        if not targets:
            raise ModelError(
                "not_configured",
                "No model is chosen for the 'embeddings' role. Choose one under Settings, "
                "Models.")
        plan = _Plan([], None, list(targets), config.providers, "embeddings", None, None, None,
                     None, actor, caller, bool(local_only), False, False, cause, root,
                     self.timeouts(), 0, embedding=True)
        return plan, batches

    def _embedded(self, plan: _Plan, provider: Provider, reply: EmbedReply,
                  duration_ms: int) -> None:
        self.core.tape.print("model.called", {
            "provider_id": reply.provider_id, "provider_type": provider.type,
            "model": reply.model, "role": plan.role, "caller": plan.caller,
            "duration_ms": duration_ms, "input_tokens": reply.input_tokens,
            "output_tokens": 0, "local": reply.is_local, "streamed": False,
            "embedding": True, "inputs": len(reply.vectors),
        }, actor=plan.actor, cause=plan.cause, root=plan.root)
        if not self._moved(provider):
            self._state.set_health(reply.provider_id, True, now_ms())

    # --- receipts -------------------------------------------------------------

    def _called(self, plan: _Plan, reply: Reply, streamed: bool) -> None:
        prompt_ref = completion_ref = None
        if plan.keep_text:
            prompt: dict[str, Any] = {
                "system": plan.system,
                "messages": [{"role": r, "content": c if isinstance(c, str) else _kept(c)}
                             for r, c in plan.messages]}
            if plan.tools:
                prompt["tools"] = list(plan.tools)
            prompt_ref = self.core.vault.put_text(json.dumps(prompt, ensure_ascii=False))
            # Plain text as ever for a plain answer. An answer that asked for
            # tools is kept as JSON, so the calls are part of the record.
            completion_ref = self.core.vault.put_text(reply.text if not reply.tool_calls else (
                json.dumps({"text": reply.text,
                            "tool_calls": [{"id": c.id, "name": c.name,
                                            "arguments": c.arguments}
                                           for c in reply.tool_calls]},
                           ensure_ascii=False)))
        counts: dict[str, int] = {}
        if plan.tools:
            counts |= {"tools": len(plan.tools), "tool_calls": len(reply.tool_calls)}
        if plan.images:
            counts |= {"images": plan.images, "image_bytes": plan.image_bytes}
        self.core.tape.print("model.called", counts | {
            "provider_id": reply.provider_id, "provider_type": reply.provider_type,
            "model": reply.model, "role": plan.role, "caller": plan.caller,
            "duration_ms": reply.duration_ms, "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens, "local": reply.is_local, "streamed": streamed,
            "fallback_from": reply.fallback_from, "fallback_reason": reply.fallback_reason,
            "prompt_ref": prompt_ref, "completion_ref": completion_ref,
        }, actor=plan.actor, cause=plan.cause, root=plan.root)
        if not self._moved(plan.providers[reply.provider_id]):
            self._state.set_health(reply.provider_id, True, now_ms())

    def _failed(self, plan: _Plan, provider: Provider, target: Target, error: ModelError,
                duration_ms: int) -> None:
        # Only the reason and our own sentence are kept. What the provider
        # said stays out: it can quote the prompt back.
        log.warning("model call failed: provider=%s reason=%s", provider.id, error.kind)
        self.core.tape.print("model.failed", {
            "provider_id": provider.id, "provider_type": provider.type, "model": target.model,
            "role": plan.role, "caller": plan.caller, "duration_ms": duration_ms,
            "reason": error.kind, "message": error.public, "status": error.status,
            "fallback_ok": error.fallback_ok, "embedding": plan.embedding,
        }, actor=plan.actor, cause=plan.cause, root=plan.root)
        if error.kind in ("unreachable", "timeout") and not self._moved(provider):
            self._state.set_health(provider.id, False, now_ms(), error.public)

    def close(self) -> None:
        self._state.close()


def _no_key(provider: Provider) -> ModelError:
    return ModelError("not_configured",
                      f"{provider.label} has no key yet. Add one under Settings, Models.")


def _not_set_up(provider: Provider) -> dict[str, Any]:
    """The health of a provider that needs a key and has none: never checked."""
    return {"ok": None, "checked_ms": None, "error": _no_key(provider).message}


def _why_skip(provider: Provider | None, target: Target, local_only: bool) -> ModelError | None:
    if provider is None:
        return ModelError("not_configured",
                          f"The provider '{target.provider_id}' no longer exists.")
    if not provider.enabled:
        return ModelError("not_configured", f"{provider.label} is switched off.")
    if local_only and not provider.is_local:
        return ModelError("local_only",
                          f"{provider.label} is not on your own machines, and this request "
                          "must stay local.")
    return None


def _clean(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise _bad(f"{what} must be text.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _bad(f"{what} contains characters that cannot be sent.") from None
    return value


def _image(part: dict, limit: int) -> tuple[dict[str, Any], int]:
    """Check one image part. Returns it cleaned, and its size in bytes."""
    kind, data = part.get("media_type"), part.get("data")
    if kind not in IMAGE_TYPES:
        raise _bad("An image must have a media_type of " + ", ".join(IMAGE_TYPES)
                   + ". Convert it to one of those first.")
    if not isinstance(data, str) or not data:
        raise _bad("An image needs `data`: the file's bytes, base64 encoded, as text.")
    # Sized from the text first, so an enormous one is refused without decoding it.
    if len(data) * 3 // 4 - 2 > limit:
        raise _bad(_too_big(limit))
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise _bad("An image's data is not valid base64. Encode the file's bytes with "
                   "standard base64 and no line breaks or data: prefix.") from None
    if len(raw) > limit:
        raise _bad(_too_big(limit))
    if not raw.startswith(_IMAGE_STARTS[kind]) or (kind == "image/webp"
                                                  and raw[8:12] != b"WEBP"):
        raise _bad(f"An image is labelled {kind} but its data is not that kind of file. "
                   "Set media_type to what the file really is.")
    return {"type": "image", "media_type": kind, "data": data}, len(raw)


def _too_big(limit: int) -> str:
    return (f"An image is larger than the {limit} bytes allowed. Make it smaller, or raise "
            "the limit under Settings, Models.")


def _tool_id(value: Any) -> str:
    if not isinstance(value, str) or not _TOOL_ID.match(value):
        raise _bad("A tool call id must be 1 to 256 printable characters with no spaces. "
                   "Use the id from the reply's tool call unchanged.")
    return value


def _turns(messages: Any, max_image_bytes: int = 0, max_total_image_bytes: int = 0
           ) -> tuple[list[tuple[str, str | list[dict[str, Any]]]], int, int, bool]:
    """Check the conversation. Returns the turns, how many images they carry,
    the images' size together, and whether any turn has a tool call.

    A turn that is only text becomes ``(role, text)``, as it always has. Any
    other becomes ``(role, parts)``; see ``ChatRequest.messages``.
    """
    if isinstance(messages, str) or not isinstance(messages, Sequence) or not messages:
        raise _bad("A model call needs at least one message.")
    if len(messages) > MAX_MESSAGES:
        raise _bad(f"A model call can carry at most {MAX_MESSAGES} messages.")
    turns: list[tuple[str, str | list[dict[str, Any]]]] = []
    size = images = image_bytes = 0
    used_tools = False
    waiting: dict[str, str] = {}  # tool calls of the turn before: id -> tool name

    def image(part: dict) -> dict[str, Any]:
        nonlocal images, image_bytes
        cleaned, weight = _image(part, max_image_bytes)
        images += 1
        image_bytes += weight
        if images > MAX_IMAGES:
            raise _bad(f"A model call can carry at most {MAX_IMAGES} images.")
        if max_total_image_bytes and image_bytes > max_total_image_bytes:
            raise _bad(f"The images in this request add up to more than the "
                       f"{max_total_image_bytes} bytes allowed for all of them together. Send "
                       "fewer or smaller images, or raise the limit under Settings, Models.")
        return cleaned

    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            raise _bad("Each message needs a role of 'user' or 'assistant'.")
        role = message["role"]
        content = message.get("content")
        asked = message.get("tool_calls") or []
        if asked and (role != "assistant" or not isinstance(asked, list | tuple)):
            raise _bad("Only an assistant message can carry tool_calls, as a list.")
        if content is None and asked:
            content = ""
        parts: list[dict[str, Any]] = []
        answered: set[str] = set()
        if isinstance(content, list):
            for part in content:
                kind = part.get("type") if isinstance(part, dict) else None
                if kind == "text":
                    parts.append({"type": "text", "text": _clean(part.get("text"), "A message")})
                elif kind == "image" and role == "user":
                    parts.append(image(part))
                elif kind == "tool_result" and role == "user":
                    if any(p["type"] != "tool_result" for p in parts):
                        raise _bad("Tool results must come first in a message, before any "
                                   "text or image.")
                    call_id = _tool_id(part.get("tool_call_id"))
                    if call_id not in waiting or call_id in answered:
                        raise _bad(f"The tool result for '{call_id}' does not answer a tool "
                                   "call in the assistant message just before it. Send one "
                                   "result for each tool call, in the very next message.")
                    answered.add(call_id)
                    inner = part.get("content", "")
                    if isinstance(inner, str):
                        inner = [{"type": "text", "text": inner}]
                    if not isinstance(inner, list):
                        raise _bad("A tool result's content must be text, or a list of text "
                                   "and image parts.")
                    texts, pictures = [], []
                    for piece in inner:
                        sort = piece.get("type") if isinstance(piece, dict) else None
                        if sort == "text":
                            texts.append(_clean(piece.get("text"), "A tool result"))
                        elif sort == "image":
                            pictures.append(image(piece))
                        else:
                            raise _bad("A tool result can hold only text and image parts.")
                    parts.append({"type": "tool_result", "tool_call_id": call_id,
                                  "name": waiting[call_id], "text": "".join(texts),
                                  "images": pictures, "is_error": bool(part.get("is_error"))})
                elif kind in ("image", "tool_result"):
                    raise _bad("Images and tool results belong in user messages. An "
                               "assistant message carries text and tool_calls.")
                else:
                    raise _bad("A message part must be of type text, image or tool_result.")
        else:
            parts.append({"type": "text", "text": _clean(content, "A message")})
        if answered != set(waiting):
            raise _bad("An assistant message with tool calls must be followed by a user "
                       "message with a tool_result part for every one of them.")
        waiting = {}
        arguments_size = 0
        for call in asked:
            call = call.as_dict() if hasattr(call, "as_dict") else call
            if not isinstance(call, dict):
                raise _bad("Each tool call needs an id, a name and arguments.")
            call_id, name, arguments = call.get("id"), call.get("name"), call.get("arguments")
            if (not isinstance(name, str) or not _TOOL_NAME.match(name)
                    or not isinstance(arguments, dict)):
                raise _bad("Each tool call needs an id, a name and arguments (a JSON object). "
                           "Pass the reply's tool calls back unchanged: reply.as_message().")
            try:
                arguments_size += len(json.dumps(arguments, ensure_ascii=False, allow_nan=False))
            except (TypeError, ValueError, RecursionError):
                raise _bad(f"The arguments of the tool call '{name}' must be plain JSON: "
                           "objects, lists, text, finite numbers, true, false and null.") from None
            if _tool_id(call_id) in waiting:
                raise _bad("Two tool calls in one message have the same id.")
            signature = call.get("signature")
            if signature is not None and not isinstance(signature, str):
                raise _bad("A tool call's signature must be passed back unchanged.")
            waiting[call_id] = name
            parts.append({"type": "tool_call", "id": call_id, "name": name,
                          "arguments": arguments, "signature": signature})
        size += sum(len(p["text"]) for p in parts if "text" in p)
        size += arguments_size
        if all(p["type"] == "text" for p in parts):
            turns.append((role, "".join(p["text"] for p in parts)))
        else:
            used_tools |= bool(asked) or bool(answered)
            turns.append((role, parts))
    if waiting:
        raise _bad("The conversation ends with tool calls that have no results. Add a user "
                   "message with a tool_result part for each of them.")
    if size > MAX_CHARS:
        raise _bad("The conversation is too long to send.")
    if turns[0][0] != "user":
        raise _bad("The first message must be from the user.")
    return turns, images, image_bytes, used_tools


def _tools(tools: Any, choice: Any) -> tuple[tuple[dict[str, Any], ...], str | dict[str, str]]:
    """Check the tools on offer and the choice among them."""
    if tools is None or (isinstance(tools, list | tuple) and not tools):
        if choice not in (None, "auto", "none"):
            raise _bad("tool_choice was given without any tools. Pass `tools` as well.")
        return (), "auto"
    if not isinstance(tools, list | tuple):
        raise _bad("tools must be a list of {name, description, parameters}.")
    if len(tools) > MAX_TOOLS:
        raise _bad(f"A model call can offer at most {MAX_TOOLS} tools.")
    offered: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise _bad("Each tool must be {name, description, parameters}.")
        name, description = tool.get("name"), tool.get("description")
        parameters = tool.get("parameters")
        if not isinstance(name, str) or not _TOOL_NAME.match(name):
            raise _bad("A tool's name must be 1 to 64 letters, digits, underscores or "
                       "hyphens.")
        if any(name == other["name"] for other in offered):
            raise _bad(f"Two tools are both called '{name}'. Give each its own name.")
        description = _clean(description, f"The description of the tool '{name}'")
        if not description.strip():
            raise _bad(f"The tool '{name}' needs a description saying what it does and when "
                       "to use it.")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise _bad(f"The parameters of the tool '{name}' must be a JSON Schema whose "
                       "type is \"object\". For a tool that takes nothing, use "
                       "{\"type\": \"object\", \"properties\": {}}.")
        offered.append({"name": name, "description": description, "parameters": parameters})
    try:
        weight = len(json.dumps(offered, ensure_ascii=False))
    except (TypeError, ValueError):
        raise _bad("A tool's parameters must be plain JSON: objects, lists, text, numbers, "
                   "true, false and null.") from None
    if weight > MAX_TOOL_CHARS:
        raise _bad("The tool definitions are too long to send.")
    if choice is None:
        choice = "auto"
    if isinstance(choice, dict):
        wanted = choice.get("name")
        if not any(wanted == tool["name"] for tool in offered):
            raise _bad("tool_choice names a tool that is not in `tools`.")
        choice = {"name": wanted}
    elif choice not in TOOL_CHOICES:
        raise _bad("tool_choice must be \"auto\", \"required\", \"none\" or {\"name\": ...}.")
    return tuple(offered), choice


def _kept(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A turn's parts as they are kept with the prompt: every image replaced
    by its type and size, and no provider signatures. Image data is never
    stored."""

    def without(image: dict) -> dict[str, Any]:
        return {"type": "image", "media_type": image["media_type"],
                "bytes": len(image["data"]) * 3 // 4 - image["data"][-2:].count("=")}

    kept = []
    for part in parts:
        if part["type"] == "image":
            kept.append(without(part))
        elif part["type"] == "tool_result":
            kept.append(part | {"images": [without(image) for image in part["images"]]})
        elif part["type"] == "tool_call":
            kept.append({k: v for k, v in part.items() if k != "signature"})
        else:
            kept.append(part)
    return kept


def _cannot(adapter: Adapter, request: ChatRequest, images: int,
            error: ModelError) -> ModelError | None:
    """A provider's rejection, read as "this model cannot do that" when the
    request offered tools or carried images and the rejection is about them.

    A plain rejection is final: the same request would fail anywhere. One
    that is about tools or images is worth taking to the next model.
    """
    if error.kind != "bad_request" or error.status not in (400, 422):
        return None
    if images and _ABOUT_IMAGES.search(error.detail):
        return adapter.error("no_images", status=error.status, detail=error.detail)
    if request.tools and _ABOUT_TOOLS.search(error.detail):
        return adapter.error("no_tools", status=error.status, detail=error.detail)
    return None


def _parse_json(text: str, adapter: Adapter) -> Any:
    fenced = _FENCE.match(text)
    try:
        return json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        raise adapter.error(
            "bad_reply", detail="JSON was asked for and the answer was not valid JSON."
        ) from None


__all__ = ["Brain", "Config", "ModelError", "Reply", "mask", "secret_name"]
