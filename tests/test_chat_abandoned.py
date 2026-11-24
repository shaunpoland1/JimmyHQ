"""A reply nobody is listening to any more, and a send that arrives while one
is being written. However the browser behaves, a conversation is never left
busy for good: an abandoned reply ends as "stopped" with what it had written,
the conversation is free again, and a second send gets either an answer or a
sentence, never silence.

The requests go straight into the ASGI app the way the server hands them
over, so a client can hang up at an exact moment."""

from __future__ import annotations

import asyncio
import json

import pytest

from jimmyhq import chat
from jimmyhq.chat import replies, store
from jimmyhq.web.app import create_app
from test_chat_fakes import SlowProvider
from test_chat_web import QUESTION, setup

PASSWORD = "correct horse battery"
BASE = "/api/chat/conversations"
AT_ONCE = ""  # the client is gone before the answer begins
AFTER_START = "event: start"  # the client is gone once it has seen the reply begin


async def call(app, method, path, body=None, cookie=None, *, hang_up=None, timeout=10.0):
    """One request. ``hang_up`` is text in the answer after which the client
    is gone (``AT_ONCE``: gone as soon as the request has been read)."""
    raw = json.dumps(body).encode() if body is not None else b""
    headers = [(b"host", b"testserver"), (b"content-type", b"application/json"),
               (b"origin", b"http://testserver")]
    if cookie:
        headers.append((b"cookie", cookie))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 50000), "server": ("testserver", 80)}
    asked = False
    gone = asyncio.Event()
    out = {"status": None, "headers": {}, "body": b""}

    async def receive():
        nonlocal asked
        if not asked:
            asked = True
            if hang_up == AT_ONCE:
                gone.set()
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if gone.is_set():
            return  # written to a closed connection: dropped, as the server does
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")
            if hang_up and hang_up.encode() in out["body"]:
                gone.set()

    await asyncio.wait_for(app(scope, receive, send), timeout)
    return out


def events(answer) -> list[tuple[str, dict]]:
    out = []
    for block in answer["body"].decode().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if lines:
            out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


async def until(condition, seconds=5.0):
    for _ in range(int(seconds / 0.005)):
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the condition never became true")


def scene(core, steps, *, on_record=True):
    """Run ``steps(post, model, conv_id)`` against the app with the owner
    signed in and one slow local model, web lookup on."""
    model = SlowProvider()
    setup(core, model)
    conv_id = chat.start_conversation(core, on_record=on_record)["id"]
    app = create_app(core)

    async def go():
        async with app.router.lifespan_context(app):
            signed = await call(app, "POST", "/api/auth/setup",
                                {"name": "Sam", "password": PASSWORD})
            cookie = signed["headers"]["set-cookie"].split(";")[0].encode()

            def post(tail, body=None, **how):
                return call(app, "POST", f"{BASE}/{conv_id}{tail}", body, cookie, **how)

            try:
                return await steps(post, model, conv_id)
            finally:
                model.release.set()

    return asyncio.run(go())


def thread(core, conv_id) -> list[tuple[str, str | None, str]]:
    return [(m["role"], m.get("state"), m["text"])
            for m in chat.get_conversation(core, conv_id)["messages"]]


def free(core, conv_id) -> bool:
    return conv_id not in store.svc(core).runs


@pytest.mark.parametrize("web", [True, False], ids=["web", "no web"])
@pytest.mark.parametrize("on_record", [True, False], ids=["on the record", "off the record"])
@pytest.mark.parametrize("hang_up", [AFTER_START, AT_ONCE], ids=["after start", "at once"])
def test_a_client_that_hangs_up_leaves_a_stopped_reply_and_the_next_send_is_answered(
        core, web, on_record, hang_up):
    async def steps(post, model, conv_id):
        first = await post("/messages", {"text": QUESTION, "web": web}, hang_up=hang_up)
        assert first["status"] == (200 if hang_up else None)  # None: it never saw an answer
        # Nobody released the model: the reply ended because its listener left.
        await until(lambda: free(core, conv_id))
        kept = thread(core, conv_id)
        model.release.set()
        second = await post("/messages", {"text": QUESTION, "web": web})
        return kept, events(second)

    kept, second = scene(core, steps, on_record=on_record)
    assert [(role, state) for role, state, _ in kept] == [("user", None), ("assistant", "stopped")]
    assert kept[1][2] in ("", "Hello")  # whatever had arrived, and nothing more
    assert [name for name, _ in second][0] == "start" and second[-1][0] == "done"
    assert second[-1][1]["message"]["state"] == "complete"
    assert [(role, state) for role, state, _ in thread(core, chat_id(second))] == [
        ("user", None), ("assistant", "stopped"), ("user", None), ("assistant", "complete")]
    assert free(core, chat_id(second))


def chat_id(stream) -> str:
    return stream[0][1]["conversation_id"]


@pytest.mark.parametrize("web", [True, False], ids=["web", "no web"])
@pytest.mark.parametrize("on_record", [True, False], ids=["on the record", "off the record"])
def test_the_same_message_sent_again_while_its_reply_is_being_written_is_refused_in_a_sentence(
        core, web, on_record):
    async def steps(post, model, conv_id):
        first = asyncio.ensure_future(post("/messages", {"text": QUESTION, "web": web}))
        await until(model.streaming.is_set)
        refused = [await post("/messages", {"text": QUESTION, "web": web}),
                   await post("/regenerate", {"web": web})]
        during = thread(core, conv_id)
        model.release.set()
        return refused, during, events(await first)

    refused, during, first = scene(core, steps, on_record=on_record)
    for answer in refused:
        assert answer["status"] == 409
        assert json.loads(answer["body"]) == {"detail": replies.BUSY}
    # The refused copy was not stored, and the reply being written went on to its end.
    assert [role for role, _, _ in during] == ["user"]
    assert first[-1][0] == "done" and first[-1][1]["message"]["text"] == "Hello there friend"


@pytest.mark.parametrize("on_record", [True, False], ids=["on the record", "off the record"])
def test_a_regenerate_whose_client_hangs_up_is_stored_as_stopped_and_can_be_asked_again(
        core, on_record):
    async def steps(post, model, conv_id):
        model.release.set()
        await post("/messages", {"text": QUESTION})
        model.release.clear()
        abandoned = await post("/regenerate", {"web": True}, hang_up=AFTER_START)
        assert abandoned["status"] == 200
        await until(lambda: free(core, conv_id))
        kept = thread(core, conv_id)
        model.release.set()
        return kept, events(await post("/regenerate", {"web": True}))

    kept, again = scene(core, steps, on_record=on_record)
    assert [(role, state) for role, state, _ in kept] == [("user", None), ("assistant", "stopped")]
    assert again[-1][0] == "done" and again[-1][1]["message"]["state"] == "complete"
    assert [(role, state) for role, state, _ in thread(core, chat_id(again))] == [
        ("user", None), ("assistant", "complete")]


def test_a_reply_abandoned_while_the_model_is_writing_keeps_what_was_written(core):
    async def steps(post, model, conv_id):
        first = await post("/messages", {"text": QUESTION, "web": True}, hang_up="event: delta")
        await until(lambda: free(core, conv_id))
        return events(first), thread(core, conv_id)

    first, kept = scene(core, steps)
    assert [name for name, _ in first] == ["start", "delta"]
    assert kept == [("user", None, QUESTION), ("assistant", "stopped", "Hello")]
    answered = [e for e in core.tape.after(0, limit=1000) if e.kind == "chat.message.answered"]
    assert [e.payload["state"] for e in answered] == ["stopped"]
    assert answered[0].payload["web"]["reason"] == "The reply was stopped before anything " \
                                                   "was looked up."
