"""The bridge route inside the real web app: who may relay a call, and what comes back."""

from __future__ import annotations

import json

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import jimmyhq.apps
from jimmyhq.apps import appdata, bridge, files
from jimmyhq.web.app import create_app
from test_apps_bridge_fakes import BOOKS, WATER, installs

PASSWORD = "correct horse battery"
URL = "/api/apps/water/bridge/storage.set"
SET = {"jimmyhq": 1, "id": 1, "call": "storage.set", "args": {"key": "k", "value": "v"}}


@pytest.fixture
def apps(monkeypatch, tmp_path):
    return installs(monkeypatch, tmp_path)


@pytest.fixture
def client(core, monkeypatch):
    def build(core_):
        router = APIRouter()
        router.include_router(bridge.build_router(core_))
        router.include_router(files.build_router(core_))
        return router

    monkeypatch.setattr(jimmyhq.apps, "build", build, raising=False)
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    return client


def stored(core, app_id="water", key="k"):
    return appdata.open_data(core, app_id).get(key)


def test_a_call_relayed_from_the_owners_session_is_carried_out(owner, core, apps):
    apps.add(WATER)
    answer = owner.post(URL, json=SET)
    assert answer.status_code == 200
    assert answer.json() == {"id": 1, "ok": True, "result": None}
    assert stored(core) == "v"


def test_nobody_relays_a_call_without_the_owners_session(client, core, apps):
    apps.add(WATER)
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    client.cookies.clear()
    assert client.post(URL, json=SET).status_code == 401
    assert stored(core) is None


def test_a_token_however_wide_cannot_relay_a_call(client, core, apps):
    apps.add(WATER)
    secret = core.auth.create_token("automation", ["read", "write"])[1]
    answer = client.post(URL, json=SET, headers={"authorization": f"Bearer {secret}"})
    assert answer.status_code == 403
    assert stored(core) is None


@pytest.mark.parametrize("headers", [
    {"origin": "null"},  # what a sandboxed frame's own request says
    {"origin": "https://evil.example"},
    {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"},
    {"sec-fetch-dest": "iframe"},
    {"sec-fetch-dest": "document"},
])
def test_a_request_that_did_not_come_from_jimmyhqs_own_pages_is_refused(
        owner, core, apps, headers):
    apps.add(WATER)
    answer = owner.post(URL, json=SET, headers=headers)
    assert answer.status_code == 403
    assert stored(core) is None


def test_the_shells_own_fetch_headers_are_accepted(owner, core, apps):
    apps.add(WATER)
    answer = owner.post(URL, json=SET, headers={
        "origin": "http://testserver", "sec-fetch-site": "same-origin",
        "sec-fetch-dest": "empty", "sec-fetch-mode": "cors"})
    assert answer.json()["ok"] is True


def test_the_app_is_the_one_in_the_address_whatever_the_page_sends(owner, core, apps):
    apps.add(WATER)
    apps.add(BOOKS)
    for extra in ({"app": "books_read"}, {"app_id": "books_read"}, {"actor": "app:books_read"}):
        answer = owner.post(URL, json={**SET, **extra}).json()
        assert answer["ok"] is False and answer["error"]["code"] == "invalid"
    answer = owner.post(URL, json={**SET, "args": {"key": "k", "value": "v",
                                                  "app": "books_read"}}).json()
    assert answer["error"]["code"] == "invalid"
    assert stored(core) is None and stored(core, "books_read") is None
    owner.post(URL, json=SET)
    assert stored(core) == "v" and stored(core, "books_read") is None


def test_a_refusal_is_an_answer_with_the_designs_shape(owner, core, apps):
    apps.add(WATER, granted=())
    answer = owner.post("/api/apps/water/bridge/tasks.list", json={"id": "x", "args": {}})
    assert answer.status_code == 200
    body = answer.json()
    assert body["id"] == "x" and body["ok"] is False
    assert body["error"]["code"] == "not_allowed" and "tasks.read" in body["error"]["message"]
    missing = owner.post("/api/apps/nobody/bridge/storage.get", json={"args": {"key": "k"}})
    assert missing.json()["error"]["code"] == "not_found"
    unknown = owner.post("/api/apps/water/bridge/bench.decide", json={"args": {}})
    assert unknown.json()["error"]["code"] == "invalid"


def test_an_oversized_request_is_refused_before_it_is_read(owner, core, apps):
    apps.add(WATER)
    core.settings.set("apps.quota.value_kb", 4, actor="owner")
    big = {**SET, "args": {"key": "k", "value": "x" * 200_000}}
    answer = owner.post(URL, json=big).json()
    assert answer["ok"] is False and answer["error"]["code"] == "too_large"
    assert "at most 20 KB" in answer["error"]["message"]

    def pieces():
        yield json.dumps(big).encode()

    streamed = owner.post(URL, content=pieces(), headers={"content-type": "application/json"})
    assert streamed.json()["error"]["code"] == "too_large"
    assert stored(core) is None


def test_a_body_that_is_not_json_is_refused_as_an_answer(owner, core, apps):
    apps.add(WATER)
    answer = owner.post(URL, content=b"{nope", headers={"content-type": "application/json"})
    assert answer.status_code == 200 and answer.json()["error"]["code"] == "invalid"


@pytest.mark.parametrize("method", ["get", "put", "delete", "patch"])
def test_only_a_post_reaches_the_bridge(owner, core, apps, method):
    apps.add(WATER)
    answer = getattr(owner, method)(URL)
    assert answer.status_code in (404, 405)
    assert stored(core) is None


@pytest.mark.parametrize("path", [
    "/api/apps/..%2Fwater/bridge/storage.set", "/api/apps/water%00/bridge/storage.set",
    "/api/apps/WATER/bridge/storage.set", "/api/apps/water/bridge/storage.set%2F..%2Fx",
    "/api/apps/books_read%2F..%2Fwater/bridge/storage.set",
])
def test_an_address_bent_out_of_shape_reaches_no_app(owner, core, apps, path):
    apps.add(WATER)
    answer = owner.post(path, json=SET)
    assert answer.status_code == 404 or answer.json()["ok"] is False
    assert stored(core) is None
