"""The MCP door as a client meets it: one address, a token, and the protocol."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from door_helpers import (
    ALL_TOOLS,
    HOST,
    META,
    MODERN,
    add_client,
    ask,
    http,
    modern,
    open_door,
    receipts,
    tape_text,
)
from jimmyhq import door as door_module
from jimmyhq.door import protocol
from jimmyhq.door.listener import DoorApp, wanted
from jimmyhq.hands import mcp
from jimmyhq.hands.provider import ProviderRecord, Wiring
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"


# ------------------------------------------------------------------ the listener


def test_the_door_is_off_until_the_owner_switches_it_on(core):
    d = door_module.door(core)
    assert wanted(d) is None
    _, token = add_client(core)
    assert ask(http(core), token, "tools/list").status_code == 404
    core.settings.set("door.enabled", True, actor="owner")
    assert wanted(d) == ("127.0.0.1", 8441)
    assert ask(http(core), token, "tools/list").status_code == 200
    core.settings.set("door.listen", "main_port", actor="owner")
    assert wanted(d) is None


def test_the_listener_answers_only_mcp(core):
    open_door(core)
    _, token = add_client(core)
    client = http(core)
    auth = {"Authorization": f"Bearer {token}"}
    for path in ("/", "/api/health", "/api/auth/state", "/api/door/status", "/api/tasks",
                 "/mcp/", "/mcp/extra", "/MCP", "/index.html",
                 "/.well-known/oauth-protected-resource"):
        for method in ("GET", "POST"):
            answer = client.request(method, path, headers=auth)
            assert answer.status_code == 404 and answer.content == b"", (method, path)


def test_the_door_module_never_imports_the_apps_routes():
    for name in ("listener", "protocol", "service", "tools", "shape", "messages"):
        text = Path(protocol.__file__).with_name(f"{name}.py").read_text(encoding="utf-8")
        assert "jimmyhq.web.app" not in text and "create_app" not in text, name


def test_the_main_address_serves_mcp_only_when_the_owner_mounts_it_there(core):
    _, token = add_client(core)
    with TestClient(create_app(core), base_url="http://testserver") as app:
        assert ask(app, token, "tools/list").status_code == 404
        core.settings.set("door.enabled", True, actor="owner")
        assert ask(app, token, "tools/list").status_code == 404
        core.settings.set("door.listen", "main_port", actor="owner")
        core.settings.set("door.allowed_hosts", ["testserver"], actor="owner")
        assert ask(app, token, "tools/list").status_code == 200
        assert ask(app, None, "tools/list").status_code == 401


def test_a_request_addressed_to_an_unknown_name_is_refused(core):
    open_door(core)
    _, token = add_client(core)
    client = http(core)
    assert ask(client, token, "tools/list", Host="evil.example.com").status_code == 421
    core.settings.set("door.allowed_hosts", ["door.example.com"], actor="owner")
    assert ask(client, token, "tools/list", Host="door.example.com").status_code == 200
    core.settings.set("door.allowed_hosts", [], actor="owner")
    core.settings.set("door.public_url", "https://tunnel.example.com/mcp", actor="owner")
    assert ask(client, token, "tools/list", Host="tunnel.example.com").status_code == 200


def test_a_request_from_a_web_page_is_refused_unless_the_page_is_listed(core):
    open_door(core)
    _, token = add_client(core)
    client = http(core)
    assert ask(client, token, "tools/list", Origin="https://page.example.com").status_code == 403
    assert ask(client, token, "tools/list", Origin=HOST).status_code == 403
    core.settings.set("door.allowed_origins", ["page.example.com"], actor="owner")
    assert ask(client, token, "tools/list", Origin="https://page.example.com").status_code == 200


# ------------------------------------------------------------------ tokens


def test_no_token_or_a_wrong_token_gets_401_with_no_detail_and_no_tool_list(core):
    open_door(core)
    _, token = add_client(core)
    client = http(core)
    for bad in (None, "", "nonsense", token + "x", token[:-1], "jhqd_" + "a" * 43,
                "x" * 5000):
        answer = ask(client, bad, "tools/list")
        assert answer.status_code == 401 and answer.content == b"", bad
        assert answer.headers["www-authenticate"] == "Bearer"
    for method in ("GET", "DELETE", "PUT"):
        assert client.request(method, "/mcp").status_code == 401
    assert receipts(core) == []  # a stranger's knocking writes nothing


def test_a_door_token_does_not_open_the_api_and_an_api_token_does_not_open_the_door(core):
    open_door(core)
    _, token = add_client(core)
    _, api_token = core.auth.create_token("script", ["read", "write"])
    assert core.auth.check_token(token) is None
    assert core.auth.check_door_token(api_token) is None
    assert ask(http(core), api_token, "tools/list").status_code == 401
    with TestClient(create_app(core), base_url="http://testserver") as app:
        for path in ("/api/tasks", "/api/door/status", "/api/settings"):
            assert app.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_a_revoked_token_and_a_rotated_one_stop_working_at_once(core):
    d = open_door(core)
    client, token = add_client(core)
    web = http(core)
    assert ask(web, token, "tools/list").status_code == 200
    new = d.rotate_client(client["id"], by="owner")
    assert ask(web, token, "tools/list").status_code == 401
    assert ask(web, new, "tools/list").status_code == 200
    d.revoke_client(client["id"], by="owner")
    assert ask(web, new, "tools/list").status_code == 401


def test_a_token_is_long_never_stored_and_never_on_the_tape_or_in_a_log(core, caplog):
    open_door(core)
    with caplog.at_level("DEBUG"):
        client, token = add_client(core)
        ask(http(core), token, "tools/call", {"name": "whoami", "arguments": {}})
        ask(http(core), token + "x", "tools/list")
    assert token.startswith("jhqd_") and len(token) >= 40
    assert token not in tape_text(core) and token[5:] not in tape_text(core)
    assert token not in caplog.text
    raw = core.auth._conn.execute("SELECT * FROM door_clients").fetchall()
    assert all(token not in str(tuple(row)) for row in raw)
    assert token not in json.dumps(door_module.door(core).clients())


def test_wrong_tokens_from_one_address_are_throttled_and_a_right_one_still_gets_in(core):
    d = open_door(core)
    core.settings.set("door.failed_tries", 3, actor="owner")
    _, token = add_client(core)
    client = http(core)
    for _ in range(3):
        assert ask(client, "wrong", "tools/list").status_code == 401
    turned_away = ask(client, "wrong", "tools/list")
    assert turned_away.status_code == 429 and turned_away.content == b""
    assert ask(client, token, "tools/list").status_code == 200
    assert d.failures.recent() == 3
    d.clock = lambda: 10_000_000.0  # sixteen minutes and more later
    assert ask(client, "wrong", "tools/list").status_code == 401


def test_wrong_tokens_through_a_tunnel_are_counted_apart_from_this_computers_own(core):
    open_door(core)
    core.settings.set("door.failed_tries", 3, actor="owner")
    core.settings.set("door.allowed_hosts", ["door.example.com"], actor="owner")
    _, token = add_client(core)
    door_app = DoorApp(door_module.door(core))
    loopback = ("127.0.0.1", 5000)
    tunnel = TestClient(door_app, base_url="http://door.example.com", client=loopback,
                        headers={"x-forwarded-for": "198.51.100.7"})
    here = TestClient(door_app, base_url=HOST, client=loopback)
    for _ in range(3):
        assert ask(tunnel, "wrong", "tools/list").status_code == 401
    assert ask(tunnel, "wrong", "tools/list").status_code == 429
    assert ask(here, "wrong", "tools/list").status_code == 401
    assert ask(tunnel, token, "tools/list").status_code == 200  # a right token always passes


def test_wrong_tokens_from_ever_new_addresses_meet_a_ceiling_and_a_right_token_still_gets_in(
        core):
    d = open_door(core)
    core.settings.set("door.failed_tries", 3, actor="owner")
    core.settings.set("door.failed_tries_all", 20, actor="owner")
    core.settings.set("door.allowed_hosts", ["door.example.com"], actor="owner")
    _, token = add_client(core)
    door_app = DoorApp(d)
    codes = []
    for i in range(40):  # a fresh far address every time, as a rotating caller would be
        with TestClient(door_app, base_url="http://door.example.com", client=("127.0.0.1", 5000),
                        headers={"x-forwarded-for": f"198.51.100.{i}"}) as far:
            codes.append(ask(far, "wrong", "tools/list").status_code)
    assert codes[:20] == [401] * 20 and set(codes[20:]) == {429}
    with TestClient(door_app, base_url="http://door.example.com", client=("127.0.0.1", 5000),
                    headers={"x-forwarded-for": "198.51.100.250"}) as far:
        assert ask(far, token, "tools/list").status_code == 200  # a right token always passes
    d.clock = lambda: 10_000_000.0  # sixteen minutes and more later
    with TestClient(door_app, base_url="http://door.example.com", client=("127.0.0.1", 5000),
                    headers={"x-forwarded-for": "198.51.100.251"}) as far:
        assert ask(far, "wrong", "tools/list").status_code == 401


def test_an_address_cannot_push_its_own_block_out_of_memory_with_many_others():
    from jimmyhq.door import service

    failures = service.Failures(lambda: 100.0)
    for _ in range(3):
        failures.record("forwarded:one")
    assert failures.blocked("forwarded:one", 3, 100)
    # A caller with endless fresh addresses, each asking first as the door does.
    for i in range(service.MAX_SOURCES + 500):
        if not failures.blocked(f"forwarded:other-{i}", 3, 100):
            failures.record(f"forwarded:other-{i}")
    assert failures.blocked("forwarded:one", 3)  # its own count, the ceiling aside
    assert failures.recent() == 100


def test_the_door_answers_its_own_names_only_to_a_request_nothing_relayed(core):
    open_door(core)
    _, token = add_client(core)
    # The owner listed them: still never accepted through a tunnel.
    core.settings.set("door.allowed_hosts", ["localhost", "127.0.0.1", "door.example.com"],
                      actor="owner")
    door_app = DoorApp(door_module.door(core))
    loopback = ("127.0.0.1", 5000)
    for name in ("localhost", "127.0.0.1", "[::1]"):
        with TestClient(door_app, base_url=f"http://{name}:8441", client=loopback) as here:
            assert ask(here, token, "tools/list").status_code == 200, name
        for header in ("x-forwarded-for", "forwarded", "x-real-ip"):
            with TestClient(door_app, base_url=f"http://{name}:8441", client=loopback,
                            headers={header: "198.51.100.7"}) as relayed:
                assert ask(relayed, token, "tools/list").status_code == 421, (name, header)
    with TestClient(door_app, base_url="http://door.example.com", client=loopback,
                    headers={"x-forwarded-for": "198.51.100.7"}) as tunnel:
        assert ask(tunnel, token, "tools/list").status_code == 200


def test_the_doors_own_port_refuses_the_public_internet_unless_config_allows_it(core,
                                                                              monkeypatch):
    open_door(core)
    _, token = add_client(core)
    core.settings.set("door.allowed_hosts", ["door.example.com"], actor="owner")
    funnel = {"x-forwarded-for": "203.0.113.9", "Tailscale-Funnel-Request": "?1"}
    with TestClient(DoorApp(door_module.door(core)), base_url="http://door.example.com",
                    client=("127.0.0.1", 5000), headers=funnel) as public:
        refused = ask(public, token, "tools/list")
        assert refused.status_code == 403
        said = refused.json()["error"]["message"]
        assert "tailscale funnel --https=8441 off" in said and "allow_public" in said
        assert ask(public, None, "tools/list").status_code == 403  # before any token is read
    monkeypatch.setattr(core, "config", dataclasses.replace(core.config, allow_public=True))
    with TestClient(DoorApp(door_module.door(core)), base_url="http://door.example.com",
                    client=("127.0.0.1", 5000), headers=funnel) as public:
        assert ask(public, token, "tools/list").status_code == 200


def test_the_door_through_the_owners_tailnet_mapping_keeps_working(core):
    """JimmyHQ shared on the tailnet with `tailscale serve`, the door on its address at
    /mcp and the tailnet name in the allowed hosts: the owner's normal set-up."""
    tailnet = "box.example-tailnet.example"
    core.settings.set("access.allowed_hosts", [tailnet], actor="owner")
    open_door(core)
    core.settings.set("door.listen", "main_port", actor="owner")
    core.settings.set("door.public_url", f"https://{tailnet}/mcp", actor="owner")
    _, token = add_client(core)
    relayed = {"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https"}
    with TestClient(create_app(core), base_url=f"http://{tailnet}", client=("127.0.0.1", 5000),
                    headers=relayed) as tunnel:
        assert ask(tunnel, token, "tools/list").status_code == 200
        assert ask(tunnel, "wrong", "tools/list").status_code == 401
    # And on the door's own port, mapped the same way.
    core.settings.set("door.listen", "own_port", actor="owner")
    core.settings.set("door.allowed_hosts", [tailnet], actor="owner")
    with TestClient(DoorApp(door_module.door(core)), base_url=f"http://{tailnet}",
                    client=("127.0.0.1", 5000), headers=relayed) as tunnel:
        assert ask(tunnel, token, "tools/list").status_code == 200


# ------------------------------------------------------------------ the protocol


def test_the_newest_revision_needs_no_handshake_and_discovery_says_what_is_spoken(core):
    open_door(core)
    _, token = add_client(core)
    client = http(core)
    found = ask(client, token, "server/discover").json()["result"]
    assert found["supportedVersions"][0] == MODERN and "2025-11-25" in found["supportedVersions"]
    assert found["capabilities"] == {"tools": {"listChanged": False}}
    assert found["_meta"][META + "serverInfo"]["name"] == "jimmyhq-door"
    assert found["resultType"] == "complete" and found["cacheScope"] == "private"
    listed = ask(client, token, "tools/list")
    assert listed.headers["content-type"] == "application/json"
    assert "mcp-session-id" not in listed.headers
    result = listed.json()["result"]
    assert result["cacheScope"] == "private" and result["ttlMs"] == 0
    assert [t["name"] for t in result["tools"]] == ["whoami", *ALL_TOOLS]
    assert ask(client, token, "ping").json()["result"] == {"resultType": "complete"}


def test_a_header_that_disagrees_with_the_body_is_refused_before_anything_runs(core):
    open_door(core)
    _, token = add_client(core)
    client = http(core)
    auth = {"Authorization": f"Bearer {token}"}
    headers, body = modern("tools/call", {"name": "whoami", "arguments": {}})
    for change in ({"Mcp-Method": "tools/list"}, {"Mcp-Name": "tasks_add"},
                   {"MCP-Protocol-Version": "2025-11-25"}):
        answer = client.post("/mcp", json=body, headers=headers | auth | change)
        assert answer.status_code == 400 and answer.json()["error"]["code"] == -32020, change
    for missing in ("Mcp-Method", "Mcp-Name", "MCP-Protocol-Version"):
        sent = {k: v for k, v in (headers | auth).items() if k != missing}
        answer = client.post("/mcp", json=body, headers=sent)
        assert answer.status_code == 400 and answer.json()["error"]["code"] == -32020, missing
    assert receipts(core) == []
    encoded = headers | auth | {"Mcp-Name": "=?base64?d2hvYW1p?="}
    assert client.post("/mcp", json=body, headers=encoded).status_code == 200


def test_an_unknown_revision_is_refused_with_the_ones_that_are_known(core):
    open_door(core)
    _, token = add_client(core)
    headers, body = modern("tools/list")
    headers["MCP-Protocol-Version"] = "1900-01-01"
    body["params"]["_meta"][META + "protocolVersion"] = "1900-01-01"
    answer = http(core).post("/mcp", json=body,
                             headers=headers | {"Authorization": f"Bearer {token}"})
    assert answer.status_code == 400
    error = answer.json()["error"]
    assert error["code"] == -32022 and error["data"]["supported"][0] == MODERN
    assert error["data"]["requested"] == "1900-01-01"


def test_the_older_handshake_works_and_gives_out_no_session(core):
    open_door(core)
    _, token = add_client(core, tools=("tasks_list",))
    client = http(core)
    auth = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}

    def post(message, **headers):
        return client.post("/mcp", json=message, headers=auth | headers)

    hello = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "old", "version": "1"}}})
    assert hello.status_code == 200 and "mcp-session-id" not in hello.headers
    result = hello.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "jimmyhq-door" and "tools" in result["capabilities"]
    newer = post({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                  "params": {"protocolVersion": "2030-01-01", "capabilities": {}}})
    assert newer.json()["result"]["protocolVersion"] == "2025-11-25"
    done = post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert done.status_code == 202 and done.content == b""
    version = {"MCP-Protocol-Version": "2025-06-18"}
    listed = post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, **version).json()
    assert [t["name"] for t in listed["result"]["tools"]] == ["whoami", "tasks_list"]
    assert "resultType" not in listed["result"]
    called = post({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "whoami", "arguments": {}}}, **version).json()
    assert called["result"]["isError"] is False
    assert called["result"]["structuredContent"]["client"] == "helper"
    assert post({"jsonrpc": "2.0", "id": 5, "method": "ping"}).json()["result"] == {}
    unknown = post({"jsonrpc": "2.0", "id": 6, "method": "resources/list"}, **version)
    assert unknown.status_code == 200 and unknown.json()["error"]["code"] == -32601


def test_only_tools_are_offered_and_get_and_delete_are_not_allowed(core):
    open_door(core)
    _, token = add_client(core)
    client = http(core)
    for method in ("resources/list", "prompts/list", "sampling/createMessage",
                   "subscriptions/listen", "initialize2"):
        answer = ask(client, token, method)
        assert answer.status_code == 404 and answer.json()["error"]["code"] == -32601, method
    auth = {"Authorization": f"Bearer {token}"}
    for method in ("GET", "DELETE", "PUT"):
        answer = client.request(method, "/mcp", headers=auth)
        assert answer.status_code == 405 and answer.headers["allow"] == "POST"


def test_oversize_and_malformed_requests_are_refused_without_running_anything(core):
    open_door(core)
    core.settings.set("door.max_request_bytes", 2000, actor="owner")
    _, token = add_client(core)
    client = http(core)
    headers, body = modern("tools/call", {"name": "tasks_add",
                                          "arguments": {"title": "x" * 5000}})
    auth = headers | {"Authorization": f"Bearer {token}"}
    big = client.post("/mcp", json=body, headers=auth)
    assert big.status_code == 413 and "2000 bytes" in big.json()["error"]["message"]

    def raw(content, kind="application/json"):
        return client.post("/mcp", content=content, headers=auth | {"Content-Type": kind})

    assert raw(b"{not json").json()["error"]["code"] == -32700
    assert raw(b"[" * 1500).status_code == 400
    assert raw(b"[]").json()["error"]["code"] == -32600  # no batches
    assert raw(b'"text"').json()["error"]["code"] == -32600
    assert raw(b'{"jsonrpc":"1.0","id":1,"method":"ping"}').json()["error"]["code"] == -32600
    assert raw(b'{"jsonrpc":"2.0","id":{"a":1},"method":"ping"}').status_code == 400
    assert raw(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":[1]}').status_code == 400
    assert raw(b'{"jsonrpc":"2.0","id":1,"method":"ping"}', "text/plain").status_code == 415
    assert client.post("/mcp", json=body, headers=auth | {"Accept": "text/html"}
                       ).status_code == 406
    no_name = dict(body, params={"_meta": body["params"]["_meta"], "arguments": {}})
    sent = {k: v for k, v in auth.items() if k != "Mcp-Name"}
    assert client.post("/mcp", json=no_name, headers=sent).status_code == 400
    assert receipts(core) == [] and "xxxxx" not in tape_text(core)


def test_a_client_asking_too_fast_is_told_to_slow_down_and_it_is_recorded_once(core):
    d = open_door(core)
    client, token = add_client(core, calls_per_minute=3)
    web = http(core)
    now = [1000.0]
    d.clock = lambda: now[0]
    for _ in range(3):
        assert ask(web, token, "tools/list").status_code == 200
    for _ in range(4):
        slow = ask(web, token, "tools/list")
        assert slow.status_code == 429 and slow.headers["retry-after"] == "60"
        assert "Nothing happened" in slow.json()["error"]["message"]
    assert [(r["rule"], r["ok"]) for r in receipts(core)] == [("door_rate_limit", False)]
    now[0] += 61
    assert ask(web, token, "tools/list").status_code == 200


# ------------------------------------------------------------------ this repo's own client


def adapter(core, token, **options):
    transport = httpx.ASGITransport(app=DoorApp(door_module.door(core)))
    record = ProviderRecord(id="door-test", type="mcp", label="The door",
                            base_url="https://door.example.com/mcp", options=options)
    return mcp.make(record, Wiring(key=token, secret={}.get, http=transport))


def data(outcome) -> dict:
    return outcome.data if isinstance(outcome.data, dict) else json.loads(outcome.data)


def test_this_repos_own_mcp_client_lists_and_calls_the_doors_tools(core, monkeypatch):
    monkeypatch.setattr(mcp, "PROBE_WAIT_S", 0.5)
    open_door(core)
    core.settings.set("door.allowed_hosts", ["door.example.com"], actor="owner")
    _, token = add_client(core, tools=("tasks_list", "tasks_add"))
    try:
        hands = adapter(core, token)
        account = asyncio.run(hands.accounts())[0]
        actions = asyncio.run(hands.actions(account))
        assert [a.name for a in actions] == ["whoami", "tasks_list", "tasks_add"]
        effects = {a.name: a.effect for a in actions}
        assert effects["whoami"] == "read" and effects["tasks_add"] != "read"
        added = asyncio.run(hands.run(account, "tasks_add", {"title": "Water the plants"},
                                      key="k1"))
        assert added.ok and data(added)["added"] is True
        listed = asyncio.run(hands.run(account, "tasks_list", {}, key="k2"))
        assert [t["title"] for t in data(listed)["tasks"]] == ["Water the plants"]
        session = mcp.SESSIONS["door-test"]
        assert session.era == mcp.MODERN_ERA and session.session_id is None
        refused = asyncio.run(hands.run(account, "bench_file_card",
                                        {"kind": "request", "title": "x"}, key="k3"))
        assert not refused.ok
        assert asyncio.run(adapter(core, token + "x").check()).ok is False
    finally:
        mcp.close_all()
    assert [e.actor for e in core.tape.after(0, kind="task.filed")] == ["client:helper"]


def test_an_older_client_is_served_by_the_same_address(core, monkeypatch):
    """The repo's client, held to the older handshake, still gets its answer."""
    monkeypatch.setattr(mcp, "PROBE_WAIT_S", 0.5)
    monkeypatch.setattr(mcp, "MODERN", "2031-01-01")  # a revision the door does not know
    open_door(core)
    core.settings.set("door.allowed_hosts", ["door.example.com"], actor="owner")
    _, token = add_client(core, tools=())
    try:
        hands = adapter(core, token)
        account = asyncio.run(hands.accounts())[0]
        out = asyncio.run(hands.run(account, "whoami", {}, key="k"))
        assert out.ok and data(out)["client"] == "helper"
        assert mcp.SESSIONS["door-test"].era == mcp.LEGACY_ERA
    finally:
        mcp.close_all()


@pytest.mark.parametrize(("method", "header", "meta", "era"), [
    ("initialize", None, None, "legacy"), ("tools/list", None, None, "legacy"),
    ("tools/list", "2025-03-26", None, "legacy"), ("tools/list", MODERN, MODERN, "modern"),
    ("tools/list", MODERN, None, None), ("tools/list", None, MODERN, None),
    ("tools/list", "2024-11-05", None, None), ("tools/list", MODERN, "2025-11-25", None),
])
def test_the_era_of_a_request_is_read_from_its_header_and_its_body(method, header, meta, era):
    assert protocol.era_of(method, header, meta)[0] == era


# ------------------------------------------------------------------ following the settings


def test_the_listener_follows_the_settings_without_a_restart(core, monkeypatch):
    from jimmyhq.door.listener import Listener

    d = door_module.door(core)
    listener = Listener(d)
    moves: list = []

    def up(at):
        listener._at, listener._serving = at, SimpleNamespace(is_alive=lambda: True, join=id)
        moves.append(("up", at))

    def down():
        if listener._serving is not None:
            moves.append(("down", listener._at))
        listener._at = listener._serving = None

    monkeypatch.setattr(listener, "_up", up)
    monkeypatch.setattr(listener, "_down", down)
    listener.sync()
    assert moves == []  # off by default: no port is opened
    core.settings.set("door.enabled", True, actor="owner")
    listener.sync()
    listener.sync()
    assert moves == [("up", ("127.0.0.1", 8441))]
    core.settings.set("door.port", 9100, actor="owner")
    listener.sync()
    assert moves[1:] == [("down", ("127.0.0.1", 8441)), ("up", ("127.0.0.1", 9100))]
    core.settings.set("door.enabled", False, actor="owner")
    listener.sync()
    assert moves[-1] == ("down", ("127.0.0.1", 9100)) and len(moves) == 4
