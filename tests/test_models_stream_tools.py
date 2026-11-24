"""Tools with streaming: text arrives as it is written and the tool calls come
on the final reply. Nothing here touches a network."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jimmyhq.models import ModelError, Reply, Target, brain
from jimmyhq.models.adapters import ADAPTERS
from jimmyhq.models.shapes import RawReply
from test_models_fakes import HTTP_TYPES, PIECES, TEXT, FakeProvider, add, wire
from test_models_tools import (
    ARGS,
    ASKING,
    TEXT_ONLY,
    TOOLS,
    WHO,
    Bedrock,
    adapter,
    continued,
)

HOME = "127.0.0.1"


def sse(events: list[tuple[str | None, object]]) -> httpx.Response:
    lines = []
    for name, data in events:
        if name:
            lines.append(f"event: {name}")
        lines.append(f"data: {data if isinstance(data, str) else json.dumps(data)}")
        lines.append("")
    return httpx.Response(200, text="\n".join(lines) + "\n",
                          headers={"content-type": "text/event-stream"})


class Streams:
    """A provider that records each request and answers with one fixed stream."""

    def __init__(self, events: list[tuple[str | None, object]]):
        self.events = events
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return sse(self.events)


def collect(stream) -> tuple[list[str], RawReply | Reply]:
    async def gather():
        return [item async for item in stream]

    *pieces, last = asyncio.run(gather())
    return pieces, last


def openai_chunks(calls=True) -> list[tuple[None, object]]:
    whole = json.dumps(ARGS)
    chunks: list[dict] = [
        {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
        {"choices": [{"delta": {"content": "Look"}}]},
        {"choices": [{"delta": {"content": "ing."}}]},
    ]
    if calls:
        chunks += [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1",
                                                    "type": "function",
                                                    "function": {"name": "look_up",
                                                                 "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
                "arguments": whole[:7]}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": whole[7:]}},
                {"index": 1, "id": "call_2", "type": "function",
                 "function": {"name": "finish", "arguments": "{}"}}]}}]},
        ]
    chunks += [{"choices": [{"delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]},
               {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}]
    return [(None, c) for c in chunks] + [(None, "[DONE]")]


@pytest.mark.parametrize("kind", ["openai_compatible", "openai"])
def test_an_openai_stream_puts_tool_calls_together_from_their_pieces(kind):
    fake = Streams(openai_chunks())
    pieces, raw = collect(adapter(kind, fake).stream(ASKING))
    assert pieces == ["Look", "ing."] and raw.text == "Looking."
    assert [(c.id, c.name, c.arguments) for c in raw.tool_calls] == [
        ("call_1", "look_up", ARGS), ("call_2", "finish", {})]
    assert (raw.input_tokens, raw.output_tokens) == (9, 4)
    body = fake.bodies[0]
    assert body["stream"] is True and [t["function"]["name"] for t in body["tools"]] == [
        "look_up", "finish"]


def test_an_openai_compatible_server_that_sends_whole_calls_with_no_index_is_read():
    fake = Streams([
        (None, {"choices": [{"delta": {"tool_calls": [
            {"id": "a", "function": {"name": "look_up", "arguments": ARGS}}]}}]}),
        (None, {"choices": [{"delta": {"tool_calls": [
            {"id": "b", "function": {"name": "finish", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}),
        (None, "[DONE]")])
    _, raw = collect(adapter("openai_compatible", fake).stream(ASKING))
    assert [(c.id, c.name, c.arguments) for c in raw.tool_calls] == [
        ("a", "look_up", ARGS), ("b", "finish", {})]


def test_a_streamed_tool_call_with_broken_arguments_is_refused():
    fake = Streams([(None, {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "a", "function": {"name": "look_up", "arguments": "{not json"}}]}}]}),
        (None, "[DONE]")])
    with pytest.raises(ModelError) as caught:
        collect(adapter("openai_compatible", fake).stream(ASKING))
    assert caught.value.kind == "bad_tool_call"


def anthropic_events() -> list[tuple[str, dict]]:
    whole = json.dumps(ARGS)

    def delta(index: int, **fields) -> tuple[str, dict]:
        return ("content_block_delta", {"type": "content_block_delta", "index": index,
                                        "delta": fields})

    def start(index: int, block: dict) -> tuple[str, dict]:
        return ("content_block_start", {"type": "content_block_start", "index": index,
                                        "content_block": block})

    return [
        ("message_start", {"type": "message_start", "message": {
            "usage": {"input_tokens": 9, "output_tokens": 1}}}),
        start(0, {"type": "thinking", "thinking": "", "signature": ""}),
        delta(0, type="thinking_delta", thinking="Where "),
        delta(0, type="thinking_delta", thinking="is it?"),
        delta(0, type="signature_delta", signature="sig-abc"),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        start(1, {"type": "text", "text": ""}),
        delta(1, type="text_delta", text="Look"),
        delta(1, type="text_delta", text="ing."),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        start(2, {"type": "tool_use", "id": "toolu_1", "name": "look_up", "input": {}}),
        delta(2, type="input_json_delta", partial_json=""),
        delta(2, type="input_json_delta", partial_json=whole[:7]),
        delta(2, type="input_json_delta", partial_json=whole[7:]),
        ("content_block_stop", {"type": "content_block_stop", "index": 2}),
        start(3, {"type": "tool_use", "id": "toolu_2", "name": "finish", "input": {}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 3}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 4}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def test_an_anthropic_stream_rebuilds_the_tool_calls_and_keeps_the_whole_answer():
    fake = Streams(anthropic_events())
    made = adapter("anthropic", fake)
    pieces, raw = collect(made.stream(ASKING))
    assert pieces == ["Look", "ing."] and raw.text == "Looking."
    first, second = raw.tool_calls
    assert (first.id, first.name, first.arguments) == ("toolu_1", "look_up", ARGS)
    assert (second.id, second.name, second.arguments) == ("toolu_2", "finish", {})
    assert (raw.input_tokens, raw.output_tokens) == (9, 4)
    # The whole answer, thinking and its signature included, rides on the first
    # call so it can be sent back exactly as it came.
    kept = json.loads(first.signature.split(":", 2)[2])
    assert kept == [
        {"type": "thinking", "thinking": "Where is it?", "signature": "sig-abc"},
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "toolu_1", "name": "look_up", "input": ARGS},
        {"type": "tool_use", "id": "toolu_2", "name": "finish", "input": {}}]
    assert second.signature is None
    # Sent back on the next call, the blocks are the ones that arrived.
    again = Streams(anthropic_events())
    request = continued("toolu_1", first.signature, images=False)
    request.messages[1][1][1:] = [
        {"type": "tool_call", "id": "toolu_1", "name": "look_up", "arguments": ARGS,
         "signature": first.signature},
        {"type": "tool_call", "id": "toolu_2", "name": "finish", "arguments": {},
         "signature": None}]
    collect(adapter("anthropic", again).stream(request))
    assert again.bodies[0]["messages"][1]["content"] == kept


def test_a_google_stream_collects_the_function_calls_as_they_pass():
    fake = Streams([
        (None, {"candidates": [{"content": {"role": "model", "parts": [{"text": "Look"}]}}]}),
        (None, {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "ing."},
            {"functionCall": {"name": "look_up", "args": ARGS}, "thoughtSignature": "sig-1"}]}}]}),
        (None, {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"id": "fc_2", "name": "finish", "args": {}}}]},
            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4}}),
    ])
    pieces, raw = collect(adapter("google", fake).stream(ASKING))
    assert pieces == ["Look", "ing."] and raw.text == "Looking."
    first, second = raw.tool_calls
    assert (first.name, first.arguments) == ("look_up", ARGS)
    assert first.signature.endswith("sig-1") and first.id
    assert (second.id, second.name, second.arguments) == ("fc_2", "finish", {})
    assert (raw.input_tokens, raw.output_tokens) == (9, 4)


@pytest.mark.parametrize("kind", HTTP_TYPES)
def test_a_text_only_stream_sends_and_yields_exactly_what_it_always_has(kind):
    """The same body ``chat`` sends plus the provider's own "stream" switch,
    the same pieces, and a final reply with no tool calls."""
    plain, streamed = FakeProvider(kind), FakeProvider(kind)
    asyncio.run(adapter(kind, plain).chat(TEXT_ONLY))
    pieces, raw = collect(adapter(kind, streamed).stream(TEXT_ONLY))
    extra = {"openai_compatible": {"stream": True, "stream_options": {"include_usage": True}},
             "openai": {"stream": True, "stream_options": {"include_usage": True}},
             "anthropic": {"stream": True}, "google": {}}[kind]
    assert streamed.bodies[0] == plain.bodies[0] | extra
    assert tuple(pieces) == PIECES and raw.text == TEXT and raw.tool_calls == ()


def test_which_adapters_stream_tool_calls_is_written_down():
    assert {kind: cls.streams_tools for kind, cls in ADAPTERS.items()} == {
        "openai_compatible": True, "openai": True, "anthropic": True, "google": True,
        "bedrock": False}


@pytest.fixture
def home(core):
    def make(fake):
        b = wire(core, {HOME: fake})
        add(b, "home", host=f"{HOME}:11434")
        b.set_role("default", Target("home", "model-a"), actor="owner")
        return b

    return make


def called(core) -> dict:
    return [e.payload for e in core.tape.after(0) if e.kind == "model.called"][-1]


def test_the_brain_streams_text_and_hands_the_tool_calls_over_on_the_reply(home, core):
    b = home(Streams(openai_chunks()))
    pieces, reply = collect(b.stream([{"role": "user", "content": "Go"}], tools=TOOLS, **WHO))
    assert pieces == ["Look", "ing."] and isinstance(reply, Reply)
    assert reply.text == "Looking." and [c.name for c in reply.tool_calls] == ["look_up",
                                                                               "finish"]
    assert reply.as_message()["tool_calls"][0]["arguments"] == ARGS
    receipt = called(core)
    assert (receipt["streamed"], receipt["tools"], receipt["tool_calls"]) == (True, 2, 2)


def test_a_streamed_call_to_a_tool_that_was_not_offered_is_refused(home):
    chunks = openai_chunks()
    chunks[3][1]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] = "delete_all"
    b = home(Streams(chunks))
    with pytest.raises(ModelError) as caught:
        collect(b.stream([{"role": "user", "content": "Go"}], tools=TOOLS, **WHO))
    assert caught.value.kind == "bad_tool_call"


def test_a_streamed_answer_with_tools_on_offer_and_none_called_is_just_text(home):
    b = home(Streams(openai_chunks(calls=False)))
    pieces, reply = collect(b.stream([{"role": "user", "content": "Go"}], tools=TOOLS, **WHO))
    assert "".join(pieces) == reply.text == "Looking." and reply.tool_calls == ()


def test_bedrock_cannot_stream_tool_calls_so_the_brain_asks_whole_and_yields_the_text_once(
        core):
    b = brain(core)
    b.bedrock_clients = Bedrock([
        {"text": "Looking."},
        {"toolUse": {"toolUseId": "tooluse_1", "name": "look_up", "input": ARGS}}])
    b.save_provider({"id": "aws", "type": "bedrock", "label": "Cloud", "region": "us-east-1"},
                    actor="owner")
    pieces, reply = collect(b.stream([{"role": "user", "content": "Go"}], provider_id="aws",
                                     model="model-a", tools=TOOLS, **WHO))
    assert pieces == ["Looking."] and reply.text == "Looking."
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("tooluse_1", "look_up", ARGS)]
    assert len(b.bedrock_clients.calls) == 1  # converse, not converse_stream
    # The receipt says how the provider was really asked.
    assert called(core)["streamed"] is False
