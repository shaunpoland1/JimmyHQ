"""Chat over HTTP: who may do what, the reply stream, and what odd input gets back."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from jimmyhq import chat
from jimmyhq.chat import replies
from jimmyhq.web.app import create_app
from test_chat_fakes import SlowProvider, models, of_kind
from test_models_fakes import PIECES, TEXT, FakeProvider

PASSWORD = "correct horse battery"
MISSING = "01JABCDEFGHJKMNPQRSTVWXYZ0"
BASE = "/api/chat/conversations"
QUESTION = "What grows well in a shady corner?"


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    r = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
    assert r.status_code == 200
    return client


@pytest.fixture
def token(owner, core):
    """A client acting as a read-and-write token, with no owner session."""
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(owner.app, base_url="http://testserver",
                    headers={"authorization": f"Bearer {secret}"}) as c:
        yield c


def stream(response) -> list[tuple[str, dict]]:
    """A reply stream as (event name, data) pairs."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in response.text.split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if lines:
            assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
            out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def start(owner, **body) -> str:
    r = owner.post(BASE, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("the condition never became true")


def test_chat_needs_someone_signed_in(client):
    for path in (BASE, f"{BASE}/{MISSING}", "/api/chat/options"):
        assert client.get(path).status_code == 401, path
    assert client.post(BASE, json={}).status_code == 401
    assert client.post(f"{BASE}/{MISSING}/messages", json={"text": "Hi"}).status_code == 401


def test_a_token_can_read_but_only_the_owner_can_talk_to_a_model(owner, token, core):
    models(core)
    conv_id = start(owner)
    assert token.get(BASE).json()["conversations"][0]["id"] == conv_id
    assert token.get(f"{BASE}/{conv_id}").status_code == 200
    assert token.get("/api/chat/options").status_code == 200
    for method, path, body in (
        ("post", BASE, {}),
        ("patch", f"{BASE}/{conv_id}", {"title": "Mine now"}),
        ("post", f"{BASE}/{conv_id}/messages", {"text": "Hi"}),
        ("post", f"{BASE}/{conv_id}/regenerate", None),
        ("post", f"{BASE}/{conv_id}/stop", None),
        ("post", f"{BASE}/{conv_id}/archive", None),
        ("post", f"{BASE}/{conv_id}/restore", None),
    ):
        r = getattr(token, method)(path, json=body)
        assert r.status_code == 403 and "owner" in r.json()["detail"], path
    assert not of_kind(core, "chat.message.sent", "model.called")


def test_a_conversation_starts_with_defaults_and_can_be_listed_and_read(owner):
    assert owner.get(BASE).json() == {"conversations": [], "next": None}
    conv = owner.post(BASE).json()
    assert conv["target"] == {"kind": "role", "role": "default"}
    assert (conv["title"], conv["title_source"], conv["effort"]) == ("", "none", None)
    assert conv["local_only"] is False and conv["archived"] is False
    assert conv["messages"] == 0 and conv["streaming"] is False
    assert conv["created"] == conv["updated"] > 0
    assert owner.get(BASE).json()["conversations"] == [conv]
    assert owner.get(f"{BASE}/{conv['id']}").json() == {
        "conversation": conv, "messages": [], "older": None, "unanswered": False,
        "can_regenerate": False}
    chosen = owner.post(BASE, json={
        "title": "Plans", "effort": "low", "local_only": True,
        "target": {"kind": "model", "provider_id": "home", "model": "model-a"}}).json()
    assert chosen["title"] == "Plans" and chosen["title_source"] == "owner"
    assert chosen["target"] == {"kind": "model", "provider_id": "home", "model": "model-a"}
    assert chosen["effort"] == "low" and chosen["local_only"] is True


def test_sending_streams_start_then_deltas_then_done(owner, core):
    models(core)
    conv_id = start(owner)
    response = owner.post(f"{BASE}/{conv_id}/messages", json={"text": QUESTION})
    assert response.headers["cache-control"] == "no-cache"
    events = stream(response)
    assert [name for name, _ in events] == ["start", "delta", "delta", "done"]
    begin = events[0][1]
    assert begin["conversation_id"] == conv_id and begin["regenerate"] is False
    assert begin["user_message"]["text"] == QUESTION and begin["user_message"]["role"] == "user"
    assert (begin["provider_id"], begin["provider_label"], begin["model"], begin["local"]) == (
        "home", "Home", "model-a", True)
    assert begin["omitted"] == 0 and begin["replaces"] is None
    assert [data for name, data in events if name == "delta"] == [{"text": p} for p in PIECES]
    reply = events[-1][1]["message"]
    assert reply["id"] == begin["assistant_message_id"] and reply["text"] == TEXT
    assert reply["state"] == "complete" and reply["local"] is True
    detail = owner.get(f"{BASE}/{conv_id}").json()
    assert detail["messages"] == [begin["user_message"], reply]
    assert detail["can_regenerate"] is True and detail["unanswered"] is False
    assert detail["conversation"]["title"] == QUESTION


def test_a_model_failure_ends_the_stream_with_one_error_and_a_stored_failed_reply(owner, core):
    home = SlowProvider(then="fail")
    home.release.set()
    models(core, home=home)
    conv_id = start(owner)
    events = stream(owner.post(f"{BASE}/{conv_id}/messages", json={"text": QUESTION}))
    assert [name for name, _ in events] == ["start", "delta", "error"]
    error = events[-1][1]
    assert "could not be reached" in error["detail"]
    assert error["message"]["state"] == "failed" and error["message"]["text"] == "Hello"
    detail = owner.get(f"{BASE}/{conv_id}").json()
    assert detail["messages"][-1] == error["message"] and detail["can_regenerate"] is True

    models(core)
    events = stream(owner.post(f"{BASE}/{conv_id}/regenerate"))
    assert [name for name, _ in events] == ["start", "delta", "delta", "done"]
    assert events[0][1]["replaces"] == error["message"]["id"]
    assert events[0][1]["regenerate"] is True
    shown = owner.get(f"{BASE}/{conv_id}").json()["messages"]
    assert [m["state"] for m in shown if m["role"] == "assistant"] == ["complete"]


def test_validation_problems_are_ordinary_json_answers_not_stream_errors(owner, core):
    models(core)
    conv_id = start(owner)
    core.settings.set("chat.max_message_chars", 100, actor="owner")
    send = f"{BASE}/{conv_id}/messages"
    for body, status in (
        ({}, 422),
        ({"text": ""}, 422),
        ({"text": "   \n"}, 422),
        ({"text": 7}, 422),
        ({"text": "x" * 101}, 422),
        ({"text": "a\x00b"}, 422),
        ({"text": "Hi", "surprise": True}, 422),
        ({"text": "Hi", "effort": "enormous"}, 422),
        ({"text": "Hi", "target": "default"}, 422),
        ({"text": "Hi", "target": {"kind": "role", "role": "embeddings"}}, 422),
        ({"text": "Hi", "target": {"kind": "role", "role": "default", "extra": 1}}, 422),
        ({"text": "Hi", "target": {"kind": "model", "provider_id": "Home!", "model": "m"}}, 422),
        ({"text": "Hi", "target": {"kind": "model", "provider_id": "home", "model": " "}}, 422),
        ({"text": "Hi", "target": {"kind": "agent", "agent_id": "scout"}}, 422),
        ({"text": "Hi", "target": {"kind": "model", "provider_id": "nowhere", "model": "m"}},
         409),
    ):
        r = owner.post(send, json=body)
        assert r.status_code == status, body
        assert r.headers["content-type"].startswith("application/json")
        detail = r.json()["detail"]
        assert isinstance(detail, str) and detail.endswith(".") and "\x00" not in detail
    assert "later release" in owner.post(
        send, json={"text": "Hi", "target": {"kind": "agent", "agent_id": "x"}}).json()["detail"]
    assert owner.post(send, content=b"{not json", headers={
        "content-type": "application/json"}).status_code == 422
    assert owner.post(f"{BASE}/{MISSING}/messages", json={"text": "Hi"}).status_code == 404
    assert owner.post(f"{BASE}/not-an-id/messages", json={"text": "Hi"}).status_code == 404
    assert owner.post(f"{BASE}/{MISSING}/regenerate").status_code == 404
    assert owner.post(f"{BASE}/{conv_id}/regenerate").status_code == 409  # nothing to redo yet
    assert not of_kind(core, "chat.message.sent", "chat.conversation.changed")
    assert owner.get(f"{BASE}/{conv_id}").json()["conversation"]["streaming"] is False


def test_with_no_model_set_up_a_send_says_where_to_add_one(owner, core):
    conv_id = start(owner)
    r = owner.post(f"{BASE}/{conv_id}/messages", json={"text": QUESTION})
    assert r.status_code == 409
    assert "Settings, Models and roles" in r.json()["detail"]
    assert owner.get(f"{BASE}/{conv_id}").json()["messages"] == []


def test_a_second_send_while_one_is_streaming_is_refused_and_stop_works_from_another_tab(
        owner, core):
    home = SlowProvider()
    models(core, home=home)
    conv_id = start(owner)
    result = {}

    def first_tab():
        result["events"] = stream(
            owner.post(f"{BASE}/{conv_id}/messages", json={"text": QUESTION}))

    tab = threading.Thread(target=first_tab)
    tab.start()
    try:
        wait_for(home.streaming.is_set)
        assert owner.get(f"{BASE}/{conv_id}").json()["conversation"]["streaming"] is True
        assert owner.get(BASE).json()["conversations"][0]["streaming"] is True
        for path, body in ((f"{BASE}/{conv_id}/messages", {"text": "Me too"}),
                           (f"{BASE}/{conv_id}/regenerate", None),
                           (f"{BASE}/{conv_id}/archive", None)):
            r = owner.post(path, json=body)
            assert r.status_code == 409 and "reply" in r.json()["detail"], path
        assert owner.post(f"{BASE}/{conv_id}/stop").json() == {"stopped": True}
    finally:
        home.release.set()
        tab.join(10)
    assert [name for name, _ in result["events"]] == ["start", "delta", "done"]
    reply = result["events"][-1][1]["message"]
    assert (reply["state"], reply["text"]) == ("stopped", "Hello")
    detail = owner.get(f"{BASE}/{conv_id}").json()
    assert detail["messages"][-1] == reply and detail["conversation"]["streaming"] is False
    assert len(detail["messages"]) == 2
    assert owner.post(f"{BASE}/{conv_id}/stop").json() == {"stopped": False}
    assert owner.post(f"{BASE}/{MISSING}/stop").status_code == 404


def test_renaming_and_changing_what_answers(owner, core):
    conv_id = start(owner)
    path = f"{BASE}/{conv_id}"
    assert owner.patch(path, json={"title": "  Spring   plans "}).json()["title"] == "Spring plans"
    changed = owner.patch(path, json={
        "target": {"kind": "role", "role": "smart"}, "effort": "high", "local_only": True}).json()
    assert changed["target"] == {"kind": "role", "role": "smart"}
    assert (changed["effort"], changed["local_only"], changed["title_source"]) == (
        "high", True, "owner")
    assert owner.patch(path, json={"effort": None}).json()["effort"] is None
    before = len(of_kind(core, "chat.conversation.changed"))
    assert owner.patch(path, json={"title": "Spring plans"}).status_code == 200  # no change
    assert len(of_kind(core, "chat.conversation.changed")) == before
    for body in ({}, {"title": ""}, {"title": None}, {"title": "x" * 121}, {"title": 5},
                 {"target": None}, {"local_only": None}, {"local_only": "yes"},
                 {"effort": "huge"}, {"archived": True},
                 {"target": {"kind": "agent", "agent_id": "scout"}}):
        r = owner.patch(path, json=body)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), body
    assert owner.patch(f"{BASE}/{MISSING}", json={"title": "Nobody"}).status_code == 404


def test_archive_and_restore_over_http(owner, core):
    models(core)
    conv_id = start(owner)
    stream(owner.post(f"{BASE}/{conv_id}/messages", json={"text": QUESTION}))
    assert owner.post(f"{BASE}/{conv_id}/archive").json()["archived"] is True
    assert owner.post(f"{BASE}/{conv_id}/archive").json()["archived"] is True  # twice is fine
    assert owner.get(BASE).json()["conversations"] == []
    assert [c["id"] for c in owner.get(BASE, params={"archived": "true"}).json()[
        "conversations"]] == [conv_id]
    assert owner.post(f"{BASE}/{conv_id}/messages", json={"text": "Hi"}).status_code == 409
    assert len(owner.get(f"{BASE}/{conv_id}").json()["messages"]) == 2
    assert owner.post(f"{BASE}/{conv_id}/restore").json()["archived"] is False
    assert [c["id"] for c in owner.get(BASE).json()["conversations"]] == [conv_id]
    assert owner.post(f"{BASE}/{MISSING}/archive").status_code == 404
    assert owner.post(f"{BASE}/{MISSING}/restore").status_code == 404


def test_odd_reads_get_a_sentence_and_never_a_crash(owner):
    conv_id = start(owner)
    for path in (f"{BASE}/{MISSING}", f"{BASE}/nope", f"{BASE}/{'9' * 400}",
                 f"{BASE}/%00", f"{BASE}/{conv_id.lower()}"):
        r = owner.get(path)
        assert r.status_code == 404 and isinstance(r.json()["detail"], str), path
    for params in ({"limit": "0"}, {"limit": "201"}, {"limit": "many"}, {"before": "x" * 65},
                   {"before": "yesterday"}, {"archived": "perhaps"}):
        r = owner.get(BASE, params=params)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), params
    for params in ({"limit": "0"}, {"limit": "-1"}, {"before": MISSING}, {"before": "%"}):
        r = owner.get(f"{BASE}/{conv_id}", params=params)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), params
    for body in ([], "text", {"title": ["a"]}, {"target": []}, {"local_only": 1},
                 {"effort": 5}, {"title": "x" * 2000}, {"target": {"kind": "role"}},
                 {"target": {"kind": 3}}, {"target": {"kind": "role", "role": None}}):
        r = owner.post(BASE, json=body)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), body


def test_the_composer_gets_its_options_in_one_call(owner, core):
    empty = owner.get("/api/chat/options").json()
    assert [r["role"] for r in empty["roles"]] == ["default", "cheap", "smart", "code", "vision"]
    assert all(r["provider_id"] is None and r["available"] is False for r in empty["roles"])
    assert empty["providers"] == []
    assert empty["defaults"] == {"target": {"kind": "role", "role": "default"},
                                 "effort": None, "local_only": False, "on_record": True,
                                 "use_memory": True}
    assert "high" in empty["efforts"] and empty["max_message_chars"] == 200000

    models(core, cloud=FakeProvider("openai_compatible", "down"), cheap=("cloud", "typed-model"))
    core.settings.set("chat.local_only", True, actor="owner")
    options = owner.get("/api/chat/options").json()
    roles = {r["role"]: r for r in options["roles"]}
    assert roles["default"] == {
        "role": "default", "provider_id": "home", "provider_label": "Home", "model": "model-a",
        "effort": None, "local": True, "available": True, "inherits_default": False,
        "fallbacks": 1, "memory": True}
    assert roles["cheap"]["memory"] is False  # hosted, and memory stays off hosted models
    assert (roles["cheap"]["provider_id"], roles["cheap"]["model"], roles["cheap"]["local"]) == (
        "cloud", "typed-model", False)
    assert roles["smart"]["inherits_default"] is True and roles["smart"]["model"] == "model-a"
    providers = {p["id"]: p for p in options["providers"]}
    assert providers["home"] == {"id": "home", "label": "Home", "type": "openai_compatible",
                                 "local": True, "models": ["model-a", "model-b"],
                                 "listed": True, "memory": True}
    assert providers["cloud"]["memory"] is False and options["memory"] == {"to_hosted": False}
    # A provider that cannot list still offers the models typed in for a role.
    assert providers["cloud"]["models"] == ["model-b", "typed-model"]
    assert providers["cloud"]["listed"] is False and providers["cloud"]["local"] is False
    assert options["defaults"]["local_only"] is True
    assert "sk-test" not in json.dumps(options)


def test_the_list_takes_a_title_search(owner):
    kept = start(owner, title="Greenhouse plans")
    start(owner, title="Something else")
    r = owner.get(BASE, params={"q": "GREENHOUSE"})
    assert r.status_code == 200 and r.json()["next"] is None
    assert [c["id"] for c in r.json()["conversations"]] == [kept]
    assert owner.get(BASE, params={"q": "nothing like it"}).json()["conversations"] == []
    r = owner.get(BASE, params={"q": "x" * 121})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_a_reply_in_flight_when_the_web_process_stops_is_on_the_tape_before_it_exits(core):
    models(core, home=SlowProvider())
    conv_id = chat.start_conversation(core)["id"]
    app = create_app(core)

    async def scenario():
        async with app.router.lifespan_context(app):
            run = await replies.begin(core, conv_id, text=QUESTION)
            while run.queue.empty():
                await asyncio.sleep(0.005)
        # The lifespan has ended; the process would exit here.
        return [(e.kind, e.payload.get("state")) for e in of_kind(
            core, "chat.message.answered", "model.failed")]

    assert asyncio.run(scenario()) == [("model.failed", None),
                                       ("chat.message.answered", "stopped")]
