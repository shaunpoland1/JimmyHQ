"""One adapter per provider type. ``ADAPTERS`` maps a type to its class."""

from __future__ import annotations

from jimmyhq.models.adapters.anthropic import AnthropicAdapter
from jimmyhq.models.adapters.base import Adapter, Timeouts, mask
from jimmyhq.models.adapters.bedrock import BedrockAdapter
from jimmyhq.models.adapters.google import GoogleAdapter
from jimmyhq.models.adapters.openai_compat import OpenAIAdapter, OpenAICompatibleAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    "openai_compatible": OpenAICompatibleAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "google": GoogleAdapter,
    "bedrock": BedrockAdapter,
}

__all__ = ["ADAPTERS", "Adapter", "Timeouts", "mask"]
