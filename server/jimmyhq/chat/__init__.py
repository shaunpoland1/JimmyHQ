"""Chat: conversations between the owner and a model.

Other modules use the functions exported here. They do not print ``chat.*``
events themselves, and they do not open the off-the-record store.

    from jimmyhq import chat

    conversation = chat.start_conversation(core, title="Plans")
    async for item in chat.send(core, conversation["id"], "Where do I start?"):
        ...  # pieces of text, then the stored reply as a dict

See jimmyhq/modules.py for how a module plugs in, and docs/chat.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from jimmyhq.chat.conversations import (
    archive_conversation,
    change_conversation,
    delete_off_record,
    get_conversation,
    list_conversations,
    memory_of,
    restore_conversation,
    start_conversation,
)
from jimmyhq.chat.deletion import delete_conversation, delete_expired, finish_pending
from jimmyhq.chat.replies import regenerate, send, stop
from jimmyhq.chat.store import ChatError
from jimmyhq.core import Core
from jimmyhq.scheduler import Scheduler

__all__ = [
    "ChatError", "archive_conversation", "build", "change_conversation",
    "delete_conversation", "delete_expired", "delete_off_record", "finish_pending",
    "get_conversation", "jobs", "list_conversations", "memory_of", "regenerate",
    "restore_conversation", "send", "start_conversation", "shutdown", "stop",
]

DELETE_EVERY_S = 3600  # how often the worker looks for archived conversations past their time


async def shutdown(core: Core) -> None:
    """Called when the web process is asked to stop: a reply in flight is
    stored as stopped, with its partial text and receipt, before it exits."""
    from jimmyhq.chat.replies import shutdown as settle  # noqa: PLC0415

    await settle(core)


def build(core: Core) -> APIRouter:
    from jimmyhq.chat.routes import build_router  # noqa: PLC0415 - keeps import light

    return build_router(core)


def jobs(core: Core, scheduler: Scheduler) -> None:
    """The worker's chat job: finish any deletion that was interrupted, then
    mark, and after the grace period delete, conversations that have been
    archived for longer than the owner's setting allows (never, while it is 0)."""
    scheduler.every("chat.delete_archived", DELETE_EVERY_S, lambda: delete_expired(core))
