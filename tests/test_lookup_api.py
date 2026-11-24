"""Web lookup over HTTP: the status, a provider's key, and the owner's "try it"."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from jimmyhq import lookup, modules
from jimmyhq.lookup import searching
from jimmyhq.settings import sections
from jimmyhq.web.app import create_app
from test_lookup_fakes import receipts

PASSWORD = "correct horse battery"
KEY = "test-key-0123456789"


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam",
                                                "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def token(owner, core):
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(owner.app, base_url="http://testserver",
                    headers={"authorization": f"Bearer {secret}"}) as c:
        yield c


def serve(core, answer: dict | None = None, status: int = 200) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer or {"results": [
            {"title": "Growing broad beans", "url": "https://example.com/beans",
             "content": "Sow from late winter."}]})

    lookup.service(core).transport = httpx.MockTransport(handler)
    return seen


def test_web_lookup_is_a_module_with_its_own_settings_section_and_everything_off(core):
    assert "lookup" in modules.MODULES
    get = core.settings.get
    assert get("lookup.enabled") is False and get("lookup.chat_default") is False
    assert get("lookup.search_provider") == "none" and get("lookup.searxng_url") == ""
    assert (get("lookup.results"), get("lookup.fetch_max_bytes")) == (6, 5 * 1024 * 1024)
    assert (get("lookup.fetch_timeout_s"), get("lookup.page_chars")) == (30, 12000)
    assert (get("lookup.steps_per_reply"), get("lookup.allow_private_hosts")) == (6, [])
    assert get("lookup.fetch_any_url") is False
    assert dict(sections())["lookup"] == "Web lookup"


def test_the_status_needs_someone_signed_in_and_a_token_may_read_it(client, owner, token):
    with TestClient(owner.app, base_url="http://testserver") as stranger:
        assert stranger.get("/api/lookup/status").status_code == 401
    assert token.get("/api/lookup/status").status_code == 200
    for call in (lambda: token.put("/api/lookup/providers/tavily/key", json={"key": KEY}),
                 lambda: token.delete("/api/lookup/providers/tavily/key"),
                 lambda: token.post("/api/lookup/try", json={"query": "beans"})):
        assert call().status_code == 403


def test_the_status_says_what_is_set_up_and_what_is_missing(owner, core):
    status = owner.get("/api/lookup/status").json()
    assert status["enabled"] is False and status["provider"] == "none"
    assert status["search_ready"] is False and "Search provider" in status["search_problem"]
    assert [p["id"] for p in status["providers"]] == ["none", "exa", "tavily", "brave",
                                                      "searxng", "jina"]
    assert {p["id"]: p["needs_key"] for p in status["providers"]} == {
        "none": False, "exa": True, "tavily": True, "brave": True, "searxng": False,
        "jina": True}
    core.settings.set("lookup.search_provider", "tavily", actor="owner")
    status = owner.get("/api/lookup/status").json()
    assert (status["provider_label"], status["needs_key"], status["key_set"]) == (
        "Tavily", True, False)
    assert "needs an API key" in status["search_problem"]


def test_a_key_can_be_saved_and_cleared_and_is_never_shown_or_recorded(owner, core):
    core.settings.set("lookup.search_provider", "tavily", actor="owner")
    saved = owner.put("/api/lookup/providers/tavily/key", json={"key": f"  {KEY}\n"})
    assert saved.status_code == 200 and saved.json()["key_set"] is True
    assert saved.json()["search_ready"] is True and saved.json()["search_problem"] is None
    assert core.secrets.get(searching.secret_name("tavily")) == KEY
    assert KEY not in saved.text and KEY not in owner.get("/api/lookup/status").text
    assert KEY not in owner.get("/api/settings").text
    assert KEY not in json.dumps([e.payload for e in core.tape.after(0, limit=100000)])
    cleared = owner.delete("/api/lookup/providers/tavily/key")
    assert cleared.json()["key_set"] is False
    assert core.secrets.get(searching.secret_name("tavily")) is None
    assert owner.delete("/api/lookup/providers/tavily/key").status_code == 200  # already gone


@pytest.mark.parametrize("provider, body, status", [
    ("nobody", {"key": KEY}, 404), ("none", {"key": KEY}, 404), ("searxng", {"key": KEY}, 422),
    ("tavily", {"key": ""}, 422), ("tavily", {"key": "two words"}, 422),
    ("tavily", {"key": "   "}, 422), ("tavily", {}, 422), ("tavily", {"key": "k" * 501}, 422),
    ("tavily", {"key": KEY, "extra": 1}, 422),
])
def test_an_odd_key_request_gets_a_sentence(owner, provider, body, status):
    r = owner.put(f"/api/lookup/providers/{provider}/key", json=body)
    assert r.status_code == status and r.json()["detail"]


def test_the_owner_can_try_a_search_before_switching_web_lookup_on(owner, core):
    core.settings.set("lookup.search_provider", "searxng", actor="owner")
    core.settings.set("lookup.searxng_url", "https://search.example.com", actor="owner")
    seen = serve(core)
    assert core.settings.get("lookup.enabled") is False
    r = owner.post("/api/lookup/try", json={"query": "broad beans"})
    assert r.status_code == 200, r.text
    tried = r.json()
    assert (tried["provider"], tried["provider_label"]) == ("searxng",
                                                            "SearXNG (your own server)")
    assert tried["results"] == [{"title": "Growing broad beans",
                                 "url": "https://example.com/beans",
                                 "snippet": "Sow from late winter.", "text": None}]
    assert len(seen) == 1 and seen[0].url.params["q"] == "broad beans"
    (receipt,) = receipts(core, "lookup.searched")
    assert (receipt["caller"], receipt["query"]) == ("lookup.try", "broad beans")


def test_trying_a_search_that_cannot_work_says_what_to_do(owner, core):
    r = owner.post("/api/lookup/try", json={"query": "broad beans"})
    assert r.status_code == 409 and "Search provider" in r.json()["detail"]
    core.settings.set("lookup.search_provider", "brave", actor="owner")
    r = owner.post("/api/lookup/try", json={"query": "broad beans"})
    assert r.status_code == 409 and "needs an API key" in r.json()["detail"]
    owner.put("/api/lookup/providers/brave/key", json={"key": KEY})
    serve(core, {"error": "slow"}, 429)
    r = owner.post("/api/lookup/try", json={"query": "broad beans"})
    assert r.status_code == 429 and "rate limiting" in r.json()["detail"]
    assert owner.post("/api/lookup/try", json={"query": ""}).status_code == 422
    assert owner.post("/api/lookup/try", json={}).status_code == 422
