from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from jimmyhq.models import ModelError, brain
from jimmyhq.web.app import create_app
from test_models_fakes import TEXT, TOKENS_IN, TOKENS_OUT, FakeProvider, wire

PASSWORD = "correct horse battery"
KEY = "sk-test-SECRET-0123456789"
HOME, CLOUD = "127.0.0.1", "cloud.example.com"
HOME_BODY = {"type": "openai_compatible", "label": "Home server",
             "base_url": f"http://{HOME}:11434/v1/"}
CLOUD_BODY = {"id": "cloud", "type": "openai_compatible", "label": "Cloud",
              "base_url": f"https://{CLOUD}/v1"}


@pytest.fixture
def fakes():
    return {HOME: FakeProvider("openai_compatible"),
            CLOUD: FakeProvider("openai_compatible", text="From the cloud")}


@pytest.fixture
def owner(core, fakes):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        wire(core, fakes)
        yield client


@pytest.fixture
def ready(owner):
    """Both providers saved, a key on the hosted one, and a default role."""
    assert owner.post("/api/models/providers", json=HOME_BODY).status_code == 201
    assert owner.post("/api/models/providers", json=CLOUD_BODY).status_code == 201
    assert owner.put("/api/models/providers/cloud/key", json={"key": KEY}).status_code == 200
    role = {"primary": {"provider_id": "home-server", "model": "model-a"},
            "fallbacks": [{"provider_id": "cloud", "model": "model-b", "effort": "low"}]}
    assert owner.put("/api/models/roles/default", json=role).status_code == 200
    return owner


def token(client, core, scopes):
    _, value = core.auth.create_token("wall", scopes)
    client.cookies.clear()
    return {"authorization": f"Bearer {value}"}


def test_nothing_under_models_answers_without_signing_in(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        for path in ("providers", "roles", "health", "usage", "prices"):
            assert client.get(f"/api/models/{path}").status_code == 401, path


def test_a_provider_is_created_with_an_id_from_its_label_and_local_from_its_address(owner):
    r = owner.post("/api/models/providers", json=HOME_BODY)
    assert r.status_code == 201
    assert r.json()["provider"] == {
        "id": "home-server", "type": "openai_compatible", "label": "Home server",
        "base_url": f"http://{HOME}:11434/v1", "enabled": True, "local": True,
        "options": {"max_tokens": None, "temperature": None, "effort": None,
                    "native_json": None},
        "region": None, "profile": None, "has_key": False, "needs_key": False,
        "health": {"ok": None, "checked_ms": None, "error": None},
    }
    hosted = owner.post("/api/models/providers", json=CLOUD_BODY).json()["provider"]
    assert (hosted["id"], hosted["local"]) == ("cloud", False)
    listed = owner.get("/api/models/providers").json()["providers"]
    assert [p["id"] for p in listed] == ["cloud", "home-server"]


def test_a_second_provider_with_the_same_id_is_refused(owner):
    owner.post("/api/models/providers", json=CLOUD_BODY)
    r = owner.post("/api/models/providers", json=CLOUD_BODY)
    assert r.status_code == 409 and "already a provider" in r.json()["detail"]


def test_a_change_touches_only_the_fields_sent(ready):
    r = ready.put("/api/models/providers/cloud",
                  json={"label": "Hosted", "local": True, "options": {"max_tokens": 500}})
    changed = r.json()["provider"]
    assert (changed["label"], changed["local"], changed["options"]["max_tokens"]) == (
        "Hosted", True, 500)
    assert changed["base_url"] == f"https://{CLOUD}/v1" and changed["has_key"] is True
    off = ready.put("/api/models/providers/cloud", json={"enabled": False}).json()["provider"]
    assert off["enabled"] is False and off["label"] == "Hosted"
    assert ready.put("/api/models/providers/nowhere", json={"label": "x"}).status_code == 404


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({"type": "magic", "label": "x"}, 422, "provider type must be one of"),
        ({"type": "openai_compatible", "label": "x"}, 422, "needs an address"),
        ({"type": "openai_compatible", "label": "   ", "base_url": "http://a.example"}, 422,
         "label cannot be empty"),
        ({"type": "openai", "label": "x", "base_url": "javascript:alert(1)"}, 422,
         "must start with http"),
        ({"type": "openai", "label": "x", "base_url": "https://name:pass@example.com/v1"}, 422,
         "Do not put a name or key"),
        ({"type": "openai", "label": "x", "id": "../etc"}, 422, "provider id must start"),
        ({"type": "openai", "label": "x" * 300}, 422, "label"),
        ({"type": "openai", "label": "x", "surprise": 1}, 422, "surprise"),
        ({"type": "openai", "label": "x", "enabled": "maybe"}, 422, "enabled"),
        ({"type": "bedrock", "label": "x"}, 422, "needs an AWS region"),
        ({"label": "x"}, 422, "type"),
        ([], 422, ""),
    ],
)
def test_a_bad_provider_comes_back_as_one_readable_sentence(owner, body, status, message):
    r = owner.post("/api/models/providers", json=body)
    assert r.status_code == status
    assert isinstance(r.json()["detail"], str) and message in r.json()["detail"]
    assert owner.get("/api/models/providers").json() == {"providers": []}


def test_a_request_that_is_not_json_is_a_readable_422_not_a_500(owner):
    r = owner.post("/api/models/providers", content=b"{not json",
                   headers={"content-type": "application/json"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    r = owner.post("/api/models/try", content=b'{"prompt": "\\ud800"}',
                   headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_a_key_can_be_saved_and_removed_and_only_its_presence_is_ever_shown(ready, core):
    listed = ready.get("/api/models/providers").json()["providers"]
    shown = {p["id"]: p["has_key"] for p in listed}
    assert shown == {"cloud": True, "home-server": False}
    assert core.secrets.get("models.cloud.api_key") == KEY
    assert ready.delete("/api/models/providers/cloud/key").json() == {
        "id": "cloud", "has_key": False}
    assert core.secrets.names() == []
    assert ready.put("/api/models/providers/nowhere/key", json={"key": KEY}).status_code == 404
    for bad in ("", "   ", "two words", "line\nbreak", "café"):
        r = ready.put("/api/models/providers/cloud/key", json={"key": bad})
        assert r.status_code == 422, bad
        assert not bad.strip() or bad.strip() not in r.json()["detail"]


def test_a_provider_in_use_by_a_role_cannot_be_removed_until_the_role_moves(ready, core):
    r = ready.delete("/api/models/providers/cloud")
    assert r.status_code == 409 and "default role" in r.json()["detail"]
    ready.put("/api/models/roles/default",
              json={"primary": {"provider_id": "home-server", "model": "model-a"}})
    assert ready.delete("/api/models/providers/cloud").json() == {"ok": True}
    assert core.secrets.names() == []  # its key goes with it
    assert ready.delete("/api/models/providers/cloud").status_code == 404


def test_roles_are_listed_in_full_and_unset_ones_say_they_follow_the_default(ready):
    roles = {r["role"]: r for r in ready.get("/api/models/roles").json()["roles"]}
    assert list(roles) == ["default", "cheap", "smart", "code", "vision", "embeddings"]
    assert roles["default"] == {
        "role": "default", "inherits_default": False,
        "primary": {"provider_id": "home-server", "model": "model-a", "effort": None},
        "fallbacks": [{"provider_id": "cloud", "model": "model-b", "effort": "low"}],
    }
    assert roles["cheap"] == {"role": "cheap", "primary": None, "fallbacks": [],
                              "inherits_default": True}
    assert roles["embeddings"]["inherits_default"] is False


@pytest.mark.parametrize(
    ("role", "body", "status", "message"),
    [
        ("wizard", {"primary": None}, 404, "no role called"),
        ("smart", {"primary": {"provider_id": "nowhere", "model": "m"}}, 422, "no provider"),
        ("smart", {"primary": {"provider_id": "cloud"}}, 422, "model must be text"),
        ("smart", {"primary": {"provider_id": "cloud", "model": " "}}, 422, "cannot be empty"),
        ("smart", {"primary": {"provider_id": "cloud", "model": "m", "effort": "extreme"}},
         422, "Effort must be one of"),
        ("smart", {"primary": None, "fallbacks": [{"provider_id": "cloud", "model": "m"}]},
         422, "needs a first choice"),
        ("smart", {"primary": "cloud"}, 422, "primary"),
        ("smart", {"primary": {"provider_id": "cloud", "model": "m"},
                   "fallbacks": [{"provider_id": "cloud", "model": "m"}] * 9}, 422, "fallbacks"),
    ],
)
def test_a_bad_role_is_refused_with_what_to_fix(ready, role, body, status, message):
    r = ready.put(f"/api/models/roles/{role}", json=body)
    assert r.status_code == status and message in r.json()["detail"]


def test_a_role_cannot_point_at_a_provider_that_is_switched_off(ready):
    ready.put("/api/models/providers/cloud", json={"enabled": False})
    r = ready.put("/api/models/roles/smart",
                  json={"primary": {"provider_id": "cloud", "model": "m"}})
    assert r.status_code == 422 and "switched off" in r.json()["detail"]


def test_discovered_models_are_listed_cached_and_refreshed_on_request(ready, fakes):
    first = ready.get("/api/models/providers/home-server/models").json()
    assert first["models"] == ["model-a", "model-b"] and first["cached"] is False
    assert ready.get("/api/models/providers/home-server/models").json()["cached"] is True
    refreshed = ready.get("/api/models/providers/home-server/models", params={"refresh": True})
    assert refreshed.json()["cached"] is False and len(fakes[HOME].requests) == 2
    assert ready.get("/api/models/providers/nowhere/models").status_code == 404
    fakes[CLOUD].mode = "auth"
    r = ready.get("/api/models/providers/cloud/models")
    assert r.status_code == 502 and "refused the key" in r.json()["detail"]


def test_testing_a_provider_says_whether_it_works_and_why_not(ready, fakes):
    assert ready.post("/api/models/providers/home-server/test").json() | {"checked_ms": 0} == {
        "ok": True, "error": None, "models": 2, "checked_ms": 0}
    fakes[CLOUD].mode = "down"
    result = ready.post("/api/models/providers/cloud/test").json()
    assert result["ok"] is False and "could not be reached" in result["error"]
    assert ready.post("/api/models/providers/nowhere/test").status_code == 404
    health = {p["id"]: p["health"]["ok"]
              for p in ready.get("/api/models/providers").json()["providers"]}
    assert health == {"cloud": False, "home-server": True}


def test_health_reports_each_enabled_provider_and_does_not_recheck_within_the_interval(
    ready, fakes
):
    fakes[CLOUD].mode = "server"
    body = ready.get("/api/models/health").json()
    by_id = {p["id"]: p for p in body["providers"]}
    assert body["interval_s"] == 30
    assert by_id["home-server"] | {"checked_ms": 0} == {
        "id": "home-server", "label": "Home server", "type": "openai_compatible",
        "local": True, "ok": True, "checked_ms": 0, "error": None}
    assert by_id["cloud"]["ok"] is False and "trouble" in by_id["cloud"]["error"]
    for _ in range(3):
        ready.get("/api/models/health")
    assert [len(f.requests) for f in fakes.values()] == [1, 1]
    assert all(r.method == "GET" for f in fakes.values() for r in f.requests)


def test_trying_a_role_returns_a_short_completion_and_keeps_only_counts(ready, core):
    r = ready.post("/api/models/try", json={"role": "default", "prompt": "Say hello"})
    body = r.json()
    assert body["ok"] is True and body["error"] is None
    reply = body["reply"]
    assert (reply["text"], reply["provider_id"], reply["model"], reply["local"]) == (
        TEXT, "home-server", "model-a", True)
    assert (reply["input_tokens"], reply["output_tokens"]) == (TOKENS_IN, TOKENS_OUT)
    assert reply["fallback_from"] is None and "data" not in reply
    (event,) = core.tape.after(0, kind="model.called")
    assert (event.payload["caller"], event.payload["prompt_ref"], event.actor) == (
        "models.try", None, "owner")


def test_trying_shows_a_fallback_and_a_failure_in_words(ready, fakes):
    fakes[HOME].mode = "down"
    reply = ready.post("/api/models/try", json={"prompt": "Hi"}).json()["reply"]
    assert reply["provider_id"] == "cloud" and reply["fallback_from"] == "home-server/model-a"
    assert "could not be reached" in reply["fallback_reason"]
    failed = ready.post("/api/models/try", json={"prompt": "Hi", "local_only": True}).json()
    assert failed == {"ok": False, "reply": None, "reason": "unreachable",
                      "error": failed["error"]} and "Home server" in failed["error"]
    direct = ready.post("/api/models/try", json={"provider_id": "cloud", "model": "model-z",
                                                 "prompt": "Hi"}).json()
    assert direct["reply"]["model"] == "model-z"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"prompt": ""}, "prompt"),
        ({"prompt": "   "}, "prompt"),
        ({"prompt": "x" * 4001}, "prompt"),
        ({"role": "default"}, "prompt"),
        ({"role": "wizard", "prompt": "Hi"}, "no role called"),
        ({"provider_id": "cloud", "prompt": "Hi"}, "both a provider and a model"),
        ({"model": "m", "prompt": "Hi"}, "both a provider and a model"),
        ({"role": "default", "provider_id": "cloud", "model": "m", "prompt": "Hi"}, "not both"),
        ({"prompt": 7}, "prompt"),
    ],
)
def test_a_bad_try_request_is_a_readable_422(ready, body, message):
    r = ready.post("/api/models/try", json=body)
    assert r.status_code == 422 and message in r.json()["detail"]


def test_trying_a_provider_that_does_not_exist_says_so_without_a_500(ready):
    r = ready.post("/api/models/try", json={"provider_id": "nowhere", "model": "m",
                                            "prompt": "Hi"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["reason"] == "not_configured"


def test_prices_start_as_the_shipped_examples_and_the_owner_can_replace_them(ready):
    start = ready.get("/api/models/prices").json()
    assert start["is_default"] is True and start["prices"][0].keys() == {
        "id", "provider", "model", "input_per_mtok", "output_per_mtok"}
    table = [{"id": "mine", "provider": "cloud", "model": "*", "input_per_mtok": 2,
              "output_per_mtok": 8}]
    saved = ready.put("/api/models/prices", json={"prices": table}).json()
    assert saved == {"is_default": False, "prices": [table[0] | {
        "input_per_mtok": 2.0, "output_per_mtok": 8.0}]}
    assert ready.get("/api/models/prices").json() == saved
    r = ready.put("/api/models/prices", json={"prices": [table[0] | {"input_per_mtok": "x"}]})
    assert r.status_code == 422 and "must be a number" in r.json()["detail"]
    assert ready.put("/api/models/prices", json={"prices": "free"}).status_code == 422


def test_usage_reports_totals_cost_and_what_running_locally_saved(ready, fakes):
    ready.put("/api/models/prices", json={"prices": [
        {"id": "ref", "provider": "cloud", "model": "*", "input_per_mtok": 500_000,
         "output_per_mtok": 1_000_000}]})
    ready.put("/api/settings/models.savings_reference", json={"value": "ref"})
    ready.post("/api/models/try", json={"prompt": "Hi"})
    ready.post("/api/models/try", json={"provider_id": "cloud", "model": "m", "prompt": "Hi"})
    fakes[HOME].mode = "down"
    ready.post("/api/models/try", json={"prompt": "Hi", "local_only": True})
    usage = ready.get("/api/models/usage").json()
    each = TOKENS_IN / 2 + TOKENS_OUT  # half a unit per input token, one per output token
    for window in ("all_time", "last_24h"):
        total = usage[window]
        assert (total["calls"], total["local_calls"], total["failed_calls"]) == (2, 1, 1)
        assert (total["input_tokens"], total["output_tokens"]) == (2 * TOKENS_IN,
                                                                   2 * TOKENS_OUT)
        assert total["estimated_cost"] == each and total["saved_by_local"] == each
        assert {m["provider_id"] for m in total["models"]} == {"home-server", "cloud"}
    assert usage["savings_reference"]["id"] == "ref" and usage["currency"] == "USD"
    assert [d["calls"] for d in usage["days"]] == [2]


def test_a_read_token_can_look_but_only_the_owner_can_change_spend_or_see_keys_set(
        ready, core, fakes):
    headers = token(ready, core, ["read"])
    for path in ("providers", "roles", "health", "usage", "prices", "providers/cloud/models"):
        assert ready.get(f"/api/models/{path}", headers=headers).status_code == 200, path
    # A read token cannot force a fresh model list: that is an outbound call with the key.
    asked = len(fakes[CLOUD].requests)
    for _ in range(2):
        r = ready.get("/api/models/providers/cloud/models", params={"refresh": True},
                      headers=headers)
        assert r.status_code == 200 and r.json()["cached"] is True
    assert len(fakes[CLOUD].requests) == asked
    write = token(ready, core, ["read", "write"])
    for hdrs in (headers, write):
        for method, path, body in (
            ("post", "providers", CLOUD_BODY),
            ("put", "providers/cloud", {"label": "x"}),
            ("delete", "providers/cloud", None),
            ("put", "providers/cloud/key", {"key": "another-key"}),
            ("delete", "providers/cloud/key", None),
            ("post", "providers/cloud/test", None),
            ("put", "roles/default", {"primary": None}),
            ("put", "prices", {"prices": []}),
            ("post", "try", {"prompt": "Hi"}),
        ):
            r = ready.request(method.upper(), f"/api/models/{path}", json=body, headers=hdrs)
            assert r.status_code == 403, (method, path)
    assert core.secrets.get("models.cloud.api_key") == KEY


def test_the_key_never_appears_in_any_response_event_or_log(ready, core, fakes, caplog):
    seen = []

    def call(method, path, **kwargs):
        r = ready.request(method, f"/api/models/{path}", **kwargs)
        seen.append(r.text)
        return r

    def echo(request):  # a provider that quotes the key back in its error
        import httpx
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(401, json={"error": {"message": f"Incorrect key: {KEY}"}})

    with caplog.at_level(logging.DEBUG):
        call("PUT", "providers/cloud/key", json={"key": KEY})
        call("POST", "try", json={"provider_id": "cloud", "model": "m", "prompt": "Hi"})
        core.service("models", None).transport.hosts[CLOUD] = echo
        call("POST", "try", json={"provider_id": "cloud", "model": "m", "prompt": "Hi"})
        call("POST", "providers/cloud/test")
        call("GET", "providers/cloud/models", params={"refresh": True})
        call("PUT", "providers/cloud", json={"label": "Renamed"})
        for path in ("providers", "roles", "health", "usage", "prices"):
            call("GET", path)
        seen.append(ready.get("/api/tape", params={"limit": 500}).text)
        seen.append(ready.get("/api/settings").text)
    assert "Incorrect key" in "".join(seen)  # the provider's words do get through, masked
    assert KEY not in "".join(seen)
    assert KEY not in json.dumps([e.model_dump() for e in core.tape.after(0, limit=1000)])
    assert KEY not in caplog.text
    for path in core.config.data.rglob("*"):
        if path.is_file() and path != core.config.secrets_file:
            assert KEY.encode() not in path.read_bytes(), path.name


# --- found on review ----------------------------------------------------------


def test_moving_a_provider_to_a_new_address_works_out_again_whether_it_is_local(ready, core):
    assert ready.get("/api/models/providers").json()["providers"][1]["local"] is True
    r = ready.put("/api/models/providers/home-server",
                  json={"base_url": "https://hosted.example.com/v1"})
    assert r.status_code == 200 and r.json()["provider"]["local"] is False
    # So a request that must stay home is not sent to the new, hosted address.
    ready.put("/api/models/roles/default",
              json={"primary": {"provider_id": "home-server", "model": "model-a"}})
    tried = ready.post("/api/models/try", json={"prompt": "Hi", "local_only": True}).json()
    assert (tried["ok"], tried["reason"]) == (False, "local_only")
    # The owner's own answer still wins when it is sent, and survives other changes.
    r = ready.put("/api/models/providers/home-server",
                  json={"base_url": "https://hosted.example.com/v2", "local": True})
    assert r.json()["provider"]["local"] is True
    r = ready.put("/api/models/providers/home-server",
                  json={"label": "Renamed", "base_url": "https://hosted.example.com/v2/"})
    assert r.json()["provider"]["local"] is True


@pytest.mark.parametrize("label", ["Two\nlines", "Bell\u0007", "Nul\u0000"])
def test_a_label_with_control_characters_is_refused(owner, label):
    r = owner.post("/api/models/providers", json=HOME_BODY | {"label": label})
    assert r.status_code == 422 and "cannot be saved" in r.json()["detail"]


def test_an_unknown_field_inside_a_role_or_a_price_line_is_refused_not_dropped(ready, core):
    r = ready.put("/api/models/roles/cheap", json={
        "primary": {"provider_id": "cloud", "model": "model-b", "efort": "low"}})
    assert r.status_code == 422 and "efort" in r.json()["detail"]
    r = ready.put("/api/models/prices", json={"prices": [
        {"id": "a", "provider": "*", "model": "*", "input_per_mtok": 1, "output_per_mtok": 1,
         "cached_per_mtok": 1}]})
    assert r.status_code == 422 and "cached_per_mtok" in r.json()["detail"]
    kinds = [e.kind for e in core.tape.after(0, limit=1000)]
    assert kinds.count("model.role.set") == 1 and "model.prices.set" not in kinds


# --- found on the phase 1 review ----------------------------------------------


def test_a_hosted_provider_with_no_key_is_reported_as_not_set_up_and_never_called(
        owner, core, fakes):
    fakes["anthropic.example.com"] = hosted = FakeProvider("anthropic")
    body = {"type": "anthropic", "label": "Hosted", "base_url": "https://anthropic.example.com"}
    made = owner.post("/api/models/providers", json=body).json()["provider"]
    sentence = "Hosted has no key yet. Add one under Settings, Models."
    assert (made["needs_key"], made["has_key"]) == (True, False)
    assert made["health"] == {"ok": None, "checked_ms": None, "error": sentence}
    reader = token(owner, core, ["read"])
    (entry,) = owner.get("/api/models/health", headers=reader).json()["providers"]
    assert (entry["ok"], entry["checked_ms"], entry["error"]) == (None, None, sentence)
    r = owner.get("/api/models/providers/hosted/models", headers=reader)
    assert (r.status_code, r.json()["detail"]) == (409, sentence)
    assert hosted.requests == []


def test_the_test_button_on_a_provider_with_no_key_says_to_add_one_and_calls_nobody(
        owner, fakes):
    fakes["anthropic.example.com"] = hosted = FakeProvider("anthropic")
    body = {"type": "anthropic", "label": "Hosted", "base_url": "https://anthropic.example.com"}
    assert owner.post("/api/models/providers", json=body).status_code == 201
    result = owner.post("/api/models/providers/hosted/test").json()
    assert result["ok"] is False and "has no key yet" in result["error"]
    assert hosted.requests == []


def test_a_test_that_has_to_send_one_word_shows_up_in_usage(ready, core, fakes):
    class NoList(FakeProvider):
        def __call__(self, request):
            self.mode = "missing" if request.method == "GET" else "ok"
            return super().__call__(request)

    core_brain = wire(core, fakes)
    core_brain.transport.hosts[CLOUD] = NoList("openai_compatible")
    result = ready.post("/api/models/providers/cloud/test").json()
    assert (result["ok"], result["models"]) == (True, None)
    (receipt,) = [e for e in core.tape.after(0) if e.kind == "model.called"]
    assert receipt.payload["caller"] == "models.test"
    assert receipt.payload["prompt_ref"] is None
    assert ready.get("/api/models/usage").json()["all_time"]["calls"] == 1


def test_a_changed_address_or_key_comes_back_unchecked_not_as_the_old_result(ready, fakes):
    ready.get("/api/models/health")
    fakes["moved.example.com"] = FakeProvider("openai_compatible")
    r = ready.put("/api/models/providers/cloud",
                  json={"base_url": "https://moved.example.com/v1"})
    assert r.json()["provider"]["health"] == {"ok": None, "checked_ms": None, "error": None}
    ready.get("/api/models/health")
    ready.put("/api/models/providers/cloud/key", json={"key": "sk-test-another-key-000"})
    by_id = {p["id"]: p for p in ready.get("/api/models/providers").json()["providers"]}
    assert by_id["cloud"]["health"]["ok"] is None and by_id["home-server"]["health"]["ok"]


@pytest.mark.parametrize("mode", ["down", "auth", "missing", "garbage"])
def test_a_read_token_cannot_make_a_failing_provider_be_asked_on_every_request(
        ready, core, fakes, mode):
    fakes[CLOUD].mode = mode
    headers = token(ready, core, ["read"])
    for _ in range(4):
        r = ready.get("/api/models/providers/cloud/models", headers=headers)
        assert r.status_code == 502 and KEY not in r.text
    # One request found out; the other three were told what it found.
    assert len(fakes[CLOUD].requests) == 1
    # The owner asking again is a fresh try.
    with pytest.raises(ModelError):
        asyncio.run(brain(core).list_models("cloud"))
    assert len(fakes[CLOUD].requests) == 2


def test_a_name_and_key_hidden_in_the_address_with_percent_signs_is_refused():
    import pytest

    from jimmyhq.models import validate

    with pytest.raises(validate.Invalid, match="Do not put a name or key"):
        validate.base_url("https://user%3Asecret%40models.example.com/v1")
    assert validate.base_url("https://models.example.com/v1/") == "https://models.example.com/v1"
