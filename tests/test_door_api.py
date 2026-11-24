"""The owner's API for the door: who may use it, and what each route answers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from door_helpers import ask, http
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
READS = ["/api/door/status", "/api/door/clients", "/api/door/calls"]


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


def test_every_door_route_is_for_the_signed_in_owner_only(core, owner):
    made = owner.post("/api/door/clients", json={"name": "helper"}).json()
    cid = made["client"]["id"]
    _, api_token = core.auth.create_token("script", ["read", "write"])
    writes = [("POST", "/api/door/clients", {"name": "second"}),
              ("PUT", f"/api/door/clients/{cid}", {"about": "x"}),
              ("POST", f"/api/door/clients/{cid}/rotate", None),
              ("POST", f"/api/door/clients/{cid}/revoke", None)]
    with TestClient(create_app(core), base_url="http://testserver") as stranger:
        for path in READS:
            assert stranger.get(path).status_code == 401, path
            for token in (api_token, made["token"]):
                answer = stranger.get(path, headers={"Authorization": f"Bearer {token}"})
                assert answer.status_code in (401, 403), path
        for method, path, body in writes:
            assert stranger.request(method, path, json=body).status_code == 401, path
            answer = stranger.request(method, path, json=body,
                                      headers={"Authorization": f"Bearer {api_token}"})
            assert answer.status_code == 403, path
    assert len(owner.get("/api/door/clients").json()) == 1


def test_status_says_whether_the_door_is_on_and_the_exact_address_to_give(core, owner):
    status = owner.get("/api/door/status").json()
    assert status["enabled"] is False and status["listening"] is False
    assert status["address"] == "http://127.0.0.1:8441/mcp" and status["public"] is None
    assert [t["name"] for t in status["tools"]][0] == "whoami"
    assert all({"name", "title", "description", "write", "per_day"} <= set(t)
               for t in status["tools"])
    owner.put("/api/settings/door.public_url", json={"value": "https://door.example.com/mcp"})
    owner.put("/api/settings/door.port", json={"value": 9000})
    status = owner.get("/api/door/status").json()
    assert status["address"] == "https://door.example.com/mcp"
    assert status["local_address"] == "http://127.0.0.1:9000/mcp"
    owner.put("/api/settings/door.listen", json={"value": "main_port"})
    owner.put("/api/settings/door.enabled", json={"value": True})
    status = owner.get("/api/door/status").json()
    assert status["local_address"] == "http://127.0.0.1:0/mcp" and status["listening"] is True


def test_the_doors_settings_refuse_values_that_make_no_sense(owner):
    for key, value in (("door.public_url", "door.example.com"),
                       ("door.public_url", "https://user:pw@" + "door.example.com/mcp"),
                       ("door.host", "not a host"), ("door.port", 80),
                       ("door.allowed_hosts", ["https://door.example.com/mcp"]),
                       ("door.listen", "everywhere"), ("door.limit.tasks_add", -1)):
        assert owner.put(f"/api/settings/{key}", json={"value": value}).status_code == 422, key


def test_a_new_client_gets_its_token_once_and_starts_with_no_tools(core, owner):
    made = owner.post("/api/door/clients",
                      json={"name": "Helper", "about": "An assistant elsewhere."})
    assert made.status_code == 200
    body = made.json()
    assert body["token"].startswith("jhqd_") and body["address"].endswith("/mcp")
    assert body["client"]["name"] == "helper" and body["client"]["tools"] == {}
    assert body["client"]["about"] == "An assistant elsewhere."
    listed = owner.get("/api/door/clients")
    assert body["token"] not in listed.text and "token" not in listed.json()[0]
    assert body["token"] not in owner.get("/api/door/status").text
    for bad in ({"name": "helper"}, {"name": "Has Space"}, {"name": ""}, {},
                {"name": "ok", "extra": 1}):
        assert owner.post("/api/door/clients", json=bad).status_code == 422, bad


def test_the_owner_changes_tools_and_limits_rotates_and_revokes(core, owner):
    owner.put("/api/settings/door.enabled", json={"value": True})
    made = owner.post("/api/door/clients", json={"name": "helper"}).json()
    cid, token = made["client"]["id"], made["token"]
    door_side = http(core)
    names = lambda t: [x["name"] for x in ask(door_side, t, "tools/list").json()["result"]["tools"]]  # noqa: E731
    assert names(token) == ["whoami"]
    changed = owner.put(f"/api/door/clients/{cid}",
                        json={"tools": {"tasks_list": None, "tasks_add": 3},
                              "options": {"task_areas": ["Home"], "calls_per_minute": 10}})
    assert changed.status_code == 200
    assert changed.json()["tools"] == {"tasks_list": None, "tasks_add": 3}
    assert changed.json()["options"] == {"bench_bodies": False, "addresses": False,
                                         "task_areas": ["home"], "calls_per_minute": 10,
                                         "messages_need_approval": False}
    assert names(token) == ["whoami", "tasks_list", "tasks_add"]
    kept = owner.put(f"/api/door/clients/{cid}", json={"options": {
        "bench_bodies": True, "messages_need_approval": True}}).json()
    assert kept["options"]["task_areas"] == ["home"] and kept["options"]["bench_bodies"] is True
    assert kept["options"]["messages_need_approval"] is True
    assert owner.put(f"/api/door/clients/{cid}",
                     json={"tools": {"format_disk": None}}).status_code == 422
    assert owner.put("/api/door/clients/nobody", json={"about": "x"}).status_code == 404

    rotated = owner.post(f"/api/door/clients/{cid}/rotate").json()
    assert rotated["token"] != token and rotated["client"]["rotated"]
    assert ask(door_side, token, "tools/list").status_code == 401
    assert names(rotated["token"]) == ["whoami", "tasks_list", "tasks_add"]

    revoked = owner.post(f"/api/door/clients/{cid}/revoke").json()
    assert revoked["revoked"]
    assert ask(door_side, rotated["token"], "tools/list").status_code == 401
    assert owner.post(f"/api/door/clients/{cid}/rotate").status_code == 409
    assert owner.put(f"/api/door/clients/{cid}", json={"about": "x"}).status_code == 409


def test_a_clients_activity_is_listable_newest_first(core, owner):
    owner.put("/api/settings/door.enabled", json={"value": True})
    first = owner.post("/api/door/clients", json={"name": "helper"}).json()
    second = owner.post("/api/door/clients", json={"name": "rival"}).json()
    owner.put(f"/api/door/clients/{first['client']['id']}", json={"tools": {"tasks_add": None}})
    door_side = http(core)
    ask(door_side, first["token"], "tools/call",
        {"name": "tasks_add", "arguments": {"title": "Buy stamps"}})
    ask(door_side, first["token"], "tools/call", {"name": "whoami", "arguments": {}})
    ask(door_side, second["token"], "tools/call", {"name": "tasks_add", "arguments": {}})
    calls = owner.get("/api/door/calls").json()["calls"]
    assert [(c["client"], c["tool"], c["ok"]) for c in calls] == [
        ("rival", "tasks_add", False), ("helper", "whoami", True), ("helper", "tasks_add", True)]
    assert calls[0]["rule"] == "door_tool_not_allowed" and calls[2]["created"].startswith("task:")
    assert "Buy stamps" not in owner.get("/api/door/calls").text
    mine = owner.get("/api/door/calls", params={"client_id": first["client"]["id"],
                                                "limit": 1}).json()["calls"]
    assert [(c["client"], c["tool"]) for c in mine] == [("helper", "whoami")]
    listed = {c["name"]: c for c in owner.get("/api/door/clients").json()}
    assert listed["helper"]["used_today"] == {"whoami": 1, "tasks_add": 1}
    assert listed["helper"]["last_call"]["tool"] == "whoami"
    assert listed["rival"]["used_today"] == {"whoami": 0}
