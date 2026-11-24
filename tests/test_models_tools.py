"""Tool calling and image input, through every adapter and through the Brain.

Nothing here touches a network: each provider is a small function that records
what it was sent and answers in that provider's wire format.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re

import httpx
import pytest

from jimmyhq.models import ModelError, Target, brain
from jimmyhq.models.adapters import ADAPTERS, Timeouts
from jimmyhq.models.adapters.bedrock import BedrockAdapter
from jimmyhq.models.shapes import ChatRequest, Provider, RawReply, ToolCall
from test_models_fakes import BASE_URLS, HTTP_TYPES, FakeProvider, add, wire

KEY = "sk-test-0123456789abcdef"
HOME, CLOUD = "127.0.0.1", "cloud.example.com"
WHO = {"actor": "agent:scout", "caller": "agent.step"}

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24).decode()
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 20).decode()
WEBP = base64.b64encode(b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8).decode()
PICTURE = {"type": "image", "media_type": "image/png", "data": PNG}

SCHEMA = {"type": "object", "properties": {"place": {"type": "string"}}, "required": ["place"]}
TOOLS = (
    {"name": "look_up", "description": "Look a place up.", "parameters": SCHEMA},
    {"name": "finish", "description": "Stop working.",
     "parameters": {"type": "object", "properties": {}}},
)
ARGS = {"place": "the harbour"}

TEXT_ONLY = ChatRequest("model-a", [("user", "Hi"), ("assistant", "Yes?"), ("user", "Hello")],
                        system="Be brief.", max_tokens=64)
ASKING = ChatRequest("model-a", [("user", "Where is it?")], max_tokens=64, tools=TOOLS)


def continued(call_id: str = "call_1", signature: str | None = None, *, is_error: bool = False,
              images: bool = True, tool_choice="auto") -> ChatRequest:
    """A conversation as the Brain hands it over once a tool has been run."""
    return ChatRequest("model-a", [
        ("user", [{"type": "text", "text": "What is on screen?"}, PICTURE]),
        ("assistant", [{"type": "text", "text": "Looking."},
                       {"type": "tool_call", "id": call_id, "name": "look_up",
                        "arguments": ARGS, "signature": signature}]),
        ("user", [{"type": "tool_result", "tool_call_id": call_id, "name": "look_up",
                   "text": "By the water.", "images": [PICTURE] if images else [],
                   "is_error": is_error},
                  {"type": "text", "text": "Go on."}]),
    ], max_tokens=64, tools=TOOLS, tool_choice=tool_choice)


class Canned:
    """A provider that records each request and answers with one fixed reply."""

    def __init__(self, reply: dict | None = None, status: int = 200):
        self.reply, self.status = reply, status
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content))
        return httpx.Response(self.status, json=self.reply)


def adapter(kind: str, fake):
    provider = Provider(id="p", type=kind, label="Fake", base_url=BASE_URLS[kind])
    return ADAPTERS[kind](provider, KEY, Timeouts(1, 5), httpx.MockTransport(fake))


def sent(kind: str, request: ChatRequest) -> dict:
    """The body an adapter sends for a request."""
    fake = FakeProvider(kind)
    asyncio.run(adapter(kind, fake).chat(request))
    return fake.bodies[0]


def openai_reply(calls: list[dict] | None = None, content: str | None = None,
                 finish: str = "tool_calls") -> dict:
    message: dict = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4}}


def openai_call(arguments: object = None, name: str = "look_up", call_id: str = "call_1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(ARGS) if arguments is None else arguments}}


class Bedrock:
    """Stands in for the boto3 runtime client and answers with one fixed reply."""

    def __init__(self, content: list | None = None, stop: str = "tool_use"):
        self.content = [{"text": "Hello"}] if content is None else content
        self.stop = stop
        self.calls: list[dict] = []

    def __call__(self, service, provider, timeouts):
        return self

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"role": "assistant", "content": self.content}},
                "stopReason": self.stop, "usage": {"inputTokens": 9, "outputTokens": 4}}


def bedrock(fake: Bedrock) -> BedrockAdapter:
    provider = Provider(id="aws", type="bedrock", label="Cloud", region="us-east-1")
    return BedrockAdapter(provider, None, Timeouts(1, 5), client_factory=fake)


# --- text-only calls are untouched ---------------------------------------------


@pytest.mark.parametrize("kind", HTTP_TYPES)
def test_a_text_only_call_sends_exactly_the_bytes_it_always_has(kind):
    expected = {
        "openai_compatible": {
            "model": "model-a",
            "messages": [{"role": "system", "content": "Be brief."},
                         {"role": "user", "content": "Hi"},
                         {"role": "assistant", "content": "Yes?"},
                         {"role": "user", "content": "Hello"}],
            "max_tokens": 64},
        "openai": {
            "model": "model-a",
            "messages": [{"role": "system", "content": "Be brief."},
                         {"role": "user", "content": "Hi"},
                         {"role": "assistant", "content": "Yes?"},
                         {"role": "user", "content": "Hello"}],
            "max_completion_tokens": 64},
        "anthropic": {
            "model": "model-a", "max_tokens": 64,
            "messages": [{"role": "user", "content": "Hi"},
                         {"role": "assistant", "content": "Yes?"},
                         {"role": "user", "content": "Hello"}],
            "system": "Be brief."},
        "google": {
            "contents": [{"role": "user", "parts": [{"text": "Hi"}]},
                         {"role": "model", "parts": [{"text": "Yes?"}]},
                         {"role": "user", "parts": [{"text": "Hello"}]}],
            "generationConfig": {"maxOutputTokens": 64},
            "systemInstruction": {"parts": [{"text": "Be brief."}]}},
    }[kind]
    fake = FakeProvider(kind)
    asyncio.run(adapter(kind, fake).chat(TEXT_ONLY))
    # The same bytes httpx makes of the body this adapter has always built.
    assert fake.requests[0].content == httpx.Request("POST", "http://x", json=expected).content


def test_a_text_only_call_through_the_brain_is_unchanged_on_the_wire_and_on_the_tape(core):
    fake = FakeProvider("openai_compatible")
    b = wire(core, {HOME: fake})
    add(b, "home", host=f"{HOME}:11434")
    b.set_role("default", Target("home", "model-a"), actor="owner")
    parts = [{"type": "text", "text": "One. "}, {"type": "text", "text": "Two."}]
    reply = asyncio.run(b.chat([{"role": "user", "content": parts}], system="Be kind.", **WHO))
    assert fake.requests[0].content == httpx.Request("POST", "http://x", json={
        "model": "model-a",
        "messages": [{"role": "system", "content": "Be kind."},
                     {"role": "user", "content": "One. Two."}],
        "max_tokens": 4096}).content
    assert reply.tool_calls == () and reply.as_message() == {
        "role": "assistant", "content": reply.text}
    (event,) = [e for e in core.tape.after(0) if e.kind == "model.called"]
    for count in ("tools", "tool_calls", "images", "image_bytes"):
        assert event.payload.get(count) is None
    assert json.loads(core.vault.get_text(event.payload["prompt_ref"])) == {
        "system": "Be kind.", "messages": [{"role": "user", "content": "One. Two."}]}
    assert core.vault.get_text(event.payload["completion_ref"]) == reply.text


# --- what each adapter sends ---------------------------------------------------


def test_openai_offers_tools_as_functions_and_carries_a_turn_with_results_and_images():
    body = sent("openai", continued())
    assert body["tools"] == [
        {"type": "function", "function": {"name": "look_up", "description": "Look a place up.",
                                          "parameters": SCHEMA}},
        {"type": "function", "function": {"name": "finish", "description": "Stop working.",
                                          "parameters": {"type": "object", "properties": {}}}},
    ]
    assert "tool_choice" not in body  # "auto" is the default; some servers do not know the field
    url = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}}
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "What is on screen?"}, url]},
        {"role": "assistant", "content": "Looking.", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "look_up", "arguments": json.dumps(ARGS)}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "By the water."},
        # A tool message takes text only, so the tool's image follows in a user message.
        {"role": "user", "content": [
            {"type": "text", "text": "The tool look_up returned this:"}, url,
            {"type": "text", "text": "Go on."}]},
    ]


def test_openai_sends_no_content_for_a_turn_that_only_calls_tools_and_marks_a_failed_tool():
    request = ChatRequest("model-a", [
        ("user", "Go"),
        ("assistant", [{"type": "text", "text": ""},
                       {"type": "tool_call", "id": "call_1", "name": "finish", "arguments": {},
                        "signature": None}]),
        ("user", [{"type": "tool_result", "tool_call_id": "call_1", "name": "finish",
                   "text": "It is locked.", "images": [], "is_error": True}]),
    ], tools=TOOLS)
    messages = sent("openai_compatible", request)["messages"]
    assert messages[1] == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "finish", "arguments": "{}"}}]}
    assert messages[2:] == [
        {"role": "tool", "tool_call_id": "call_1", "content": "Error: It is locked."}]


def test_anthropic_offers_tools_with_an_input_schema_and_puts_results_first_in_the_next_turn():
    body = sent("anthropic", continued("toolu_1", is_error=True))
    assert body["tools"] == [
        {"name": "look_up", "description": "Look a place up.", "input_schema": SCHEMA},
        {"name": "finish", "description": "Stop working.",
         "input_schema": {"type": "object", "properties": {}}},
    ]
    assert "tool_choice" not in body
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": PNG}}
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "What is on screen?"}, image]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "toolu_1", "name": "look_up", "input": ARGS}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
             "content": [{"type": "text", "text": "By the water."}, image]},
            {"type": "text", "text": "Go on."}]},
    ]
    plain = sent("anthropic", continued("toolu_1", images=False))["messages"][2]["content"][0]
    assert plain == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "By the water."}


def test_google_declares_functions_and_answers_a_call_with_a_function_response():
    body = sent("google", continued("fc_1", "google:opaque-signature"))
    assert body["tools"] == [{"functionDeclarations": [
        {"name": "look_up", "description": "Look a place up.", "parametersJsonSchema": SCHEMA},
        {"name": "finish", "description": "Stop working.",
         "parametersJsonSchema": {"type": "object", "properties": {}}},
    ]}]
    assert "toolConfig" not in body
    image = {"inlineData": {"mimeType": "image/png", "data": PNG}}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "What is on screen?"}, image]},
        {"role": "model", "parts": [
            {"text": "Looking."},
            {"functionCall": {"name": "look_up", "args": ARGS, "id": "fc_1"},
             "thoughtSignature": "opaque-signature"}]},
        {"role": "user", "parts": [
            {"functionResponse": {"name": "look_up", "response": {"output": "By the water."},
                                  "id": "fc_1", "parts": [image]}},
            {"text": "Go on."}]},
    ]
    failed = sent("google", continued("call-0-ab12cd34", is_error=True,
                                      images=False))["contents"]
    # An id made up here because the provider sent none is not sent back as theirs.
    assert failed[1]["parts"][1] == {"functionCall": {"name": "look_up", "args": ARGS},
                                     "thoughtSignature": "skip_thought_signature_validator"}
    assert failed[2]["parts"][0] == {
        "functionResponse": {"name": "look_up", "response": {"error": "By the water."}}}


def test_bedrock_puts_tools_in_a_tool_config_and_sends_images_as_raw_bytes():
    fake = Bedrock()
    asyncio.run(bedrock(fake).chat(continued("tooluse_1", is_error=True)))
    sent_args = fake.calls[0]
    assert sent_args["toolConfig"] == {"tools": [
        {"toolSpec": {"name": "look_up", "description": "Look a place up.",
                      "inputSchema": {"json": SCHEMA}}},
        {"toolSpec": {"name": "finish", "description": "Stop working.",
                      "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    ]}
    image = {"image": {"format": "png", "source": {"bytes": base64.b64decode(PNG)}}}
    assert sent_args["messages"] == [
        {"role": "user", "content": [{"text": "What is on screen?"}, image]},
        {"role": "assistant", "content": [
            {"text": "Looking."},
            {"toolUse": {"toolUseId": "tooluse_1", "name": "look_up", "input": ARGS}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "tooluse_1", "status": "error",
                            "content": [{"text": "By the water."}, image]}},
            {"text": "Go on."}]},
    ]
    fake = Bedrock()
    asyncio.run(bedrock(fake).chat(TEXT_ONLY))
    assert "toolConfig" not in fake.calls[0]


@pytest.mark.parametrize(
    ("choice", "openai", "anthropic", "google", "aws"),
    [
        ("required", "required", {"type": "any"},
         {"functionCallingConfig": {"mode": "ANY"}}, {"any": {}}),
        ("none", "none", {"type": "none"}, {"functionCallingConfig": {"mode": "NONE"}}, None),
        ({"name": "finish"}, {"type": "function", "function": {"name": "finish"}},
         {"type": "tool", "name": "finish"},
         {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["finish"]}},
         {"tool": {"name": "finish"}}),
    ],
)
def test_a_tool_choice_is_sent_in_each_providers_own_words(choice, openai, anthropic, google,
                                                           aws):
    request = ChatRequest("model-a", [("user", "Go")], tools=TOOLS, tool_choice=choice)
    assert sent("openai", request)["tool_choice"] == openai
    assert sent("openai_compatible", request)["tool_choice"] == openai
    assert sent("anthropic", request)["tool_choice"] == anthropic
    assert sent("google", request)["toolConfig"] == google
    fake = Bedrock()
    asyncio.run(bedrock(fake).chat(request))
    if aws is None:  # it has no way to say "none", so the tools are simply not offered
        assert "toolConfig" not in fake.calls[0]
    else:
        assert fake.calls[0]["toolConfig"]["toolChoice"] == aws


def test_bedrock_says_it_cannot_be_told_to_call_no_tool_once_tools_have_been_used():
    with pytest.raises(ModelError) as caught:
        asyncio.run(bedrock(Bedrock()).chat(continued("tooluse_1", tool_choice="none")))
    assert caught.value.kind == "no_tools" and caught.value.fallback_ok


# --- what each adapter reads back ----------------------------------------------


@pytest.mark.parametrize("kind", ["openai", "openai_compatible"])
def test_openai_tool_calls_come_back_parsed_with_whatever_text_came_alongside(kind):
    calls = [openai_call(), openai_call("", "finish", "call_2")]
    reply = asyncio.run(adapter(kind, Canned(openai_reply(calls))).chat(ASKING))
    assert reply == RawReply("", 9, 4, (ToolCall("call_1", "look_up", ARGS),
                                        ToolCall("call_2", "finish", {})))
    spoken = asyncio.run(adapter(kind, Canned(openai_reply(calls[:1], "On it."))).chat(ASKING))
    assert spoken.text == "On it." and len(spoken.tool_calls) == 1
    # A server that sends the arguments as an object, not JSON text, is understood too.
    lenient = asyncio.run(adapter(kind, Canned(openai_reply([openai_call(ARGS)]))).chat(ASKING))
    assert lenient.tool_calls[0].arguments == ARGS


def test_anthropic_tool_use_blocks_come_back_as_tool_calls_and_thinking_is_returned_intact():
    content = [
        {"type": "thinking", "thinking": "", "signature": "sealed"},
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "toolu_1", "name": "look_up", "input": ARGS},
        {"type": "tool_use", "id": "toolu_2", "name": "finish", "input": {}},
    ]
    fake = Canned({"content": content, "stop_reason": "tool_use",
                   "usage": {"input_tokens": 9, "output_tokens": 4}})
    reply = asyncio.run(adapter("anthropic", fake).chat(ASKING))
    first, second = reply.tool_calls
    assert reply.text == "Looking."
    assert (first.id, first.name, first.arguments) == ("toolu_1", "look_up", ARGS)
    assert (second.id, second.name, second.arguments, second.signature) == (
        "toolu_2", "finish", {}, None)
    # Sent back, the assistant turn is the provider's own content, thinking block and all.
    request = ChatRequest("model-a", [
        ("user", "Where is it?"),
        ("assistant", [{"type": "text", "text": "Looking."}]
         + [{"type": "tool_call", **call.as_dict()} for call in reply.tool_calls]),
        ("user", [{"type": "tool_result", "tool_call_id": c.id, "name": c.name, "text": "ok",
                   "images": [], "is_error": False} for c in reply.tool_calls]),
    ], tools=TOOLS)
    assert sent("anthropic", request)["messages"][1] == {"role": "assistant", "content": content}


def test_google_function_calls_come_back_as_tool_calls_with_their_signature():
    fake = Canned({"candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
        {"text": "Looking."},
        {"functionCall": {"name": "look_up", "args": ARGS}, "thoughtSignature": "opaque"},
        {"functionCall": {"name": "finish", "id": "fc_2"}},
    ]}}], "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4}})
    reply = asyncio.run(adapter("google", fake).chat(ASKING))
    first, second = reply.tool_calls
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("Looking.", 9, 4)
    # No id was sent, so one is made, and it starts the way a made-up one does.
    assert first.id.startswith("call-0-")
    assert (first.name, first.arguments, first.signature) == ("look_up", ARGS, "google:opaque")
    assert second == ToolCall("fc_2", "finish", {})


def test_bedrock_tool_use_blocks_come_back_as_tool_calls_and_reasoning_is_returned_intact():
    content = [
        {"reasoningContent": {"redactedContent": b"\x00sealed\xff"}},
        {"text": "Looking."},
        {"toolUse": {"toolUseId": "tooluse_1", "name": "look_up", "input": ARGS}},
    ]
    fake = Bedrock(content)
    reply = asyncio.run(bedrock(fake).chat(ASKING))
    (call,) = reply.tool_calls
    assert (reply.text, call.id, call.name, call.arguments) == (
        "Looking.", "tooluse_1", "look_up", ARGS)
    request = ChatRequest("model-a", [
        ("user", "Where is it?"),
        ("assistant", [{"type": "text", "text": "Looking."},
                       {"type": "tool_call", **call.as_dict()}]),
        ("user", [{"type": "tool_result", "tool_call_id": call.id, "name": call.name,
                   "text": "ok", "images": [], "is_error": False}]),
    ], tools=TOOLS)
    asyncio.run(bedrock(fake).chat(request))
    assert fake.calls[1]["messages"][1] == {"role": "assistant", "content": content}


def test_what_one_provider_wants_passed_back_is_never_sent_to_another():
    made_elsewhere = continued("call_1", "anthropic:" + json.dumps([{"type": "thinking"}]))
    # Google gets the documented placeholder, never another provider's sealed content.
    assert sent("google", made_elsewhere)["contents"][1]["parts"][1]["thoughtSignature"] == (
        "skip_thought_signature_validator")
    assert "anthropic:" not in json.dumps(sent("openai", made_elsewhere))
    from_google = sent("anthropic", continued("call_1", "google:opaque"))
    assert from_google["messages"][1]["content"][1] == {
        "type": "tool_use", "id": "call_1", "name": "look_up", "input": ARGS}


@pytest.mark.parametrize("arguments", ['{"place": ', "[1, 2]", '"just text"', 7])
def test_tool_arguments_that_are_not_a_json_object_raise_instead_of_passing_through(arguments):
    fake = Canned(openai_reply([openai_call(arguments)]))
    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter("openai", fake).chat(ASKING))
    assert caught.value.kind == "bad_tool_call" and caught.value.fallback_ok
    assert "could not be read" in caught.value.public


def test_a_malformed_tool_call_is_reported_the_same_way_by_every_provider():
    replies = {
        "anthropic": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "look_up",
                                   "input": "the harbour"}], "stop_reason": "tool_use"},
        "google": {"candidates": [{"finishReason": "MALFORMED_FUNCTION_CALL",
                                   "content": {"parts": []}}]},
        "openai": openai_reply([{"id": "call_1", "type": "function"}]),
    }
    for kind, reply in replies.items():
        with pytest.raises(ModelError) as caught:
            asyncio.run(adapter(kind, Canned(reply)).chat(ASKING))
        assert caught.value.kind == "bad_tool_call", kind
    for fake in (Bedrock([{"toolUse": {"toolUseId": "t", "name": "look_up", "input": [1]}}]),
                 Bedrock([], stop="malformed_tool_use")):
        with pytest.raises(ModelError) as caught:
            asyncio.run(bedrock(fake).chat(ASKING))
        assert caught.value.kind == "bad_tool_call"


def test_a_tool_call_cut_off_by_the_answer_limit_is_truncated_not_half_read():
    fake = Canned(openai_reply([openai_call('{"place": "the har')], finish="length"))
    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter("openai", fake).chat(ASKING))
    assert caught.value.kind == "truncated"


# --- through the Brain ---------------------------------------------------------


@pytest.fixture
def setup(core):
    """A local provider as the default role's first choice, a hosted one as its fallback."""

    def make(home, cloud=None):
        cloud = cloud or Canned(openai_reply([openai_call()]))
        b = wire(core, {HOME: home, CLOUD: cloud})
        add(b, "home", host=f"{HOME}:11434")
        add(b, "cloud", host=CLOUD, key="sk-test-cloud-key-000000")
        b.set_role("default", Target("home", "model-a"), [Target("cloud", "model-b")],
                   actor="owner")
        return b, home, cloud

    return make


def receipts(core, kind="model.called"):
    return [e.payload for e in core.tape.after(0) if e.kind == kind]


def test_a_turn_can_be_continued_with_the_tool_result_and_a_screenshot(setup, core):
    class Steps(Canned):
        def __call__(self, request):
            self.reply = (openai_reply([openai_call()], "Looking.") if not self.bodies
                          else openai_reply(content="It is by the water.", finish="stop"))
            return super().__call__(request)

    b, home, _ = setup(Steps())
    conversation = [{"role": "user", "content": [
        {"type": "text", "text": "What is on screen?"}, PICTURE]}]
    first = asyncio.run(b.chat(conversation, tools=TOOLS, **WHO))
    assert first.text == "Looking."
    assert first.tool_calls == (ToolCall("call_1", "look_up", ARGS),)
    assert first.as_dict()["tool_calls"][0]["arguments"] == ARGS

    conversation += [first.as_message(), {"role": "user", "content": [
        {"type": "tool_result", "tool_call_id": "call_1",
         "content": [{"type": "text", "text": "By the water."},
                     {"type": "image", "media_type": "image/jpeg", "data": JPEG}]}]}]
    second = asyncio.run(b.chat(conversation, tools=TOOLS, **WHO))
    assert (second.text, second.tool_calls) == ("It is by the water.", ())
    assert [m["role"] for m in home.bodies[1]["messages"]] == [
        "user", "assistant", "tool", "user"]
    assert home.bodies[1]["messages"][2] == {
        "role": "tool", "tool_call_id": "call_1", "content": "By the water."}

    one, two = receipts(core)
    size = len(base64.b64decode(PNG))
    assert (one["tools"], one["tool_calls"], one["images"], one["image_bytes"]) == (2, 1, 1, size)
    assert (two["tools"], two["tool_calls"], two["images"], two["image_bytes"]) == (
        2, 0, 2, size + len(base64.b64decode(JPEG)))
    # The record keeps the words and the calls, and never a picture.
    everything = json.dumps([e.model_dump() for e in core.tape.after(0)])
    kept = "".join(p.read_text(errors="replace")
                   for p in core.config.vault_dir.rglob("*") if p.is_file())
    for picture in (PNG, JPEG):
        assert picture not in everything and picture not in kept
    assert json.loads(core.vault.get_text(one["completion_ref"])) == {
        "text": "Looking.", "tool_calls": [{"id": "call_1", "name": "look_up",
                                            "arguments": ARGS}]}
    prompt = json.loads(core.vault.get_text(two["prompt_ref"]))
    assert prompt["tools"] == list(TOOLS)
    assert prompt["messages"][0]["content"][1] == {
        "type": "image", "media_type": "image/png", "bytes": size}
    assert prompt["messages"][2]["content"][0]["text"] == "By the water."


def test_the_sync_wrapper_returns_tool_calls_too(setup):
    b, _, _ = setup(Canned(openai_reply([openai_call()])))
    reply = b.chat_sync([{"role": "user", "content": "Go"}], tools=TOOLS,
                        tool_choice={"name": "look_up"}, **WHO)
    assert reply.tool_calls == (ToolCall("call_1", "look_up", ARGS),)


def test_a_local_server_that_rejects_a_forced_tool_choice_gives_way_to_the_next_model(
        setup, core):
    refusal = Canned({"error": {"message": '"auto" tool choice requires --enable-auto-tool-choice '
                                           "and --tool-call-parser to be set"}}, status=400)
    b, _, cloud = setup(refusal)
    reply = asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS,
                               tool_choice="required", **WHO))
    assert (reply.provider_id, reply.fallback_from) == ("cloud", "home/model-a")
    assert "may not support tool calling" in reply.fallback_reason
    assert cloud.bodies[0]["tool_choice"] == "required"
    (failed,) = receipts(core, "model.failed")
    assert (failed["reason"], failed["status"], failed["fallback_ok"]) == ("no_tools", 400, True)
    assert "--tool-call-parser" not in failed["message"]  # the provider's words stay off the Tape


def test_with_nowhere_else_to_go_the_error_says_what_to_do_about_tools(setup):
    refusal = Canned({"error": {"message": "this model does not support tools"}}, status=400)
    b, _, cloud = setup(refusal)
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS, local_only=True,
                           **WHO))
    error = caught.value
    assert error.kind == "no_tools" and not cloud.requests
    assert "Choose a model that does" in error.message and 'tool_choice "auto"' in error.message


def test_a_server_that_ignores_a_forced_tool_choice_is_treated_as_unable_to_use_tools(setup):
    ignores = Canned(openai_reply(content="I would rather chat.", finish="stop"))
    b, _, _ = setup(ignores)
    reply = asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS,
                               tool_choice="required", **WHO))
    assert reply.provider_id == "cloud" and reply.tool_calls
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS,
                           tool_choice={"name": "finish"}, no_fallback=True, **WHO))
    assert caught.value.kind == "no_tools"
    # Left to choose, a model that answers in words has done nothing wrong.
    free = asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS, **WHO))
    assert (free.provider_id, free.text, free.tool_calls) == ("home", "I would rather chat.", ())


def test_a_model_that_cannot_see_gives_way_to_one_that_can_and_says_so(setup, core):
    blind = Canned({"error": {"message": "This model does not support image input."}},
                   status=400)
    b, _, cloud = setup(blind, Canned(openai_reply(content="A harbour.", finish="stop")))
    look = [{"role": "user", "content": [{"type": "text", "text": "What is this?"}, PICTURE]}]
    reply = asyncio.run(b.chat(look, role="vision", **WHO))
    assert (reply.text, reply.provider_id) == ("A harbour.", "cloud")
    assert "may not be able to see" in reply.fallback_reason
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat(look, local_only=True, **WHO))
    assert caught.value.kind == "no_images" and len(cloud.requests) == 1
    assert "Choose a model that can read images" in caught.value.message


def test_a_rejection_that_is_not_about_tools_or_images_is_still_final(setup):
    b, _, cloud = setup(Canned({"error": {"message": "The prompt is too long."}}, status=400))
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat([{"role": "user", "content": [PICTURE]}], tools=TOOLS, **WHO))
    assert caught.value.kind == "bad_request" and not cloud.requests


def test_a_call_to_a_tool_that_was_not_offered_is_refused(setup):
    b, _, _ = setup(Canned(openai_reply([openai_call(name="delete_everything")])))
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS, no_fallback=True,
                           **WHO))
    assert caught.value.kind == "bad_tool_call"
    assert "not offered" in caught.value.message and "not offered" not in caught.value.public


def test_tools_reach_bedrock_through_the_brain(core):
    b = brain(core)
    b.bedrock_clients = Bedrock(
        [{"toolUse": {"toolUseId": "tooluse_1", "name": "finish", "input": {}}}])
    b.save_provider({"id": "aws", "type": "bedrock", "label": "Cloud", "region": "us-east-1"},
                    actor="owner")
    reply = asyncio.run(b.chat([{"role": "user", "content": [PICTURE]}], provider_id="aws",
                               model="model-a", tools=TOOLS, tool_choice="required", **WHO))
    (call,) = reply.tool_calls
    assert (call.id, call.name, call.arguments) == ("tooluse_1", "finish", {})
    assert b.bedrock_clients.calls[0]["toolConfig"]["toolChoice"] == {"any": {}}


def test_images_can_be_streamed(setup):
    b, home, _ = setup(FakeProvider("openai_compatible"))

    async def gather(stream):
        return [item async for item in stream]

    look = [{"role": "user", "content": [
        {"type": "image", "media_type": "image/webp", "data": WEBP}]}]
    *pieces, reply = asyncio.run(gather(b.stream(look, **WHO)))
    assert "".join(pieces) == reply.text
    assert home.bodies[0]["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{WEBP}"}}]


def image(**changes) -> list[dict]:
    return [{"role": "user", "content": [PICTURE | changes]}]


@pytest.mark.parametrize(
    ("messages", "message"),
    [
        (image(media_type="image/gif"), "media_type of image/png, image/jpeg, image/webp"),
        (image(media_type=None), "media_type of"),
        (image(data="not base64!"), "not valid base64"),
        (image(data=""), "base64 encoded"),
        (image(data=5), "base64 encoded"),
        (image(media_type="image/jpeg"), "labelled image/jpeg but its data is not"),
        (image(data=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000).decode()),
         "larger than the 1000 bytes allowed"),
        ([{"role": "user", "content": "Hi"}, {"role": "assistant", "content": [PICTURE]}],
         "belong in user messages"),
        ([{"role": "user", "content": [{"type": "audio", "data": "x"}]}],
         "type text, image or tool_result"),
        ([{"role": "user", "content": [PICTURE] * 101}], "at most 100 images"),
    ],
)
def test_an_image_that_cannot_be_sent_is_refused_before_anything_is_sent(setup, core, messages,
                                                                         message):
    b, home, _ = setup(FakeProvider("openai_compatible"))
    core.settings.set("models.max_image_bytes", 1000, actor="owner")
    with pytest.raises(ModelError, match=message) as caught:
        asyncio.run(b.chat(messages, **WHO))
    assert caught.value.kind == "bad_request" and not home.requests
    assert receipts(core) == [] and receipts(core, "model.failed") == []


def test_the_image_size_limit_is_a_setting_with_a_default(setup, core):
    b, home, _ = setup(FakeProvider("openai_compatible"))
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100_000).decode()
    asyncio.run(b.chat(image(data=big), **WHO))
    assert core.settings.get("models.max_image_bytes") == 3_500_000 and len(home.requests) == 1


CALL = {"id": "call_1", "name": "look_up", "arguments": ARGS}
ASKED = [{"role": "user", "content": "Go"},
         {"role": "assistant", "content": None, "tool_calls": [CALL]}]
RESULT = {"type": "tool_result", "tool_call_id": "call_1", "content": "ok"}
GO = [{"role": "user", "content": "Go"}]


@pytest.mark.parametrize(
    ("messages", "options", "message"),
    [
        (GO, {"tools": "look_up"}, "must be a list"),
        (GO, {"tools": [{"name": "look up", "description": "x", "parameters": SCHEMA}]},
         "name must be"),
        (GO, {"tools": [TOOLS[0], TOOLS[0]]}, "both called 'look_up'"),
        (GO, {"tools": [{"name": "a", "description": " ", "parameters": SCHEMA}]},
         "needs a description"),
        (GO, {"tools": [{"name": "a", "description": "x", "parameters": {"type": "string"}}]},
         'type is "object"'),
        (GO, {"tools": [{"name": "a", "description": "x",
                         "parameters": {"type": "object", "default": {1, 2}}}]}, "plain JSON"),
        (GO, {"tools": TOOLS, "tool_choice": "always"}, "tool_choice must be"),
        (GO, {"tools": TOOLS, "tool_choice": {"name": "missing"}}, "not in `tools`"),
        (GO, {"tool_choice": "required"}, "without any tools"),
        (GO, {"tools": TOOLS, "json_schema": {"type": "object"}}, "not both"),
        (ASKED + [{"role": "user", "content": [RESULT]}], {}, "offered the tools again"),
        (ASKED, {"tools": TOOLS}, "tool calls that have no results"),
        (ASKED + GO, {"tools": TOOLS}, "tool_result part for every one"),
        (ASKED + [{"role": "user", "content": [RESULT | {"tool_call_id": "call_9"}]}],
         {"tools": TOOLS}, "does not answer a tool call"),
        (ASKED + [{"role": "user", "content": [RESULT, RESULT]}], {"tools": TOOLS},
         "does not answer a tool call"),
        (ASKED + [{"role": "user", "content": [{"type": "text", "text": "Here:"}, RESULT]}],
         {"tools": TOOLS}, "must come first"),
        (ASKED + [{"role": "user", "content": [RESULT | {"content": [{"type": "audio"}]}]}],
         {"tools": TOOLS}, "only text and image parts"),
        ([{"role": "user", "content": [RESULT]}], {"tools": TOOLS},
         "does not answer a tool call"),
        ([{"role": "user", "content": "Go", "tool_calls": [CALL]}], {"tools": TOOLS},
         "Only an assistant message"),
        (GO + [{"role": "assistant", "content": "", "tool_calls": [CALL | {"arguments": "{}"}]}],
         {"tools": TOOLS}, "arguments \\(a JSON object\\)"),
        (GO + [{"role": "assistant", "content": "", "tool_calls": [CALL, CALL]}],
         {"tools": TOOLS}, "same id"),
    ],
)
def test_a_tool_request_that_makes_no_sense_is_refused_before_anything_is_sent(
        setup, core, messages, options, message):
    b, home, _ = setup(FakeProvider("openai_compatible"))
    with pytest.raises(ModelError, match=message) as caught:
        asyncio.run(b.chat(messages, **(WHO | options)))
    assert caught.value.kind == "bad_request" and not home.requests
    assert receipts(core) == [] and receipts(core, "model.failed") == []


def test_a_tool_result_is_matched_to_its_call_by_id_so_google_gets_the_function_name(core):
    fake = Canned({"candidates": [{"finishReason": "STOP",
                                   "content": {"parts": [{"text": "Done."}]}}]})
    b = wire(core, {"google.example.com": fake})
    add(b, "g", "google", key="test-google-key-000000")
    conversation = ASKED + [{"role": "user", "content": [RESULT | {"is_error": True}]}]
    reply = asyncio.run(b.chat(conversation, provider_id="g", model="model-a", tools=TOOLS,
                               tool_choice="none", keep_text=False, **WHO))
    assert reply.text == "Done."
    assert fake.bodies[0]["contents"][2]["parts"] == [{"functionResponse": {
        "name": "look_up", "response": {"error": "ok"}, "id": "call_1"}}]
    assert fake.bodies[0]["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}
    (receipt,) = receipts(core)
    assert receipt["prompt_ref"] is None and (receipt["tools"], receipt["tool_calls"]) == (2, 0)


# --- review fixes: reading rejections, ids, signatures, arguments, image totals ---


def _capable_tool_reply():
    return Canned(openai_reply([openai_call()]))


@pytest.mark.parametrize("detail", [
    "registry.ollama.ai/library/gemma:2b does not support tools",
    "This model does not support function calling.",
    "this model doesn't support tool use.",
    "This model doesn't support the toolConfig.toolChoice.any field. Remove it and try again.",
    "tools is not supported by this model",
    "tool_choice 'required' is not supported for this model",
    "Function calling is not enabled for models/some-model",
    "Tool use with function calling is unsupported",
    '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set',
    "tools param requires --jinja flag",
    "Thinking may not be enabled when tool_choice forces tool use.",
])
def test_a_rejection_that_says_the_model_cannot_use_tools_gives_way_to_the_next_model(
        setup, detail):
    b, _, cloud = setup(Canned({"error": {"message": detail}}, status=400), _capable_tool_reply())
    reply = asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS, **WHO))
    assert (reply.provider_id, len(cloud.requests)) == ("cloud", 1)


@pytest.mark.parametrize("detail", [
    "This model's maximum context length is 8192 tokens. However, you requested 9500 tokens "
    "(8500 in the messages, 1000 in the functions). Please reduce the length of the messages "
    "or functions.",
    "Invalid 'tools[0].function.parameters': schema must be a JSON Schema of 'type: \"object\"'.",
    "messages.2.content.0.tool_result.tool_use_id: no tool_use block with this id",
    "Request too large for this model: 12 tools and 40 images exceed the prompt limit.",
    "image exceeds 5 MB maximum: 7340032 bytes > 5242880 bytes",
    "You uploaded an unsupported image. Please make sure your image is valid.",
    "messages.0.content.1.image.source.base64: invalid base64 data",
    "Image format is not supported: use png, jpeg or webp.",
    "The image does not support more than 8000 pixels on its longest side.",
    "Too many images in request: 120 > 100",
    "Your key is not allowed to use this model.",
    "Rate limit reached: too many tool calls per minute.",
])
def test_an_unrelated_rejection_that_mentions_tools_or_images_is_still_final(setup, detail):
    for forced in ("auto", "required", {"name": "finish"}):
        b, _, cloud = setup(Canned({"error": {"message": detail}}, status=400))
        with pytest.raises(ModelError) as caught:
            asyncio.run(b.chat([{"role": "user", "content": [PICTURE]}], tools=TOOLS,
                               tool_choice=forced, **WHO))
        assert caught.value.kind == "bad_request" and not cloud.requests, (detail, forced)


@pytest.mark.parametrize("detail", [
    "This model does not support image input.",
    "image input is not supported - hint: provide the mmproj file",
    "Image input modality is not enabled for models/some-model",
    "At most 0 image(s) may be provided in one request.",
    "this model is missing data required for image input",
    "Invalid content type. image_url is only supported by certain models.",
    "The model does not support vision.",
])
def test_a_rejection_that_says_the_model_cannot_see_gives_way_to_one_that_can(setup, detail):
    b, _, cloud = setup(Canned({"error": {"message": detail}}, status=400),
                        Canned(openai_reply(content="A harbour.", finish="stop")))
    reply = asyncio.run(b.chat([{"role": "user", "content": [PICTURE]}], **WHO))
    assert (reply.provider_id, len(cloud.requests)) == ("cloud", 1)


def test_a_forced_tool_choice_alone_does_not_make_a_rejection_about_tools(setup):
    b, _, cloud = setup(Canned({"error": {"message": "The prompt is too long."}}, status=400))
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat([{"role": "user", "content": "Go"}], tools=TOOLS,
                           tool_choice="required", **WHO))
    assert caught.value.kind == "bad_request" and not cloud.requests


def test_a_tool_call_id_made_up_for_a_server_that_sent_none_is_new_every_time():
    ids = set()
    for _ in range(20):
        reply = asyncio.run(adapter("openai", Canned(openai_reply(
            [openai_call(call_id=None)]))).chat(ASKING))
        ids.add(reply.tool_calls[0].id)
    assert len(ids) == 20 and all(i.startswith("call-0-") for i in ids)


FOREIGN = "functions.look_up:0"


def test_anthropic_gets_a_safe_id_for_a_call_made_elsewhere_and_the_same_one_for_its_result():
    messages = sent("anthropic", continued(FOREIGN))["messages"]
    call_id = messages[1]["content"][1]["id"]
    assert call_id != FOREIGN and call_id.startswith("toolu_")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", call_id)
    assert messages[2]["content"][0]["tool_use_id"] == call_id
    assert sent("anthropic", continued("toolu_01ok"))["messages"][1]["content"][1]["id"] == (
        "toolu_01ok")


def test_bedrock_gets_a_safe_id_for_a_call_made_elsewhere_and_the_same_one_for_its_result():
    fake = Bedrock()
    asyncio.run(bedrock(fake).chat(continued(FOREIGN)))
    messages = fake.calls[0]["messages"]
    call_id = messages[1]["content"][1]["toolUse"]["toolUseId"]
    assert call_id != FOREIGN and call_id.startswith("tooluse_")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", call_id)
    assert messages[2]["content"][0]["toolResult"]["toolUseId"] == call_id


def test_google_is_given_the_documented_placeholder_only_on_the_first_unsigned_call_of_a_step():
    def call(name, call_id, signature=None):
        return {"type": "tool_call", "id": call_id, "name": name, "arguments": {},
                "signature": signature}

    request = ChatRequest("model-a", [
        ("user", "Go"),
        ("assistant", [call("look_up", "a"), call("finish", "b")]),
        ("user", [{"type": "tool_result", "tool_call_id": c, "name": n, "text": "ok",
                   "images": [], "is_error": False} for c, n in (("a", "look_up"),
                                                                 ("b", "finish"))]),
        ("assistant", [call("look_up", "c", "google:real"), call("finish", "d")]),
        ("user", [{"type": "tool_result", "tool_call_id": c, "name": n, "text": "ok",
                   "images": [], "is_error": False} for c, n in (("c", "look_up"),
                                                                 ("d", "finish"))]),
    ], tools=TOOLS)
    contents = sent("google", request)["contents"]
    first, second = (contents[1]["parts"], contents[3]["parts"])
    assert first[0]["thoughtSignature"] == "skip_thought_signature_validator"
    assert "thoughtSignature" not in first[1]
    assert second[0]["thoughtSignature"] == "real" and "thoughtSignature" not in second[1]


def test_an_empty_bedrock_tool_result_says_there_was_no_output():
    fake = Bedrock()
    request = continued("tooluse_1", images=False)
    request.messages[2][1][0]["text"] = ""
    asyncio.run(bedrock(fake).chat(request))
    result = fake.calls[0]["messages"][2]["content"][0]["toolResult"]
    assert result["content"] == [{"text": "(no output)"}]
    with_image = continued("tooluse_1")
    with_image.messages[2][1][0]["text"] = ""
    asyncio.run(bedrock(fake).chat(with_image))
    assert [list(c) for c in fake.calls[1]["messages"][2]["content"][0]["toolResult"]
            ["content"]] == [["image"]]


def test_bedrock_returns_what_a_model_put_in_tool_input_exactly_as_it_was_written():
    odd = {"__bytes__": "AAAA", "also": {"__bytes__": "AAAA"}}
    lone = {"__bytes__": "AAAA"}
    content = [
        {"reasoningContent": {"redactedContent": b"\x00sealed\xff"}},
        {"toolUse": {"toolUseId": "tooluse_1", "name": "look_up", "input": lone}},
        {"toolUse": {"toolUseId": "tooluse_2", "name": "finish", "input": odd}},
    ]
    fake = Bedrock(content)
    reply = asyncio.run(bedrock(fake).chat(ASKING))
    assert [c.arguments for c in reply.tool_calls] == [lone, odd]
    request = ChatRequest("model-a", [
        ("user", "Where is it?"),
        ("assistant", [{"type": "tool_call", **c.as_dict()} for c in reply.tool_calls]),
        ("user", [{"type": "tool_result", "tool_call_id": c.id, "name": c.name, "text": "ok",
                   "images": [], "is_error": False} for c in reply.tool_calls]),
    ], tools=TOOLS)
    asyncio.run(bedrock(fake).chat(request))
    replayed = fake.calls[1]["messages"][1]["content"]
    assert replayed == content  # bytes stay bytes in the reasoning, dicts stay dicts in input
    assert isinstance(replayed[0]["reasoningContent"]["redactedContent"], bytes)
    assert replayed[1]["toolUse"]["input"] == lone


def _sealed_anthropic(provider_id):
    content = [{"type": "thinking", "thinking": "", "signature": "sealed"},
               {"type": "tool_use", "id": "toolu_1", "name": "look_up", "input": ARGS}]
    provider = Provider(id=provider_id, type="anthropic", label="Fake",
                        base_url=BASE_URLS["anthropic"])
    made = ADAPTERS["anthropic"](provider, KEY, Timeouts(1, 5), httpx.MockTransport(
        Canned({"content": content, "stop_reason": "tool_use"})))
    return content, asyncio.run(made.chat(ASKING)).tool_calls[0]


def _later(call):
    return ChatRequest("model-a", [
        ("user", "Where is it?"),
        ("assistant", [{"type": "tool_call", **call.as_dict()}]),
        ("user", [{"type": "tool_result", "tool_call_id": call.id, "name": call.name,
                   "text": "ok", "images": [], "is_error": False}]),
    ], tools=TOOLS)


def test_anthropic_sealed_thinking_is_replayed_only_to_the_provider_that_wrote_it():
    content, call = _sealed_anthropic("one")
    sends = {}
    for provider_id in ("one", "two"):
        fake = Canned({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"})
        provider = Provider(id=provider_id, type="anthropic", label="Fake",
                            base_url=BASE_URLS["anthropic"])
        asyncio.run(ADAPTERS["anthropic"](provider, KEY, Timeouts(1, 5),
                                          httpx.MockTransport(fake)).chat(_later(call)))
        sends[provider_id] = fake.bodies[0]["messages"][1]["content"]
    assert sends["one"] == content
    assert sends["two"] == [{"type": "tool_use", "id": "toolu_1", "name": "look_up",
                             "input": ARGS}]


def test_bedrock_sealed_reasoning_is_replayed_only_to_the_provider_that_wrote_it():
    content = [{"reasoningContent": {"redactedContent": b"\x00sealed"}},
               {"toolUse": {"toolUseId": "tooluse_1", "name": "look_up", "input": ARGS}}]

    def adapter_for(provider_id, fake):
        provider = Provider(id=provider_id, type="bedrock", label="Cloud", region="us-east-1")
        return BedrockAdapter(provider, None, Timeouts(1, 5), client_factory=fake)

    (call,) = asyncio.run(adapter_for("one", Bedrock(content)).chat(ASKING)).tool_calls
    sends = {}
    for provider_id in ("one", "two"):
        fake = Bedrock()
        asyncio.run(adapter_for(provider_id, fake).chat(_later(call)))
        sends[provider_id] = fake.calls[0]["messages"][1]["content"]
    assert sends["one"] == content
    assert sends["two"] == [{"toolUse": {"toolUseId": "tooluse_1", "name": "look_up",
                                         "input": ARGS}}]


@pytest.mark.parametrize("arguments", [{"place": float("nan")}, {"place": float("inf")},
                                       {"place": {1, 2}}, {"place": b"bytes"},
                                       {(1, 2): "tuple key"}])
def test_tool_call_arguments_in_the_history_must_be_plain_json(setup, arguments):
    b, home, _ = setup(FakeProvider("openai_compatible"))
    history = [{"role": "user", "content": "Go"},
               {"role": "assistant", "content": None,
                "tool_calls": [CALL | {"arguments": arguments}]},
               {"role": "user", "content": [RESULT]}]
    with pytest.raises(ModelError, match="plain JSON") as caught:
        asyncio.run(b.chat(history, tools=TOOLS, **WHO))
    assert caught.value.kind == "bad_request" and not home.requests


def test_all_the_images_in_one_request_together_have_a_limit_that_is_a_setting(setup, core):
    assert core.settings.get("models.max_total_image_bytes") == 20_000_000
    core.settings.set("models.max_total_image_bytes", 1000, actor="owner")
    b, home, _ = setup(FakeProvider("openai_compatible"))

    def picture(size):
        return PICTURE | {"data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * size).decode()}

    asyncio.run(b.chat([{"role": "user", "content": [picture(400), picture(400)]}], **WHO))
    assert len(home.requests) == 1
    with pytest.raises(ModelError, match="add up to more than the 1000 bytes") as caught:
        asyncio.run(b.chat([{"role": "user", "content": [picture(600), picture(600)]}], **WHO))
    assert caught.value.kind == "bad_request" and len(home.requests) == 1
    assert "Settings, Models" in caught.value.message
