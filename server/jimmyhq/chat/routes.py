"""Chat over HTTP. Who may do what:

- reading conversations and the composer's options: the owner or a read token.
  A token is shown only what is on the Tape, which it can read anyway. What is
  off the record is the owner's alone: a token does not see those messages or
  titles, and a conversation that has never been on the record is not there
  for it at all;
- starting, sending, regenerating, stopping, renaming, archiving, flipping
  the record switch, deleting what is off the record and deleting a whole
  conversation for good: the signed-in owner only. A token cannot talk to a
  model on the owner's account, and cannot erase anything.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from jimmyhq.auth import Principal
from jimmyhq.chat import conversations as convs
from jimmyhq.chat import deletion, options, replies, store
from jimmyhq.chat.store import KEEP, ChatError
from jimmyhq.core import Core
from jimmyhq.web.deps import owner, reader

_MAX_TEXT = 2_000_000  # the setting's own ceiling; the setting decides below it


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationBody(_Body):
    title: str | None = Field(None, max_length=1000)
    target: dict[str, Any] | None = None
    effort: str | None = Field(None, max_length=20)
    local_only: bool | None = Field(None, strict=True)
    on_record: bool | None = Field(None, strict=True)
    use_memory: bool | None = Field(None, strict=True)


class SendBody(_Body):
    text: str = Field(max_length=_MAX_TEXT)
    target: dict[str, Any] | None = None
    effort: str | None = Field(None, max_length=20)
    web: bool | None = Field(None, strict=True)  # left out: lookup.chat_default decides


class RegenerateBody(_Body):
    target: dict[str, Any] | None = None
    effort: str | None = Field(None, max_length=20)
    web: bool | None = Field(None, strict=True)


_IN_PATH = re.compile(r"(/api/chat/conversations)(/[^/?\s]+)?([^?\s]*)(\?\S*)?")
_MESSAGE_IN_PATH = re.compile(r"/messages/[^/?\s]+")


_DELETE = re.compile(r"/api/chat/conversations/[^/?\s]+/off-record/?(\?\S*)?$")


class QuietAccess(logging.Filter):
    """Keeps conversation ids and title searches out of the server's access
    log. A conversation that is off the record must leave no trace of itself
    in the logs, and the address of a request names it; a search names words
    of its title. The line still says which route was asked and how it went.

    Deleting what is off the record is not written down at all: that route is
    only ever asked about something off the record, and nothing about the
    deletion is recorded anywhere."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            if any(isinstance(a, str) and _DELETE.match(a) for a in args):
                return False
            record.args = tuple(
                _IN_PATH.sub(lambda m: m[1] + ("/-" if m[2] else "")
                             + _MESSAGE_IN_PATH.sub("/messages/-", m[3]), a)
                if isinstance(a, str) and "/api/chat/conversations" in a else a for a in args)
        return True


def quiet_access_log() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QuietAccess) for f in access.filters):
        access.addFilter(QuietAccess())


def _do[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except ChatError as exc:
        raise HTTPException(exc.status, exc.detail) from None


def _sent(body: BaseModel, *names: str) -> dict[str, Any]:
    """The named fields the request really carried; a field left out stays as it is."""
    return {n: getattr(body, n) for n in names if n in body.model_fields_set}


def _web(body: BaseModel) -> dict[str, Any]:
    """The send's own choice about the web, when it made one."""
    chosen = getattr(body, "web", None)
    return {} if chosen is None else {"web": chosen}


def _sse(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class _ReplyStream(StreamingResponse):
    """The stream of one reply. The reply is stopped when its listener goes
    away, and the listener lives inside the body. A client that has already
    gone when the answer begins never starts the body, so nothing would be
    listening and nothing would stop the reply: it would be written to nobody
    for as long as the model took, with the conversation busy all the while.
    So the reply is also stopped when the response itself is over, however
    that happens. (Stopping a reply that has ended does nothing.)"""

    def __init__(self, run: replies.Run, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._run = run

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._run.stop()


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])
    quiet_access_log()

    # --- reading ---------------------------------------------------------------

    @router.get("/options")
    async def composer_options(_: Principal = Depends(reader)):  # noqa: B008
        return await options.composer_options(core)

    @router.get("/conversations")
    def conversations(
        archived: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
        before: str | None = Query(None, max_length=64),
        q: str | None = Query(None, max_length=store.MAX_QUERY),
        who: Principal = Depends(reader),  # noqa: B008
    ):
        return _do(convs.list_conversations, core, archived=archived, limit=limit,
                   before=before, q=q, off_record=who.is_owner)

    @router.get("/conversations/{conversation_id}")
    def conversation(
        conversation_id: str,
        limit: int = Query(50, ge=1, le=200),
        before: str | None = Query(None, max_length=64),
        who: Principal = Depends(reader),  # noqa: B008
    ):
        return _do(convs.get_conversation, core, conversation_id, limit=limit,
                   before=before, off_record=who.is_owner)

    @router.get("/conversations/{conversation_id}/messages/{message_id}/memory")
    def memory_used(conversation_id: str, message_id: str,
                    _: Principal = Depends(owner)):  # noqa: B008
        # Owner only: this answers with words from memory, and for a reply kept
        # off the record even which passages it was given is the owner's alone.
        return _do(convs.memory_of, core, conversation_id, message_id)

    # --- the conversation itself -------------------------------------------------

    @router.post("/conversations", status_code=201)
    def start(body: ConversationBody | None = None,
              who: Principal = Depends(owner)):  # noqa: B008
        fields = body.model_dump() if body else {}
        return _do(convs.start_conversation, core, actor=who.actor, **fields)

    @router.patch("/conversations/{conversation_id}")
    def change(conversation_id: str, body: ConversationBody,
               who: Principal = Depends(owner)):  # noqa: B008
        sent = _sent(body, "title", "target", "effort", "local_only", "on_record",
                     "use_memory")
        for name in ("title", "target", "local_only", "on_record", "use_memory"):
            if name in sent and sent[name] is None:
                raise HTTPException(422, f"{name} cannot be null. Leave it out to keep it "
                                         "as it is.")
        return _do(convs.change_conversation, core, conversation_id, actor=who.actor,
                   **sent)

    @router.post("/conversations/{conversation_id}/archive")
    def archive(conversation_id: str, who: Principal = Depends(owner)):  # noqa: B008
        return _do(convs.archive_conversation, core, conversation_id, actor=who.actor)

    @router.post("/conversations/{conversation_id}/restore")
    def restore(conversation_id: str, who: Principal = Depends(owner)):  # noqa: B008
        return _do(convs.restore_conversation, core, conversation_id, actor=who.actor)

    @router.delete("/conversations/{conversation_id}/off-record")
    def delete_off_record(conversation_id: str, _: Principal = Depends(owner)):  # noqa: B008
        # Only the part that is off the record; the route below this one
        # deletes the whole conversation.
        return _do(convs.delete_off_record, core, conversation_id)

    @router.delete("/conversations/{conversation_id}")
    def delete(conversation_id: str, who: Principal = Depends(owner)):  # noqa: B008
        # The whole conversation, for good: what is on the Tape is erased
        # from it. Owner only; a token is refused before this is reached.
        return _do(deletion.delete_conversation, core, conversation_id, actor=who.actor)

    # --- talking -----------------------------------------------------------------

    async def reply_stream(request: Request, conversation_id: str, who: Principal,
                           **how: Any) -> StreamingResponse:
        # Everything that can refuse happens here, before the stream opens, so
        # a refusal is an ordinary JSON answer.
        try:
            run = await replies.begin(core, conversation_id, actor=who.actor, **how)
        except ChatError as exc:
            raise HTTPException(exc.status, exc.detail) from None

        async def body():
            listening = replies.listen(run, disconnected=request.is_disconnected)
            async with contextlib.aclosing(listening):
                async for name, data in listening:
                    yield ": keep-alive\n\n" if name == "ping" else _sse(name, data)

        return _ReplyStream(run, body(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/conversations/{conversation_id}/messages")
    async def send(conversation_id: str, body: SendBody, request: Request,
                   who: Principal = Depends(owner)):  # noqa: B008
        how = {"target": KEEP, "effort": KEEP} | _sent(body, "target", "effort") | _web(body)
        return await reply_stream(request, conversation_id, who, text=body.text, **how)

    @router.post("/conversations/{conversation_id}/regenerate")
    async def regenerate(conversation_id: str, request: Request,
                         body: RegenerateBody | None = None,
                         who: Principal = Depends(owner)):  # noqa: B008
        how = {"target": KEEP, "effort": KEEP} | (
            _sent(body, "target", "effort") | _web(body) if body else {})
        return await reply_stream(request, conversation_id, who, regenerate=True, **how)

    @router.post("/conversations/{conversation_id}/stop")
    async def stop(conversation_id: str, _: Principal = Depends(owner)):  # noqa: B008
        if conversation_id not in store.svc(core).runs:
            # Nothing to stop: only check that the conversation exists.
            await run_in_threadpool(_do, convs.find, core, conversation_id)
            return {"stopped": False}
        try:
            return await replies.stop(core, conversation_id)
        except ChatError as exc:
            raise HTTPException(exc.status, exc.detail) from None

    return router
