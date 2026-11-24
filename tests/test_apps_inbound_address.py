"""Messages from senders that can only be given an address (``apps.inbound``): a key the
owner made for the address, the sender's own ``?parameters``, a JSON list where a name
declares it, and a name's own size. Tried from the attacker's side: a header key put in the
address, a wrong key there, floods, and looking for the key where it must never be."""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from apps_full_helpers import PASSWORD, client_for, install_full
from jimmyhq import storeversion
from jimmyhq.apps import ManifestError, dbfile, inbound, inbound_routes, inbox, permissions, quotas
from jimmyhq.apps import parse as parse_manifest
from jimmyhq.cli import _NoQueryStrings

EVENTS = [{"name": "event", "label": "Home events", "help": "What your home says happened."}]
DEVICE = ("192.0.2.10", 5000)
MARK = "the-pendant-heard-7Q3"  # looked for where a body must never be


def home_app(core, tmp_path, app_id: str = "house", **kw) -> dict:
    kw.setdefault("permissions", ["inbound"])
    kw.setdefault("inbound", EVENTS)
    return install_full(core, tmp_path, app_id, **kw)


@pytest.fixture
def owner(core):
    with client_for(core) as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
        yield c


@pytest.fixture
def device(core):
    with client_for(core, address=DEVICE) as c:
        yield c


@pytest.fixture
def home(core, tmp_path):
    home_app(core, tmp_path)
    return "house"


def make_key(owner, *, in_address: bool = False, app_id: str = "house",
             label: str = "Pendant") -> dict:
    answer = owner.post(f"/api/apps/{app_id}/inbound/keys",
                        json={"label": label, "in_address": in_address})
    assert answer.status_code == 200, answer.text
    return answer.json()


def post_to(client, path: str, body=None, *, raw: bytes | None = None,
            headers: dict | None = None):
    content = raw if raw is not None else json.dumps({"says": MARK} if body is None
                                                     else body).encode()
    return client.post(path, content=content,
                       headers={"content-type": "application/json", **(headers or {})})


def by_address(client, key: str, body=None, *, name: str = "event", query: str = "",
               app_id: str = "house", **kw):
    return post_to(client, f"/api/in/{app_id}/{name}/k/{key}{query}", body, **kw)


def by_header(client, key: str, body=None, *, name: str = "event", query: str = "", **kw):
    headers = {"authorization": f"Bearer {key}", **kw.pop("headers", {})}
    return post_to(client, f"/api/in/house/{name}{query}", body, headers=headers, **kw)


def waiting(core, app_id: str = "house") -> list[dict]:
    return [d for d in inbox.take(core, app_id) if d["type"] == "inbound"]


def shape(answer) -> tuple:
    headers = {k: v for k, v in answer.headers.items() if k.lower() != "date"}
    return answer.status_code, answer.content, headers


# --- a key in the address ---------------------------------------------------------------------


def test_a_key_made_for_an_address_is_taken_from_the_address_and_its_addresses_given_once(
        owner, device, core, tmp_path):
    home_app(core, tmp_path, inbound=[*EVENTS, {"name": "memory", "label": "Memories"}])
    made = make_key(owner, in_address=True)
    key = made["key"]
    assert made["paths"] == {"event": f"/api/in/house/event/k/{key}",
                             "memory": f"/api/in/house/memory/k/{key}"}
    assert post_to(device, made["paths"]["memory"], {"n": 1}).status_code == 202
    [delivery] = waiting(core)
    assert delivery["data"]["name"] == "memory" and delivery["data"]["key_name"] == "Pendant"
    seen = json.dumps(owner.get("/api/apps/house/inbound").json())
    assert key not in seen and '"in_address": true' in seen


def test_a_header_key_put_in_the_address_gets_the_one_refusal(owner, device, core, home):
    header_key = make_key(owner)["key"]
    assert "paths" not in make_key(owner, label="Hub")  # a header key has no address
    refusals = {
        "no key": post_to(device, "/api/in/house/event"),
        "a header key in the address": by_address(device, header_key),
        "a wrong key in the address": by_address(device, inbound.new_key()),
        "a long key in the address": by_address(device, "x" * 300),
    }
    first = shape(refusals["no key"])
    assert first[0] == 401
    for why, answer in refusals.items():
        assert shape(answer) == first, why
    assert waiting(core) == []
    assert by_header(device, header_key).status_code == 202  # still fine where it belongs


def test_a_key_made_for_an_address_works_in_the_header_too(owner, device, core, home):
    key = make_key(owner, in_address=True)["key"]
    assert by_header(device, key, {"n": 1}).status_code == 202
    assert [d["data"]["body"] for d in waiting(core)] == [{"n": 1}]


def test_on_the_address_route_only_the_key_in_the_address_counts(owner, device, core, home):
    right = make_key(owner)["key"]
    answer = by_address(device, inbound.new_key(), headers={"authorization": f"Bearer {right}"})
    assert answer.status_code == 401 and waiting(core) == []


def test_an_address_key_for_one_app_is_refused_at_another(owner, device, core, tmp_path):
    home_app(core, tmp_path)
    home_app(core, tmp_path, "garden")
    garden = make_key(owner, in_address=True, app_id="garden")["key"]
    assert by_address(device, garden, app_id="house").status_code == 401
    assert by_address(device, garden, app_id="garden").status_code == 202


def test_only_post_is_answered_at_an_address_with_a_key(owner, device, core, home):
    key = make_key(owner, in_address=True)["key"]
    for method in ("GET", "PUT", "DELETE", "PATCH", "OPTIONS"):
        answer = device.request(method, f"/api/in/house/event/k/{key}")
        assert answer.status_code == 405, method
        assert key not in answer.text
    assert waiting(core) == []


def test_refusals_in_the_address_count_toward_the_block_and_a_right_address_key_still_passes(
        owner, device, core, home):
    core.settings.set("apps.inbound_bad_per_minute", 3, actor="owner")
    key = make_key(owner, in_address=True)["key"]
    assert [by_address(device, inbound.new_key()).status_code for _ in range(3)] == [401] * 3
    turned = by_address(device, inbound.new_key())
    assert turned.status_code == 429 and int(turned.headers["retry-after"]) > 60
    assert post_to(device, "/api/in/house/event").status_code == 429  # no key: turned away
    assert by_address(device, key, {"n": 1}).status_code == 202
    assert [d["data"]["body"] for d in waiting(core)] == [{"n": 1}]


def test_an_address_key_may_send_only_so_many_messages_a_minute(owner, device, core, home):
    core.settings.set("apps.inbound_per_minute", 2, actor="owner")
    key = make_key(owner, in_address=True)["key"]
    assert [by_address(device, key).status_code for _ in range(3)] == [202, 202, 429]


def test_making_a_key_for_the_address_is_on_the_record_without_the_key(owner, core, home):
    made = make_key(owner, in_address=True)
    make_key(owner, label="Hub")
    events = [e for e in core.tape.after(0, limit=1000) if e.kind == "apps.inbound_key_made"]
    assert [(e.payload["label"], e.payload["in_address"]) for e in events] == [
        ("Pendant", True), ("Hub", False)]
    assert made["key"] not in json.dumps([e.payload for e in events])
    refused = owner.post("/api/apps/house/inbound/keys", json={"label": "X", "in_address": "yes"})
    assert refused.status_code == 422


# --- where the key in an address must never be ---------------------------------------------------


def access_line(path: str, status: int = 202) -> str:
    """The line uvicorn writes for one request, through JimmyHQ's filter."""
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("192.0.2.10:5000", "POST", path, "1.1", status), None)
    assert _NoQueryStrings().filter(record) is True
    return record.getMessage()


def test_the_key_in_an_address_never_reaches_the_app_the_record_the_receipts_or_the_logs(
        owner, device, core, home, caplog):
    caplog.set_level(logging.DEBUG)
    made = make_key(owner, in_address=True)
    key, path = made["key"], made["paths"]["event"]
    answers = [
        post_to(device, path + "?uid=7", {"says": MARK}),
        post_to(device, path, [MARK]),  # refused: a list where none is declared
        post_to(device, path, raw=b"{not json"),
        post_to(device, path + f"?echo={key}", {"n": 2}),
        post_to(device, path + "/more", {"n": 3}),  # not a route: 404
        post_to(device, path.replace("/event/", "/nosuch/"), {"n": 4}),
    ]
    for answer in answers:
        assert key not in answer.text and key not in json.dumps(dict(answer.headers))
    delivered = waiting(core)
    assert len(delivered) == 2
    inbox.answer(core, "house", delivered[0]["id"], ok=False, error="It failed.")

    assert key not in json.dumps([e.payload for e in core.tape.after(0, limit=10_000)])
    assert key not in json.dumps(inbound.service(core).store.receipts("house", 100))
    assert key not in json.dumps(owner.get("/api/apps/house/inbound").json())
    assert key not in json.dumps([d["data"] for d in delivered])
    # What JimmyHQ logged; the test's own client (httpx, the sender here) is not JimmyHQ.
    ours = [r.getMessage() for r in caplog.records if not r.name.startswith("httpx")]
    assert key not in "\n".join(ours)
    with sqlite3.connect(quotas.state_db(core)) as db:
        for table in ("inbox", "inbound_keys", "inbound_apps", "inbound_receipts"):
            rows = db.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            assert key not in json.dumps(rows, default=str), table
    for logged in (path, path + "?uid=7", path + "/more", f"/api/in/house/nosuch/k/{key}",
                   f"/prefix{path}", f"/api/in/house/event?key={key}", f"/elsewhere/{key}"):
        line = access_line(logged)
        assert key not in line and key[len(inbound.KEY_PREFIX):] not in line, logged
    assert "/api/in/house/event/..." in access_line(path)
    assert "/api/in/house/event HTTP" in access_line("/api/in/house/event")


def test_the_log_filter_knows_what_our_keys_look_like():
    assert inbound.new_key().startswith(inbound.KEY_PREFIX)
    assert inbound.KEY_PREFIX in access_line(f"/x/{inbound.new_key()}")  # hidden, not lost
    assert inbound.ADDRESS_MARK == "k"


def test_every_answer_from_the_door_tells_a_browser_to_send_no_referrer(owner, device, home):
    key = make_key(owner, in_address=True)["key"]
    for answer in (by_address(device, key), by_address(device, inbound.new_key()),
                   device.get(f"/api/in/house/event/k/{key}"), by_header(device, key)):
        assert answer.headers["referrer-policy"] == "no-referrer", answer.status_code


# --- the sender's own parameters ------------------------------------------------------------------


def test_the_senders_own_parameters_reach_the_app_with_the_first_value_winning(
        owner, device, core, home):
    key = make_key(owner, in_address=True)["key"]
    assert by_address(device, key, {"n": 1},
                      query="?uid=abc&session_id=s1&uid=xyz&empty=").status_code == 202
    assert by_header(device, key, {"n": 2}, query="?source=hub").status_code == 202
    first, second = waiting(core)
    assert first["data"]["query"] == {"uid": "abc", "session_id": "s1", "empty": ""}
    assert second["data"]["query"] == {"source": "hub"}


def test_a_message_without_parameters_reads_as_it_always_did(owner, device, core, home):
    key = make_key(owner)["key"]
    assert by_header(device, key, {"n": 1}).status_code == 202
    [delivery] = waiting(core)
    assert set(delivery["data"]) == {"name", "body", "received_ms", "key_name"}


def test_a_parameter_that_carries_one_of_our_keys_never_reaches_the_app(owner, device, core,
                                                                        home):
    key = make_key(owner, in_address=True)["key"]
    other = inbound.new_key()
    assert by_address(device, key, query=f"?token={other}&uid=1&{other}=x").status_code == 202
    [delivery] = waiting(core)
    assert delivery["data"]["query"] == {"uid": "1"}


def test_too_many_or_too_long_parameters_are_refused_by_the_owners_settings(
        owner, device, core, home):
    core.settings.set("apps.inbound_query_max", 2, actor="owner")
    core.settings.set("apps.inbound_query_chars", 16, actor="owner")
    key = make_key(owner, in_address=True)["key"]
    assert by_address(device, key, query="?a=1&b=2&a=3").status_code == 202  # two names
    many = by_address(device, key, query="?a=1&b=2&c=3")
    assert many.status_code == 400 and "Settings, Apps" in many.json()["error"]
    assert by_address(device, key, query="?a=" + "x" * 17).status_code == 400
    assert by_address(device, key, query="?" + "x" * 17 + "=1").status_code == 400
    assert len(waiting(core)) == 1
    outcomes = [r["outcome"] for r in inbound.service(core).store.receipts("house", 10)]
    assert "bad_query" in outcomes


def test_the_senders_parameters_are_picked_out_by_one_named_rule():
    pairs = [("uid", "a"), ("uid", "b"), ("x", inbound.new_key()), ("n", "1")]
    assert inbound.sender_query(pairs, 5, 50) == {"uid": "a", "n": "1"}
    assert inbound.sender_query(pairs, 1, 50) is None
    assert inbound.sender_query([("a", "x" * 51)], 5, 50) is None
    assert inbound.sender_query([], 0, 50) == {}


# --- a JSON list, and a name's own size -----------------------------------------------------------


def test_a_json_list_is_taken_only_for_a_name_that_declares_array(owner, device, core,
                                                                  tmp_path):
    home_app(core, tmp_path, inbound=[
        *EVENTS, {"name": "memory", "label": "Memories", "array": True}])
    key = make_key(owner, in_address=True)["key"]
    listed = [{"text": "one"}, {"text": "two"}]
    assert by_address(device, key, listed, name="memory").status_code == 202
    assert by_address(device, key, {"text": "three"}, name="memory").status_code == 202
    refused = by_address(device, key, listed, name="event")
    assert refused.status_code == 400 and refused.json()["error"] == inbound_routes.NOT_OBJECT
    scalar = by_address(device, key, raw=b'"on"', name="memory")
    assert scalar.status_code == 400
    assert scalar.json()["error"] == inbound_routes.NOT_OBJECT_OR_LIST
    assert [d["data"]["body"] for d in waiting(core)] == [listed, {"text": "three"}]


def test_a_names_own_size_holds_it_below_the_owners_limit_and_never_raises_it(
        owner, device, core, tmp_path):
    home_app(core, tmp_path, inbound=[
        *EVENTS, {"name": "small", "label": "Small", "max_kb": 1},
        {"name": "long", "label": "Long talks", "max_kb": 512}])
    key = make_key(owner)["key"]
    two_kb = {"says": "x" * 2000}
    assert by_header(device, key, two_kb, name="event").status_code == 202  # owner's 16 KB
    small = by_header(device, key, two_kb, name="small")
    assert small.status_code == 413 and "at most 1 KB" in small.json()["error"]
    core.settings.set("apps.inbound_max_kb", 2, actor="owner")
    three_kb = {"says": "x" * 3000}
    held = by_header(device, key, three_kb, name="long")
    assert held.status_code == 413 and "at most 2 KB" in held.json()["error"]
    seen = owner.get("/api/apps/house/inbound").json()
    assert seen["max_kb"] == 2
    assert [d.get("max_kb") for d in seen["declared"]] == [None, 1, 512]


def test_a_name_that_says_what_it_expects_gets_that_much_by_default_and_one_that_does_not_gets_16(
        owner, device, core, tmp_path):
    home_app(core, tmp_path, inbound=[
        *EVENTS, {"name": "long", "label": "Long talks", "max_kb": 512}])
    key = make_key(owner)["key"]
    big = {"says": "x" * 100_000}
    assert by_header(device, key, big, name="long").status_code == 202  # no setting touched
    refused = by_header(device, key, big, name="event")
    assert refused.status_code == 413 and "at most 16 KB" in refused.json()["error"]
    core.settings.set("apps.inbound_default_kb", 200, actor="owner")
    assert by_header(device, key, big, name="event").status_code == 202
    core.settings.set("apps.inbound_max_kb", 64, actor="owner")  # the owner's ceiling still wins
    assert by_header(device, key, big, name="long").status_code == 413
    assert by_header(device, key, big, name="event").status_code == 413
    seen = owner.get("/api/apps/house/inbound").json()
    assert (seen["max_kb"], seen["default_kb"]) == (64, 200)


def test_a_manifest_reads_array_and_max_kb_and_refuses_bad_ones():
    def manifest(**entry) -> dict:
        return {"manifest": 1, "id": "house", "name": "House", "summary": "Home events.",
                "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
                "permissions": ["inbound"], "server": {"entry": "house.main:app"},
                "inbound": [{"name": "event", "label": "Home events"} | entry]}

    plain = parse_manifest(manifest())
    assert (plain.inbound[0].array, plain.inbound[0].max_kb) == (False, None)
    assert "array" not in plain.to_dict()["inbound"][0]
    both = parse_manifest(manifest(array=True, max_kb=64))
    assert (both.inbound[0].array, both.inbound[0].max_kb) == (True, 64)
    assert parse_manifest(both.to_dict()) == both
    for bad in ({"array": "yes"}, {"max_kb": 0}, {"max_kb": 2000}, {"max_kb": 1.5},
                {"max_kb": True}):
        with pytest.raises(ManifestError) as caught:
            parse_manifest(manifest(**bad))
        assert caught.value.fields[0].startswith("inbound[0]."), bad


def test_the_install_dialog_says_how_large_an_apps_messages_may_be():
    def lines(entries) -> str:
        m = parse_manifest({
            "manifest": 1, "id": "house", "name": "House", "summary": "Home events.",
            "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
            "permissions": ["inbound"], "server": {"entry": "house.main:app"},
            "inbound": entries})
        return next(x.sentence for x in permissions.describe(m) if x.permission == "inbound")

    plain = lines([{"name": "event", "label": "Events"}])
    assert "KB" not in plain
    sized = lines([{"name": "event", "label": "Events", "max_kb": 8},
                   {"name": "talk", "label": "Talks", "max_kb": 256}])
    assert sized.startswith(plain) and "up to 256 KB" in sized and "Settings, Apps" in sized


# --- the file -------------------------------------------------------------------------------


def test_a_version_3_apps_db_gains_address_keys_and_its_keys_stay_header_keys(tmp_path):
    path = tmp_path / "apps.db"
    conn = sqlite3.connect(path)
    conn.executescript(dbfile.STORE.schema)
    for step in dbfile.STORE.steps[:2]:
        storeversion.run_script(conn, step)
    conn.execute("INSERT INTO inbound_keys (id, app_id, label, digest, created) VALUES "
                 "('k1', 'alpha', 'Hub', ?, 1800000000000)", ("0" * 64,))
    conn.commit()
    conn.execute("PRAGMA user_version = 3")
    conn.close()

    opened = inbound.Store(path)
    try:
        assert opened.keys("alpha")[0]["in_address"] is False
        assert opened.find("0" * 64)["in_address"] == 0
        assert opened.add_key("alpha", "Pendant", "1" * 64, 5, 1800000000001, in_address=True)
        assert [k["in_address"] for k in opened.keys("alpha")] == [False, True]
    finally:
        opened.close()
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == dbfile.STORE.latest >= 4
    finally:
        conn.close()
