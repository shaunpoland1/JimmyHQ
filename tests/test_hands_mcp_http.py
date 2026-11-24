"""The MCP adapter over streamable HTTP. The server is a transport that answers from
memory: no address is contacted."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from jimmyhq.hands import mcp
from test_hands_mcp_fakes import (
    ADDRESS,
    META,
    TOKEN,
    FakeHttp,
    call,
    fast,
    http_adapter,
    names,
    run,
    tool,
)


@pytest.fixture(autouse=True)
def _stopped(monkeypatch):
    yield from fast(monkeypatch)


def test_a_modern_server_is_asked_with_the_version_method_and_name_in_the_headers():
    fake = FakeHttp("modern")
    adapter = http_adapter(fake)
    assert names(adapter) == ["echo", "save", "wipe"]
    out = call(adapter, "echo", {"words": "hi"})
    assert out.ok and json.loads(out.data) == {"arguments": {"words": "hi"}}
    assert [b["method"] for _, b in fake.requests] == [
        "server/discover", "tools/list", "tools/call"]
    for headers, body in fake.requests:
        assert headers["mcp-protocol-version"] == "2026-07-28"
        assert headers["mcp-method"] == body["method"]
        assert "text/event-stream" in headers["accept"] and "application/json" in headers["accept"]
        assert body["params"]["_meta"][META + "clientCapabilities"] == {}
        assert "mcp-session-id" not in headers
    assert fake.requests[-1][0]["mcp-name"] == "echo"
    assert "initialize" not in [b["method"] for _, b in fake.requests]


def test_an_older_server_gives_a_session_and_an_event_stream_reply_is_read():
    fake = FakeHttp("legacy")
    fake.stream = True
    adapter = http_adapter(fake)
    out = call(adapter, "echo", {"words": "hi"})
    assert out.ok and json.loads(out.data) == {"arguments": {"words": "hi"}}
    sent = [(b.get("method"), h.get("mcp-session-id"), h.get("mcp-protocol-version"))
            for h, b in fake.requests]
    assert sent[:4] == [
        ("server/discover", None, "2026-07-28"),  # refused: so it is an older server
        ("initialize", None, None),
        ("notifications/initialized", "session-1", "2025-06-18"),
        ("tools/call", "session-1", "2025-06-18")]
    assert "_meta" not in fake.bodies("tools/call")[0][1]["params"]
    # On the stream the server asked this client for a model's answer: it was told no.
    assert adapter.session.transport.refused == 1
    refusal = fake.requests[-1][1]
    assert refusal["id"] == "ask-1" and refusal["error"]["code"] == -32601


def test_an_older_server_that_forgot_the_session_is_given_a_new_one_once():
    fake = FakeHttp("legacy")
    adapter = http_adapter(fake)
    assert call(adapter, "echo", {"words": "a"}).ok
    fake.forget_once = True
    assert call(adapter, "echo", {"words": "b"}).ok
    assert fake.sessions == 2 and len(fake.bodies("initialize")) == 2
    assert fake.bodies("tools/call")[-1][0]["mcp-session-id"] == "session-2"


def test_saved_headers_go_with_every_request_and_never_into_what_comes_back():
    fake = FakeHttp("modern")
    fake.reply = {"content": [{"type": "text", "text": f"you sent Bearer {TOKEN}"}]}
    adapter = http_adapter(fake, saved={"header_authorization": f"Bearer {TOKEN}",
                                        "header_x-team": "blue-team-22"},
                           headers=["Authorization", "X-Team"])
    out = call(adapter, "echo")
    assert out.data == "you sent [removed]"
    assert all(h["authorization"] == f"Bearer {TOKEN}" and h["x-team"] == "blue-team-22"
               for h, _ in fake.requests)
    assert TOKEN not in repr(adapter.record) and TOKEN not in repr(vars(adapter.session))


def test_the_providers_key_is_sent_as_a_bearer_token_unless_a_header_says_otherwise():
    fake = FakeHttp("modern")
    assert run(http_adapter(fake, key=TOKEN).check()).ok
    assert fake.requests[0][0]["authorization"] == f"Bearer {TOKEN}"


def test_a_missing_or_unusable_header_says_what_to_save_and_contacts_nobody():
    fake = FakeHttp("modern")
    health = run(http_adapter(fake, headers=["Authorization"]).check())
    assert not health.ok and "header_authorization" in health.error
    for bad in (["Mcp-Session-Id"], ["Host"], ["Content-Length"], ["Bad Name"]):
        assert not run(http_adapter(fake, headers=bad, saved={}).check()).ok
    health = run(http_adapter(fake, headers=["X-Team"],
                              saved={"header_x-team": "two\r\nlines"}).check())
    assert not health.ok and "cannot carry" in health.error
    assert fake.requests == []


def test_a_refused_sign_in_says_what_to_do_and_a_call_then_sends_nothing_to_a_tool():
    fake = FakeHttp("modern")
    fake.status = 401
    adapter = http_adapter(fake)
    health = run(adapter.check())
    assert not health.ok and "refused the sign-in" in health.error and "header" in health.error
    out = call(adapter, "echo")
    assert not out.ok and "Nothing was sent" in out.error
    assert not fake.bodies("tools/call")


def test_a_redirect_is_not_followed():
    fake = FakeHttp("modern")
    fake.status = 302
    health = run(http_adapter(fake).check())
    assert not health.ok and "redirect" in health.error
    assert len(fake.requests) == 1


def test_an_address_off_the_local_network_must_use_https():
    fake = FakeHttp("modern")
    health = run(http_adapter(fake, url="http://mcp.example.com/mcp").check())
    assert not health.ok and "https" in health.error and fake.requests == []
    assert run(http_adapter(fake, url="http://127.0.0.1:8931/mcp").check()).ok


def test_an_answer_larger_than_the_limit_is_stopped_and_the_call_is_lost():
    fake = FakeHttp("modern")
    fake.padding = 50_000
    with pytest.raises(mcp.TooLarge):
        call(http_adapter(fake, max_message_bytes=20_000), "echo")
    fake.stream = True
    with pytest.raises(mcp.TooLarge):
        call(http_adapter(fake, max_message_bytes=20_000), "echo")


def test_a_server_fault_during_a_call_is_lost_but_a_refusal_is_a_clean_failure():
    fake = FakeHttp("modern")
    adapter = http_adapter(fake)
    assert names(adapter)
    fake.status = 503
    with pytest.raises(mcp.Lost):
        call(adapter, "save", {"record": "x"})
    fake.status = 429
    out = call(adapter, "save", {"record": "x"})
    assert not out.ok and "too often" in out.error


def test_a_stream_that_ends_before_the_answer_is_lost():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "tools/call":
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=b": nothing yet\n\n")
        return FakeHttp("modern").handle(request)

    fake = FakeHttp("modern")
    fake.handle = handler  # type: ignore[method-assign]
    with pytest.raises(mcp.Lost):
        call(http_adapter(fake), "save", {"record": "x"})


def test_a_server_that_cannot_be_reached_is_a_clean_failure_with_nothing_sent():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake = FakeHttp("modern")
    fake.handle = handler  # type: ignore[method-assign]
    out = call(http_adapter(fake), "save", {"record": "x"})
    assert not out.ok and "could not be reached" in out.error and "Nothing was sent" in out.error


def test_arguments_a_tool_marks_are_copied_into_headers_and_a_bad_mark_drops_the_tool():
    marked = tool("query", region={"type": "string", "x-mcp-header": "Region"},
                  limit={"type": "integer", "x-mcp-header": "Limit"},
                  text={"type": "string"})
    marked["inputSchema"]["properties"]["where"] = {"type": "object", "properties": {
        "city": {"type": "string", "x-mcp-header": "City"}}}
    bad = [
        tool("number", ratio={"type": "number", "x-mcp-header": "Ratio"}),
        tool("twice", a={"type": "string", "x-mcp-header": "Same"},
             b={"type": "string", "x-mcp-header": "same"}),
        tool("spaced", a={"type": "string", "x-mcp-header": "Not A Token"}),
        tool("in_a_list", a={"type": "array", "items": {"type": "string", "x-mcp-header": "I"}}),
    ]
    fake = FakeHttp("modern", tools=[marked, *bad, tool("plain")])
    adapter = http_adapter(fake)
    assert names(adapter) == ["query", "plain"]
    call(adapter, "query", {"region": "eu-west", "limit": 5, "text": "not copied",
                            "where": {"city": "Zürich"}})
    headers = fake.bodies("tools/call")[0][0]
    assert headers["mcp-param-region"] == "eu-west" and headers["mcp-param-limit"] == "5"
    encoded = base64.b64encode("Zürich".encode()).decode()
    assert headers["mcp-param-city"] == f"=?base64?{encoded}?="
    assert not [h for h in headers if h.startswith("mcp-param-") and "text" in h]


def test_a_value_goes_into_a_header_plainly_or_encoded_and_never_raw():
    assert mcp.header_value("us-west1") == "us-west1"
    assert mcp.header_value(True) == "true" and mcp.header_value(-7) == "-7"
    assert mcp.header_value("line1\nline2") == "=?base64?bGluZTEKbGluZTI=?="
    assert mcp.header_value(" padded ") == "=?base64?IHBhZGRlZCA=?="
    assert mcp.header_value("=?base64?literal?=") == "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?="
    assert mcp.header_value(1.5) is None and mcp.header_value(None) is None
    assert mcp.header_value(2 ** 60) is None


def test_the_address_used_in_these_tests_is_never_resolved():
    assert ADDRESS.startswith("https://mcp.example.com")
