"""Fake model providers for the models tests. Nothing here touches a network.

Each fake speaks one provider's wire format through ``httpx.MockTransport``
and can be told to misbehave in one named way.
"""

from __future__ import annotations

import json

import httpx

from jimmyhq.models import Brain, brain

HTTP_TYPES = ("openai_compatible", "openai", "anthropic", "google")
TOKENS_IN, TOKENS_OUT = 12, 5
PIECES = ("Hello", " there")
TEXT = "".join(PIECES)
MODELS = ["model-a", "model-b"]


def _sse(events: list[tuple[str | None, object]]) -> httpx.Response:
    lines = []
    for name, data in events:
        if name:
            lines.append(f"event: {name}")
        lines.append(f"data: {data if isinstance(data, str) else json.dumps(data)}")
        lines.append("")
    return httpx.Response(200, text="\n".join(lines) + "\n",
                          headers={"content-type": "text/event-stream"})


def _openai(request: httpx.Request, body: dict | None, text: str, cut: bool):
    if request.method == "GET":
        return httpx.Response(200, json={"object": "list",
                                         "data": [{"id": m} for m in reversed(MODELS)]})
    finish = "length" if cut else "stop"
    usage = {"prompt_tokens": TOKENS_IN, "completion_tokens": TOKENS_OUT}
    if body.get("stream"):
        chunks = [(None, {"choices": [{"delta": {"role": "assistant", "content": ""}}]})]
        chunks += [(None, {"choices": [{"delta": {"content": p}}]}) for p in _split(text)]
        chunks += [(None, {"choices": [{"delta": {}, "finish_reason": finish}]}),
                   (None, {"choices": [], "usage": usage}), (None, "[DONE]")]
        return _sse(chunks)
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": finish}], "usage": usage})


def _anthropic(request: httpx.Request, body: dict | None, text: str, cut: bool):
    if request.method == "GET":
        return httpx.Response(200, json={"data": [{"id": m, "type": "model"} for m in MODELS],
                                         "has_more": False, "last_id": MODELS[-1]})
    stop = "max_tokens" if cut else "end_turn"
    if body.get("stream"):
        events = [
            ("message_start", {"type": "message_start", "message": {
                "usage": {"input_tokens": TOKENS_IN, "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("ping", {"type": "ping"}),
        ]
        events += [("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": p}})
                   for p in _split(text)]
        events += [
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop},
                               "usage": {"output_tokens": TOKENS_OUT}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        return _sse(events)
    return httpx.Response(200, json={
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": TOKENS_IN - 2, "cache_read_input_tokens": 2,
                  "output_tokens": TOKENS_OUT}})


def _google(request: httpx.Request, body: dict | None, text: str, cut: bool):
    if request.method == "GET":
        return httpx.Response(200, json={"models": [
            {"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]}
            for m in MODELS
        ] + [{"name": "models/not-for-chat", "supportedGenerationMethods": ["predict"]}]})
    finish = "MAX_TOKENS" if cut else "STOP"
    usage = {"promptTokenCount": TOKENS_IN, "candidatesTokenCount": TOKENS_OUT - 1,
             "thoughtsTokenCount": 1}

    def chunk(piece: str, last: bool) -> dict:
        candidate = {"content": {"role": "model", "parts": [{"text": piece}]}}
        if last:
            candidate["finishReason"] = finish
        return {"candidates": [candidate]} | ({"usageMetadata": usage} if last else {})

    if "streamGenerateContent" in request.url.path:
        assert request.url.params["alt"] == "sse"
        pieces = _split(text)
        return _sse([(None, chunk(p, i == len(pieces) - 1)) for i, p in enumerate(pieces)])
    return httpx.Response(200, json=chunk(text, True))


def _split(text: str) -> list[str]:
    return list(PIECES) if text == TEXT else [text[: len(text) // 2], text[len(text) // 2:]]


_WIRE = {"openai_compatible": _openai, "openai": _openai, "anthropic": _anthropic,
         "google": _google}
_ERRORS = {
    "auth": (401, {"error": {"message": "Incorrect API key provided."}}),
    "rate": (429, {"error": {"message": "Slow down."}}),
    "server": (500, {"error": {"message": "Boom."}}),
    "missing": (404, {"error": {"message": "No such thing."}}),
    "rejected": (400, {"error": {"message": "That request is malformed."}}),
}


class FakeProvider:
    """One fake provider. ``mode`` names how it behaves; ``requests`` records calls."""

    def __init__(self, kind: str, mode: str = "ok", text: str = TEXT):
        self.kind, self.mode, self.text = kind, mode, text
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content) if request.content else None
        if body is not None:
            self.bodies.append(body)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("too slow", request=request)
        if self.mode == "down":
            raise httpx.ConnectError("refused", request=request)
        if self.mode == "garbage":
            return httpx.Response(200, text="<html>not json</html>")
        if self.mode == "redirect":
            return httpx.Response(307, headers={"location": "https://elsewhere.example/v1"})
        if self.mode in _ERRORS:
            status, payload = _ERRORS[self.mode]
            return httpx.Response(status, json=payload)
        return _WIRE[self.kind](request, body, self.text, self.mode == "truncated")


class Network(httpx.MockTransport):
    """Routes each request to the fake that owns its host."""

    def __init__(self, hosts: dict[str, FakeProvider]):
        self.hosts = hosts
        super().__init__(self._route)

    def _route(self, request: httpx.Request) -> httpx.Response:
        return self.hosts[request.url.host](request)


BASE_URLS = {
    "openai_compatible": "http://127.0.0.1:11434/v1",
    "openai": "https://openai.example.com/v1",
    "anthropic": "https://anthropic.example.com",
    "google": "https://google.example.com/v1beta",
}


def add(b: Brain, provider_id: str, kind: str = "openai_compatible", *, host: str | None = None,
        key: str | None = None, **fields):
    """Save a provider on a made-up address and return it."""
    url = BASE_URLS[kind] if host is None else (
        f"http://{host}/v1" if kind.startswith("openai") else f"http://{host}")
    provider = b.save_provider({"id": provider_id, "type": kind, "label": provider_id.title(),
                                "base_url": url} | fields, actor="owner")
    if key:
        b.set_key(provider_id, key)
    return provider


def wire(core, hosts: dict[str, FakeProvider]) -> Brain:
    b = brain(core)
    b.transport = Network(hosts)
    return b


class BotoError(Exception):
    """Shaped like botocore's ClientError."""

    def __init__(self, code: str, status: int, message: str = "nope"):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message},
                         "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeBedrock:
    """Stands in for both boto3 clients (``bedrock`` and ``bedrock-runtime``)."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.calls: list[dict] = []

    def __call__(self, service, provider, timeouts):
        self.asked = (service, provider.region, provider.profile)
        return self

    def _fail(self):
        if self.mode == "throttled":
            raise BotoError("ThrottlingException", 429)
        if self.mode == "denied":
            raise BotoError("AccessDeniedException", 403)

    def list_foundation_models(self):
        self._fail()
        return {"modelSummaries": [{"modelId": m} for m in MODELS]}

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        self._fail()
        return {"output": {"message": {"role": "assistant", "content": [{"text": TEXT}]}},
                "stopReason": "max_tokens" if self.mode == "truncated" else "end_turn",
                "usage": {"inputTokens": TOKENS_IN, "outputTokens": TOKENS_OUT}}

    def converse_stream(self, **kwargs):
        self.calls.append(kwargs)
        self._fail()
        events = [{"messageStart": {"role": "assistant"}}]
        events += [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": p}}}
                   for p in PIECES]
        stop = "max_tokens" if self.mode == "truncated" else "end_turn"
        events += [{"messageStop": {"stopReason": stop}},
                   {"metadata": {"usage": {"inputTokens": TOKENS_IN,
                                           "outputTokens": TOKENS_OUT}}}]
        return {"stream": iter(events)}
