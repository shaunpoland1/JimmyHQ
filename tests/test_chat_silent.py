"""A reply that reasons silently for too long is stopped with a sentence that says
what to do, and one that is writing or looking things up never is. The model is
scripted, the clock is ours, and the only real waiting is a few hundredths of a
second."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from jimmyhq import chat
from jimmyhq.chat import replies
from test_chat_fakes import SSE, SlowProvider, models, of_kind
from test_chat_web import QUESTION, Scripted, search, setup


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    ticking = Clock()
    monkeypatch.setattr(replies, "clock", ticking)
    monkeypatch.setattr(replies, "WATCH_POLL_S", 0.01)
    return ticking


def new(core, **fields):
    return chat.start_conversation(core, **fields)["id"]


async def until(condition, seconds=20.0):
    """Wait for ``condition`` by the wall clock, not by counting short sleeps:
    on Windows a sleep shorter than the timer's resolution returns at once, so
    a counted loop can run out before any real time has passed."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the condition never became true")


def run_quiet(core, provider, clock, jump, **how):
    """Send, wait until the model is streaming, move the clock on by ``jump``
    seconds, and return the reply's events."""
    async def go():
        started = await replies.begin(core, new(core), text=QUESTION, **how)
        events = []

        async def listen():
            async for event in replies.listen(started):
                events.append(event)

        listening = asyncio.ensure_future(listen())
        await until(lambda: provider.streaming.is_set())
        await asyncio.sleep(0.05)
        clock.now += jump
        await asyncio.wait({listening}, timeout=1.0)
        provider.release.set()
        await asyncio.wait({listening}, timeout=5.0)
        return events

    return asyncio.run(go())


def test_the_limit_is_a_setting_that_defaults_to_four_minutes_and_can_be_switched_off(core):
    setting = core.settings.get
    assert setting("chat.silent_reply_limit_s") == 240
    core.settings.set("chat.silent_reply_limit_s", 0, actor="owner")
    assert setting("chat.silent_reply_limit_s") == 0
    with pytest.raises(Exception):  # noqa: B017, PT011 - any refusal will do
        core.settings.set("chat.silent_reply_limit_s", 3601, actor="owner")


def test_a_reply_that_stays_silent_past_the_limit_fails_and_says_what_to_do(core, clock):
    home = SlowProvider(hold_after=0)
    models(core, home=home)
    events = run_quiet(core, home, clock, jump=241)
    name, data = events[-1]
    reply = data["message"]
    assert name == "error" and reply["state"] == "failed" and reply["text"] == ""
    assert "240 seconds" in reply["error"] and "lower the effort" in reply["error"].lower()
    assert "Longest silent wait" in reply["error"] and "Settings, Chat" in reply["error"]


def test_a_reply_that_is_still_inside_the_limit_is_left_alone(core, clock):
    home = SlowProvider(pieces=("Hello", " there"), hold_after=0)
    models(core, home=home)
    events = run_quiet(core, home, clock, jump=239)
    assert events[-1][0] == "done" and events[-1][1]["message"]["text"] == "Hello there"


def test_a_reply_that_is_writing_text_is_never_cut_however_long_it_takes(core, clock):
    home = SlowProvider(pieces=("Hello", " there", " friend"), hold_after=1)
    models(core, home=home)
    events = run_quiet(core, home, clock, jump=5000)
    reply = events[-1][1]["message"]
    assert events[-1][0] == "done" and reply["state"] == "complete"
    assert reply["text"] == "Hello there friend"


def test_a_limit_of_zero_never_stops_a_quiet_reply(core, clock):
    home = SlowProvider(hold_after=0)
    models(core, home=home)
    core.settings.set("chat.silent_reply_limit_s", 0, actor="owner")
    events = run_quiet(core, home, clock, jump=100000)
    assert events[-1][1]["message"]["state"] == "complete"


def test_a_reply_that_has_looked_something_up_is_never_cut_for_being_quiet(core, clock):
    looked = Scripted([search()])
    quiet = SlowProvider(hold_after=0)

    async def model(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" or not looked.bodies:
            return looked(request)
        return await quiet(request)

    setup(core, model, steps_per_reply=3)
    events = run_quiet(core, quiet, clock, jump=5000, web=True)
    assert any(name == "step" for name, _ in events)
    assert events[-1][0] == "done" and events[-1][1]["message"]["state"] == "complete"


def test_a_reply_stopped_for_silence_is_stored_once_and_frees_the_conversation(core, clock):
    home = SlowProvider(hold_after=0)
    models(core, home=home)
    events = run_quiet(core, home, clock, jump=300)
    assert [n for n, _ in events].count("error") == 1
    conv_id = events[0][1]["conversation_id"]
    stored = chat.get_conversation(core, conv_id)["messages"]
    assert [m["state"] for m in stored if m["role"] == "assistant"] == ["failed"]
    assert of_kind(core, "model.failed")  # the model call left its receipt


class Reasoner:
    """A model that spends its whole answer allowance without writing a word."""

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        lines = [{"choices": [{"delta": {}, "finish_reason": "length"}]}]
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in lines) + "data: [DONE]\n\n"
        return httpx.Response(200, headers=SSE, text=text)


def test_a_model_that_runs_out_of_answer_while_reasoning_says_so_and_what_to_change(core):
    models(core, home=Reasoner())
    reply = asyncio.run(_collect(core))
    assert reply["state"] == "failed" and reply["text"] == ""
    assert "whole answer allowance on reasoning" in reply["error"]
    assert "lower the effort" in reply["error"].lower()
    assert "models.default_max_tokens" in reply["error"]


def test_a_reply_cut_off_after_some_text_keeps_the_ordinary_sentence(core):
    from test_models_fakes import FakeProvider
    models(core, home=FakeProvider("openai_compatible", "truncated"))
    reply = asyncio.run(_collect(core))
    assert reply["state"] == "failed" and reply["text"] != ""
    assert "cut off" in reply["error"] and "allowance" not in reply["error"]


async def _collect(core):
    items = [i async for i in chat.send(core, new(core), QUESTION)]
    return items[-1]
