from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jimmyhq.models.adapters import ADAPTERS, Timeouts, mask
from jimmyhq.models.adapters.bedrock import BedrockAdapter
from jimmyhq.models.shapes import ChatRequest, ModelError, Provider, RawReply
from test_models_fakes import (
    BASE_URLS,
    HTTP_TYPES,
    MODELS,
    PIECES,
    TEXT,
    TOKENS_IN,
    TOKENS_OUT,
    FakeBedrock,
    FakeProvider,
)

KEY = "sk-test-0123456789abcdef"
REQUEST = ChatRequest("model-a", [("user", "Hi"), ("assistant", "Yes?"), ("user", "Hello")],
                      system="Be brief.", max_tokens=64)


def adapter(kind: str, fake: FakeProvider, **options):
    provider = Provider(id="p", type=kind, label="Fake", base_url=BASE_URLS[kind],
                        options=options)
    return ADAPTERS[kind](provider, KEY, Timeouts(1, 5), httpx.MockTransport(fake))


async def collect(stream):
    pieces, final = [], None
    async for item in stream:
        if isinstance(item, RawReply):
            final = item
        else:
            pieces.append(item)
    return pieces, final


@pytest.mark.parametrize("kind", HTTP_TYPES)
def test_a_chat_returns_the_text_and_the_token_counts(kind):
    fake = FakeProvider(kind)
    reply = asyncio.run(adapter(kind, fake).chat(REQUEST))
    assert reply == RawReply(TEXT, TOKENS_IN, TOKENS_OUT)


@pytest.mark.parametrize("kind", HTTP_TYPES)
def test_a_stream_yields_the_pieces_and_ends_with_the_whole_text_and_usage(kind):
    fake = FakeProvider(kind)
    pieces, final = asyncio.run(collect(adapter(kind, fake).stream(REQUEST)))
    assert pieces == list(PIECES)
    assert final == RawReply(TEXT, TOKENS_IN, TOKENS_OUT)


@pytest.mark.parametrize("kind", HTTP_TYPES)
def test_models_are_discovered_from_the_provider_and_sorted(kind):
    assert asyncio.run(adapter(kind, FakeProvider(kind)).list_models()) == MODELS


@pytest.mark.parametrize("kind", HTTP_TYPES)
@pytest.mark.parametrize(
    ("mode", "reason", "fallback_ok", "status"),
    [
        ("auth", "auth", True, 401),
        ("rate", "rate_limited", True, 429),
        ("timeout", "timeout", True, None),
        ("down", "unreachable", True, None),
        ("garbage", "bad_reply", True, 200),
        ("server", "unavailable", True, 500),
        ("missing", "not_found", True, 404),
        ("redirect", "redirected", True, 307),
        ("rejected", "bad_request", False, 400),
        ("truncated", "truncated", False, None),
    ],
)
def test_every_failure_becomes_one_error_that_says_whether_a_fallback_makes_sense(
    kind, mode, reason, fallback_ok, status
):
    for call in (lambda a: a.chat(REQUEST), lambda a: collect(a.stream(REQUEST))):
        with pytest.raises(ModelError) as caught:
            asyncio.run(call(adapter(kind, FakeProvider(kind, mode))))
        error = caught.value
        assert (error.kind, error.fallback_ok, error.status) == (reason, fallback_ok, status)
        assert error.message.startswith("Fake ") or "Fake" in error.message
        assert KEY not in error.message


def _no_trace_of(text: str, error: ModelError) -> bool:
    return all(text not in str(part) for part in (
        error, repr(error), error.message, error.public, error.detail, error.args))


@pytest.mark.parametrize("kind", HTTP_TYPES)
def test_a_cut_off_answer_hands_back_the_text_that_had_arrived_whole_or_streamed(kind):
    for call in (lambda a: a.chat(REQUEST), lambda a: collect(a.stream(REQUEST))):
        with pytest.raises(ModelError) as caught:
            asyncio.run(call(adapter(kind, FakeProvider(kind, "truncated"))))
        error = caught.value
        assert error.kind == "truncated" and error.partial == TEXT
        assert _no_trace_of(TEXT, error)  # on the error for the caller, in no sentence


def test_bedrock_hands_back_the_text_of_a_cut_off_answer_whole_or_streamed():
    for call in (lambda a: a.chat(REQUEST), lambda a: collect(a.stream(REQUEST))):
        with pytest.raises(ModelError) as caught:
            asyncio.run(call(bedrock(FakeBedrock("truncated"))))
        assert caught.value.kind == "truncated" and caught.value.partial == TEXT
        assert _no_trace_of(TEXT, caught.value)


@pytest.mark.parametrize("kind", HTTP_TYPES)
def test_an_error_that_is_not_a_cut_off_answer_carries_no_partial_text(kind):
    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter(kind, FakeProvider(kind, "rejected")).chat(REQUEST))
    assert caught.value.partial == ""


def test_openai_sends_a_bearer_key_a_system_message_and_the_current_token_field():
    fake = FakeProvider("openai")
    asyncio.run(adapter("openai", fake).chat(REQUEST))
    request, body = fake.requests[0], fake.bodies[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert body["max_completion_tokens"] == 64 and "max_tokens" not in body


def test_a_compatible_server_gets_max_tokens_and_no_key_header_when_none_is_saved():
    fake = FakeProvider("openai_compatible")
    provider = Provider(id="p", type="openai_compatible", label="Fake",
                        base_url=BASE_URLS["openai_compatible"])
    plain = ADAPTERS["openai_compatible"](provider, None, Timeouts(1, 5),
                                          httpx.MockTransport(fake))
    asyncio.run(plain.chat(REQUEST))
    assert fake.bodies[0]["max_tokens"] == 64
    assert "authorization" not in fake.requests[0].headers


def test_anthropic_sends_its_version_header_and_never_a_temperature():
    fake = FakeProvider("anthropic")
    request = ChatRequest("model-a", [("user", "Hi")], system="Be brief.", max_tokens=64,
                          temperature=0.2, effort="high", json_schema={"type": "object"})
    asyncio.run(adapter("anthropic", fake).chat(request))
    sent, body = fake.requests[0], fake.bodies[0]
    assert sent.url.path == "/v1/messages"
    assert sent.headers["x-api-key"] == KEY and sent.headers["anthropic-version"] == "2026-10-21"
    assert "authorization" not in sent.headers
    assert "temperature" not in body and body["system"] == "Be brief."
    assert body["output_config"] == {
        "effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}}


def test_google_sends_the_key_in_a_header_and_never_in_the_address():
    fake = FakeProvider("google")
    request = ChatRequest("model-a", [("user", "Hi"), ("assistant", "Yes?"), ("user", "Go")],
                          system="Be brief.", max_tokens=64, effort="low",
                          json_schema={"type": "object"})
    asyncio.run(adapter("google", fake).chat(request))
    sent, body = fake.requests[0], fake.bodies[0]
    assert sent.url.path == "/v1beta/models/model-a:generateContent"
    assert sent.headers["x-goog-api-key"] == KEY and KEY not in str(sent.url)
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["systemInstruction"] == {"parts": [{"text": "Be brief."}]}
    assert body["generationConfig"] == {
        "maxOutputTokens": 64, "thinkingConfig": {"thinkingLevel": "LOW"},
        "responseMimeType": "application/json", "responseJsonSchema": {"type": "object"}}


def test_a_json_schema_is_sent_natively_only_where_the_provider_type_takes_one():
    schema = {"type": "object"}
    request = ChatRequest("model-a", [("user", "Hi")], json_schema=schema)
    hosted, compatible = FakeProvider("openai"), FakeProvider("openai_compatible")
    asyncio.run(adapter("openai", hosted).chat(request))
    asyncio.run(adapter("openai_compatible", compatible).chat(request))
    assert hosted.bodies[0]["response_format"]["json_schema"]["schema"] == schema
    assert "response_format" not in compatible.bodies[0]
    switched_on = FakeProvider("openai_compatible")
    asyncio.run(adapter("openai_compatible", switched_on, native_json=True).chat(request))
    assert "response_format" in switched_on.bodies[0]


def test_a_redirect_is_not_followed_so_the_key_cannot_reach_another_host():
    fake = FakeProvider("openai", "redirect")
    with pytest.raises(ModelError, match="redirect"):
        asyncio.run(adapter("openai", fake).chat(REQUEST))
    assert len(fake.requests) == 1


def test_an_error_stream_event_from_anthropic_is_reported_as_trouble_at_the_provider():
    def handler(request):
        return httpx.Response(200, text='event: error\ndata: ' + json.dumps(
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
        ) + "\n\n")

    provider = Provider(id="p", type="anthropic", label="Fake",
                        base_url=BASE_URLS["anthropic"])
    a = ADAPTERS["anthropic"](provider, KEY, Timeouts(1, 5), httpx.MockTransport(handler))
    with pytest.raises(ModelError) as caught:
        asyncio.run(collect(a.stream(REQUEST)))
    assert caught.value.kind == "unavailable"


def test_a_key_echoed_back_by_a_provider_is_masked_in_the_error():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"Bad key {KEY} given."}})

    provider = Provider(id="p", type="openai", label="Fake", base_url=BASE_URLS["openai"])
    a = ADAPTERS["openai"](provider, KEY, Timeouts(1, 5), httpx.MockTransport(handler))
    with pytest.raises(ModelError) as caught:
        asyncio.run(a.chat(REQUEST))
    assert KEY not in caught.value.message and "***" in caught.value.message
    assert "Bad key" not in caught.value.public
    assert mask("token sk-abcdefghijkl here") == "token *** here"


def bedrock(fake: FakeBedrock) -> BedrockAdapter:
    provider = Provider(id="aws", type="bedrock", label="Cloud", region="us-east-1",
                        profile="work")
    return BedrockAdapter(provider, None, Timeouts(1, 5), client_factory=fake)


def test_bedrock_chats_streams_and_lists_through_the_converse_api():
    fake = FakeBedrock()
    assert asyncio.run(bedrock(fake).chat(REQUEST)) == RawReply(TEXT, TOKENS_IN, TOKENS_OUT)
    assert fake.asked == ("bedrock-runtime", "us-east-1", "work")
    assert fake.calls[0] == {
        "modelId": "model-a",
        "messages": [{"role": "user", "content": [{"text": "Hi"}]},
                     {"role": "assistant", "content": [{"text": "Yes?"}]},
                     {"role": "user", "content": [{"text": "Hello"}]}],
        "inferenceConfig": {"maxTokens": 64},
        "system": [{"text": "Be brief."}],
    }
    pieces, final = asyncio.run(collect(bedrock(fake).stream(REQUEST)))
    assert pieces == list(PIECES) and final == RawReply(TEXT, TOKENS_IN, TOKENS_OUT)
    assert asyncio.run(bedrock(fake).list_models()) == MODELS


@pytest.mark.parametrize(("mode", "reason"), [("throttled", "rate_limited"), ("denied", "auth"),
                                              ("truncated", "truncated")])
def test_bedrock_failures_are_normalised_like_every_other_provider(mode, reason):
    with pytest.raises(ModelError) as caught:
        asyncio.run(bedrock(FakeBedrock(mode)).chat(REQUEST))
    assert caught.value.kind == reason


def test_bedrock_without_the_aws_library_says_how_to_install_it(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_boto(name, *args, **kwargs):
        if name.startswith(("boto3", "botocore")):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_boto)
    provider = Provider(id="aws", type="bedrock", label="Cloud", region="us-east-1")
    with pytest.raises(ModelError, match="uv sync --extra bedrock") as caught:
        asyncio.run(BedrockAdapter(provider, None, Timeouts(1, 5)).chat(REQUEST))
    assert caught.value.kind == "missing_extra"
