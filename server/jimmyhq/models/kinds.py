"""Event kinds for the models module.

Configuration (providers, roles, prices) and receipts (every call, every failed
leg) are all events. Keys are never here: they live in the secret store. Events
carry token counts, never money: prices change, so cost is worked out on read.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

_PROVIDER_TYPE = Literal["openai_compatible", "openai", "anthropic", "google", "bedrock"]
_ROLE = Literal["default", "cheap", "smart", "code", "vision", "embeddings"]


class ProviderOptions(Payload):
    """Defaults a provider applies when the caller does not say."""

    max_tokens: int | None = Field(None, ge=1, le=1_000_000)
    temperature: float | None = Field(None, ge=0, le=2)
    effort: str | None = Field(None, max_length=20)
    # None lets the provider type decide whether to ask for JSON natively.
    native_json: bool | None = None


@kind("model.provider.saved", example={
    "provider_id": "home", "type": "openai_compatible", "label": "Home server",
    "base_url": "http://127.0.0.1:11434/v1", "enabled": True, "local": True,
    "options": {"max_tokens": 2048}, "region": None, "profile": None,
})
class ProviderSaved(Payload):
    """A provider was added or changed. The whole record, so the latest event wins."""

    provider_id: str
    type: _PROVIDER_TYPE
    label: str
    base_url: str | None = None
    enabled: bool = True
    local: bool = False
    options: ProviderOptions = Field(default_factory=ProviderOptions)
    region: str | None = None
    profile: str | None = None


@kind("model.provider.removed", example={"provider_id": "home"})
class ProviderRemoved(Payload):
    provider_id: str


class TargetPayload(Payload):
    provider_id: str
    model: str
    effort: str | None = None


@kind("model.role.set", example={
    "role": "default",
    "primary": {"provider_id": "home", "model": "example-model", "effort": None},
    "fallbacks": [],
})
class RoleSet(Payload):
    """A role now points at this model, then these. No primary clears the role."""

    role: _ROLE
    primary: TargetPayload | None = None
    fallbacks: list[TargetPayload] = Field(default_factory=list)


class PriceEntry(Payload):
    id: str
    provider: str  # a provider type, a provider id, or "*"
    model: str  # a pattern; * matches any run of characters
    input_per_mtok: float = Field(ge=0, le=1_000_000)
    output_per_mtok: float = Field(ge=0, le=1_000_000)


@kind("model.prices.set", example={"prices": [
    {"id": "example", "provider": "*", "model": "example-*", "input_per_mtok": 1.0,
     "output_per_mtok": 2.0},
]})
class PricesSet(Payload):
    """The owner's whole price table. Replaces the one shipped with the package."""

    prices: list[PriceEntry]


@kind("model.called", example={
    "provider_id": "home", "provider_type": "openai_compatible", "model": "example-model",
    "role": "default", "caller": "chat.reply", "duration_ms": 840, "input_tokens": 120,
    "output_tokens": 48, "local": True, "streamed": False, "fallback_from": None,
    "fallback_reason": None, "prompt_ref": None, "completion_ref": None,
})
class ModelCalled(Payload):
    """The receipt for one answered call. The text is only referenced, and only
    when the caller allowed it to be kept."""

    provider_id: str
    provider_type: str
    model: str
    role: str | None = None
    caller: str
    duration_ms: int
    input_tokens: int
    output_tokens: int
    local: bool
    streamed: bool = False
    fallback_from: str | None = None
    fallback_reason: str | None = None
    prompt_ref: str | None = None
    completion_ref: str | None = None
    # True for an embeddings call. Its texts are never kept, ``output_tokens``
    # is 0, and ``inputs`` says how many texts were turned into vectors.
    embedding: bool = False
    inputs: int | None = None
    # Counts only, present when the call used them: tools offered, tool calls
    # the model made, images sent and their size together. Never the images.
    tools: int | None = Field(None, ge=0)
    tool_calls: int | None = Field(None, ge=0)
    images: int | None = Field(None, ge=0)
    image_bytes: int | None = Field(None, ge=0)


@kind("model.failed", example={
    "provider_id": "home", "provider_type": "openai_compatible", "model": "example-model",
    "role": "default", "caller": "chat.reply", "duration_ms": 5003, "reason": "unreachable",
    "message": "Home server could not be reached.", "status": None, "fallback_ok": True,
})
class ModelFailed(Payload):
    """One leg of a call that did not work. Never carries prompt text or
    anything the provider said back."""

    provider_id: str
    provider_type: str
    model: str
    role: str | None = None
    caller: str
    duration_ms: int
    reason: str
    message: str
    status: int | None = None
    fallback_ok: bool
    embedding: bool = False
