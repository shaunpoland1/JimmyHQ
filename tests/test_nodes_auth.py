"""Who can reach what. A node token is a fourth kind of credential: it opens the
node link and nothing else, and nothing else opens the node link."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from door_helpers import add_client, modern, open_door
from jimmyhq import nodes, supervisor
from jimmyhq.auth import AuthError
from jimmyhq.ids import now_ms
from jimmyhq.nodes import protocol
from jimmyhq.nodes.service import RELAY_SECRET
from jimmyhq.web.app import PROCESSES, create_app

PASSWORD = "correct horse battery"
BEAT = {"version": "0.1.0", "protocol": protocol.PROTOCOL, "models": ["big"]}
OWNER_ROUTES = [("GET", "/api/nodes"), ("POST", "/api/nodes/join-codes"),
                ("DELETE", "/api/nodes/01JEXAMPLE0000000000000000")]
LINK_ROUTES = [("POST", f"{protocol.LINK}/heartbeat"), ("GET", f"{protocol.LINK}/jobs?wait=0"),
               ("POST", f"{protocol.LINK}/jobs/x/reply"), ("POST", f"{protocol.LINK}/leave")]


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


@pytest.fixture
def stranger(core, owner):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        yield client


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def join(client, code, name="example-box"):
    return client.post(protocol.JOIN, json={"code": code, "name": name, "version": "0.1.0",
                                            "protocol": protocol.PROTOCOL})


def joined(owner, stranger, name="example-box"):
    """A node that has joined: (node id, its token)."""
    code = owner.post("/api/nodes/join-codes").json()["code"]
    body = join(stranger, code, name).json()
    return body["node_id"], body["token"]


def test_a_join_code_is_shown_once_and_kept_only_as_a_digest(core, owner):
    made = owner.post("/api/nodes/join-codes")
    assert made.status_code == 201
    body = made.json()
    assert len(body["code"]) == 11 and body["code"][5] == "-" and body["minutes"] == 10
    assert now_ms() < body["expires_ms"] <= now_ms() + 10 * 60_000
    plain = body["code"].replace("-", "")
    for path in core.config.data.iterdir():
        if path.is_file():
            assert plain.encode() not in path.read_bytes(), path.name
    assert all(body["code"] not in str(e.payload) for e in core.tape.latest(limit=50))


def test_a_join_code_gives_a_node_token_and_works_only_once(core, owner, stranger):
    code = owner.post("/api/nodes/join-codes").json()["code"]
    first = join(stranger, code.lower().replace("-", " "))  # typed loosely on the other machine
    assert first.status_code == 200 and first.json()["token"].startswith("jhqn_")
    again = join(stranger, code, "second-box")
    assert again.status_code == 409
    assert again.json()["detail"].startswith("That join code has already been used. Nothing "
                                             "was joined.")
    assert [n["name"] for n in core.auth.list_nodes()] == ["example-box"]


def test_an_expired_join_code_is_refused_and_says_to_make_a_new_one(core, owner, stranger):
    code, _ = core.auth.create_node_join_code(10)
    core.auth._conn.execute("UPDATE node_join_codes SET expires = ?", (now_ms() - 1,))
    joined(owner, stranger, "other-box")  # the link is in use, so the join answers
    answer = join(stranger, code)
    assert answer.status_code == 410
    assert answer.json()["detail"] == ("That join code has run out. Nothing was joined. Make "
                                       "a new one under Settings, Nodes on the hub.")


def test_making_a_new_join_code_ends_the_one_before_it(core, owner, stranger):
    old = owner.post("/api/nodes/join-codes").json()["code"]
    new = owner.post("/api/nodes/join-codes").json()["code"]
    assert join(stranger, old).status_code == 410
    assert join(stranger, new).status_code == 200


def test_wrong_join_codes_are_throttled_per_address(core, owner, stranger):
    code = owner.post("/api/nodes/join-codes").json()["code"]
    for _ in range(5):
        assert join(stranger, "AAAAA-AAAAA").status_code == 401
    locked = join(stranger, code)  # even the right code, from that address, for now
    assert locked.status_code == 429 and "Nothing was joined" in locked.json()["detail"]
    assert core.auth.list_nodes() == []
    outcome, _, _ = core.auth.redeem_node_join_code(code, "example-box", "192.0.2.9")
    assert outcome == "ok"  # another address is not held back by the first


def test_a_name_already_joined_is_refused_without_spending_the_code(core, owner, stranger):
    joined(owner, stranger)
    code = owner.post("/api/nodes/join-codes").json()["code"]
    taken = join(stranger, code)
    assert taken.status_code == 409 and "--name" in taken.json()["detail"]
    assert join(stranger, code, "second-box").status_code == 200
    with pytest.raises(AuthError):
        core.auth.redeem_node_join_code(code, "Not A Name", "192.0.2.9")


def test_a_node_token_is_refused_on_the_api_the_feed_and_the_door(core, owner, stranger):
    _, token = joined(owner, stranger)
    assert stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT,
                         headers=bearer(token)).status_code == 200
    for path in ("/api/tape", "/api/feed", "/api/settings", "/api/status", "/api/models/providers",
                 "/api/nodes", "/api/door/status", "/api/tasks"):
        answer = stranger.get(path, headers=bearer(token))
        assert answer.status_code == 401, path
    for method, path in OWNER_ROUTES:
        assert stranger.request(method, path, headers=bearer(token)).status_code == 401, path
    assert stranger.put("/api/settings/owner.name", json={"value": "x"},
                        headers=bearer(token)).status_code == 401
    # The relay is for this machine's own processes, not for a node.
    assert stranger.get(f"{protocol.RELAY}/a/b/models", headers=bearer(token)).status_code == 401
    assert core.auth.check_token(token) is None and core.auth.check_door_token(token) is None
    open_door(core)
    core.settings.set("door.listen", "main_port", actor="owner")
    headers, body = modern("tools/list")
    _, door_token = add_client(core)
    with TestClient(create_app(core), base_url="http://127.0.0.1") as at_the_door:
        assert at_the_door.post("/mcp", json=body,
                                headers=headers | bearer(door_token)).status_code == 200
        assert at_the_door.post("/mcp", json=body,
                                headers=headers | bearer(token)).status_code == 401


def test_an_api_token_a_door_token_and_the_owners_session_are_refused_on_the_node_link(
        core, owner, stranger):
    joined(owner, stranger)
    _, api_token = core.auth.create_token("script", ["read", "write"])
    _, door_token = add_client(core)
    for method, path in LINK_ROUTES:
        for token in (api_token, door_token):
            answer = stranger.request(method, path, json=BEAT, headers=bearer(token))
            assert answer.status_code in (401, 429), path  # refused, then held back too
        assert owner.request(method, path, json=BEAT).status_code in (401, 429), path
    for token in (api_token, door_token):
        for method, path in OWNER_ROUTES:
            answer = stranger.request(method, path, headers=bearer(token))
            assert answer.status_code in (401, 403), path
        assert stranger.get(f"{protocol.RELAY}/a/b/models",
                            headers=bearer(token)).status_code == 401
    assert owner.get(f"{protocol.RELAY}/a/b/models").status_code == 401


def test_wrong_node_tokens_are_held_back_and_a_real_one_is_never_logged_or_stored(
        core, owner, stranger, caplog):
    _, token = joined(owner, stranger)
    for _ in range(5):
        assert stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT,
                             headers=bearer("jhqn_" + "x" * 43)).status_code == 401
    held = stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT,
                         headers=bearer("jhqn_" + "y" * 43))
    assert held.status_code == 429 and "Nothing was done" in held.json()["detail"]
    # The joined node itself is never locked out by someone else's guessing.
    assert stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT,
                         headers=bearer(token)).status_code == 200
    assert core.auth.node_throttled("192.0.2.9") is False
    assert token not in caplog.text
    stored = (core.config.data / "auth.db").read_bytes()
    assert token.encode() not in stored  # only a digest is kept


def test_a_revoked_node_token_stops_working_at_once(core, owner, stranger):
    node_id, token = joined(owner, stranger)
    assert owner.delete(f"/api/nodes/{node_id}").json()["ok"] is True
    answer = stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT, headers=bearer(token))
    assert answer.status_code == 401 and "removed" in answer.json()["detail"]
    assert owner.delete(f"/api/nodes/{node_id}").status_code == 404


def test_one_nodes_token_cannot_answer_another_nodes_job(core, owner, stranger):
    first, _ = joined(owner, stranger)
    _, other = joined(owner, stranger, "second-box")
    hub = nodes.hub(core)
    assert hub.job_for(first, "nope") is None
    answer = stranger.post(f"{protocol.LINK}/jobs/anything/reply", content=b"",
                           headers=bearer(other))
    assert answer.status_code == 410


def test_the_relay_takes_only_this_installs_own_key_and_only_the_known_paths(
        core, owner, stranger):
    node_id, token = joined(owner, stranger)
    stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT, headers=bearer(token))
    key = core.secrets.get(RELAY_SECRET)
    assert key.startswith("jhqr_")
    assert stranger.get("/api/tape", headers=bearer(key)).status_code == 401
    assert stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT,
                         headers=bearer(key)).status_code == 401
    base = f"{protocol.RELAY}/{node_id}/big"
    assert stranger.get(f"{base}/../../tape", headers=bearer(key)).status_code in (401, 404)
    assert stranger.post(f"{base}/models", headers=bearer(key)).status_code == 404
    assert stranger.get(f"{base}/admin", headers=bearer(key)).status_code == 404


def test_a_heartbeat_with_names_that_cannot_be_used_is_refused_and_records_nothing(
        core, owner, stranger):
    _, token = joined(owner, stranger)
    for bad in ({"version": "0.1.0", "protocol": 1, "models": ["Has Spaces"]},
                {"version": "0.1.0", "protocol": 1, "models": [f"m{i}" for i in range(17)]},
                {"protocol": 1}):
        answer = stranger.post(f"{protocol.LINK}/heartbeat", json=bad, headers=bearer(token))
        assert answer.status_code == 422 and "Nothing was recorded" in answer.json()["detail"]
    assert not [e for e in core.tape.latest(limit=50) if e.kind == "node.offered"]


def test_an_install_with_one_machine_is_unchanged(core, owner, stranger):
    """No join code was ever made: the link does not answer, nothing node-shaped
    is written, and no new process is supervised."""
    assert supervisor.CHILDREN == ("web", "worker", "village") == PROCESSES
    assert stranger.post(protocol.JOIN, json={"code": "AAAAA-AAAAA",
                                              "name": "example-box"}).status_code == 404
    assert stranger.get(f"{protocol.RELAY}/a/b/models").status_code == 404
    assert stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT,
                         headers=bearer("jhqn_" + "x" * 43)).status_code == 401
    assert owner.get("/api/nodes").json()["nodes"] == []
    assert [p["name"] for p in owner.get("/api/status").json()["processes"]] == list(PROCESSES)
    assert core.secrets.get(RELAY_SECRET) is None
    assert owner.get("/api/models/providers").json()["providers"] == []
    assert not [e for e in core.tape.latest(limit=200) if e.kind.startswith("node.")]
    assert core.auth.node_throttled("testclient") is False  # nothing was even counted


def test_what_a_node_offers_is_recorded_when_it_changes_and_not_on_every_heartbeat(
        core, owner, stranger):
    _, token = joined(owner, stranger)
    for _ in range(3):
        assert stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT,
                             headers=bearer(token)).status_code == 200
    assert len([e for e in core.tape.latest(limit=50) if e.kind == "node.offered"]) == 1
    stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT | {"models": ["big", "small"]},
                  headers=bearer(token))
    offered = [e for e in core.tape.latest(limit=50) if e.kind == "node.offered"]
    assert len(offered) == 2 and offered[0].actor == "node:example-box"
    listed = owner.get("/api/nodes").json()["nodes"][0]
    assert [m["name"] for m in listed["models"]] == ["big", "small"]
    assert listed["connected"] is True and listed["last_seen_ms"] is not None


def test_a_changed_port_on_the_hub_moves_the_nodes_providers_at_the_next_start(
        core, cfg, owner, stranger):
    from dataclasses import replace

    from jimmyhq.core import open_core
    from jimmyhq.models import brain

    node_id, token = joined(owner, stranger)
    stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT, headers=bearer(token))
    before = brain(core).provider("n-example-box_big")
    assert before.base_url == f"http://127.0.0.1:0{protocol.RELAY}/{node_id}/big"
    moved = open_core(replace(cfg, port=8555))
    try:
        create_app(moved)
        after = brain(moved).provider("n-example-box_big")
        assert after.base_url == f"http://127.0.0.1:8555{protocol.RELAY}/{node_id}/big"
        assert after.is_local and after.label == before.label
    finally:
        moved.close()


def test_the_owner_can_rename_or_switch_off_a_nodes_provider_and_it_stays_that_way(
        core, owner, stranger):
    from jimmyhq.models import brain

    _, token = joined(owner, stranger)
    stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT, headers=bearer(token))
    changed = owner.put("/api/models/providers/n-example-box_big",
                        json={"label": "The big one", "enabled": False})
    assert changed.status_code == 200, changed.text
    nodes.hub(core).sync_providers()
    stranger.post(f"{protocol.LINK}/heartbeat", json=BEAT | {"models": ["big", "small"]},
                  headers=bearer(token))
    kept = brain(core).provider("n-example-box_big")
    assert kept.label == "The big one" and kept.enabled is False
