"""Helpers for the chat tests: fake providers wired through the models module.
Nothing here touches a network."""

from __future__ import annotations

import asyncio
import json
import threading

import httpx

from jimmyhq.models import Target
from test_models_fakes import FakeProvider, add, wire

HOME, CLOUD = "127.0.0.1", "cloud.example.com"
SSE = {"content-type": "text/event-stream"}


def _chunk(data) -> bytes:
    return f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n".encode()


class SlowProvider:
    """An OpenAI-compatible fake that streams its first pieces, then waits to
    be released before it finishes, or fails if ``then`` is "fail"."""

    def __init__(self, pieces=("Hello", " there", " friend"), hold_after=1, then="finish"):
        self.pieces, self.hold_after, self.then = pieces, hold_after, then
        self.release = threading.Event()
        self.streaming = threading.Event()
        self.bodies: list[dict] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        body = json.loads(request.content)
        self.bodies.append(body)
        if not body.get("stream"):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "".join(self.pieces)},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
        return httpx.Response(200, headers=SSE, content=self._stream())

    async def _stream(self):
        for piece in self.pieces[:self.hold_after]:
            yield _chunk({"choices": [{"delta": {"content": piece}}]})
        self.streaming.set()
        while not self.release.is_set():
            await asyncio.sleep(0.005)
        if self.then == "fail":
            raise httpx.ReadError("the line dropped")
        for piece in self.pieces[self.hold_after:]:
            yield _chunk({"choices": [{"delta": {"content": piece}}]})
        yield _chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        yield _chunk({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3}})
        yield _chunk("[DONE]")


def models(core, home=None, cloud=None, *, cheap=None, role=True):
    """A local provider as the default role's first choice and a hosted one as
    its fallback. Returns the fakes by host."""
    fakes = {HOME: home or FakeProvider("openai_compatible"),
             CLOUD: cloud or FakeProvider("openai_compatible", text="From the cloud")}
    b = wire(core, fakes)
    add(b, "home", host=f"{HOME}:11434")
    add(b, "cloud", host=CLOUD, key="sk-test-cloud-key-000000")
    if role:
        b.set_role("default", Target("home", "model-a"), [Target("cloud", "model-b")],
                   actor="owner")
    if cheap:
        b.set_role("cheap", Target(*cheap), actor="owner")
    return fakes


def sent_to(fake) -> list[dict]:
    """The messages the model was sent on its latest call."""
    return fake.bodies[-1]["messages"]


def of_kind(core, *wanted):
    return [e for e in core.tape.after(0, limit=100000) if e.kind in wanted]
