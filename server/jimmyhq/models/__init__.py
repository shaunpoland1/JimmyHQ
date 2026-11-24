"""The models module: the one place the product talks to language models.

Other modules call ``brain(core)`` and ask for a role:

    from jimmyhq.models import brain
    reply = await brain(core).chat(
        [{"role": "user", "content": "Hello"}], actor=who.actor, caller="chat.reply")

See jimmyhq/modules.py for how a module plugs in, and docs/models.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from jimmyhq.core import Core
from jimmyhq.models.brain import Brain
from jimmyhq.models.shapes import (
    EmbedReply,
    Message,
    ModelError,
    Provider,
    Reply,
    Target,
    Tool,
    ToolCall,
)

__all__ = ["Brain", "EmbedReply", "Message", "ModelError", "Provider", "Reply", "Target", "Tool",
           "ToolCall", "brain", "build"]


def brain(core: Core) -> Brain:
    """This process's Brain. Holds no state of its own, so one per process is enough."""
    return core.service("models", Brain)


def build(core: Core) -> APIRouter:
    from jimmyhq.models.routes import build_router  # noqa: PLC0415 - keeps import light

    return build_router(core, brain(core))
