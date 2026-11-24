"""Messages from the owner's devices (``apps.inbound``), tried from the attacker's side:
guessing keys and apps, using one app's key at another, a session cookie, a web page,
large and malformed bodies, floods, a full queue, and looking for the body where it must
never be."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

import pytest

from apps_full_helpers import PASSWORD, client_for, full_app, install_full
from apps_install_helpers import install_folder
from jimmyhq import storeversion
from jimmyhq.apps import (
    ManifestError,
    bridge,
    dbfile,
    inbound,
    inbound_routes,
    inbox,
    install,
    quotas,
    update,
)
from jimmyhq.apps import parse as parse_manifest
from jimmyhq.core import open_core

EVENTS = [{"name": "event", "label": "Home events", "help": "What your home says happened."}]
MARK = "the-kettle-is-on-7Q3"  # looked for where a body must never be
DEVICE = ("192.0.2.10", 5000)


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


def make_key(owner, app_id: str = "house", label: str = "Home hub") -> str:
    answer = owner.post(f"/api/apps/{app_id}/inbound/keys", json={"label": label})
    assert answer.status_code == 200, answer.text
    return answer.json()["key"]


def send(client, key: str | None, body=None, *, app_id: str = "house", name: str = "event",
         raw: bytes | None = None, headers: dict | None = None):
    sent = {"content-type": "application/json", **(headers or {})}
    if key is not None:
        sent["authorization"] = f"Bearer {key}"
    content = raw if raw is not None else json.dumps({"says": MARK} if body is None
                                                     else body).encode()
    return client.post(f"/api/in/{app_id}/{name}", content=content, headers=sent)


def waiting(core, app_id: str = "house") -> list[dict]:
    return [d for d in inbox.take(core, app_id) if d["type"] == "inbound"]


def shape(answer) -> tuple:
    """What a caller can see of an answer, but the date."""
    headers = {k: v for k, v in answer.headers.items() if k.lower() != "date"}
    return answer.status_code, answer.content, headers


# --- the way through --------------------------------------------------------------------------


def test_a_message_with_the_right_key_is_queued_for_its_app_and_answered_202_and_nothing_else(
        owner, device, core, home):
    key = make_key(owner)
    answer = send(device, key, {"entity": "light.porch", "state": "on"})
    assert answer.status_code == 202 and answer.content == b'{"ok":true}'
    [delivery] = waiting(core)
    assert delivery["data"]["name"] == "event"
    assert delivery["data"]["body"] == {"entity": "light.porch", "state": "on"}
    assert delivery["data"]["key_name"] == "Home hub"
    assert isinstance(delivery["data"]["received_ms"], int)


def test_one_key_works_for_every_name_its_app_declares(owner, device, core, tmp_path):
    home_app(core, tmp_path, inbound=[*EVENTS, {"name": "doorbell", "label": "Doorbell"}])
    key = make_key(owner)
    assert send(device, key, name="event").status_code == 202
    assert send(device, key, name="doorbell").status_code == 202
    assert sorted(d["data"]["name"] for d in waiting(core)) == ["doorbell", "event"]


# --- every refusal of who is one refusal -------------------------------------------------------


def test_every_refusal_of_who_is_the_same_401_byte_for_byte(owner, device, core, tmp_path):
    home_app(core, tmp_path)
    home_app(core, tmp_path, "garden")
    install_full(core, tmp_path, "plain")  # no inbound at all
    key = make_key(owner)
    garden = make_key(owner, "garden")
    # A key for an app that does not ask for "inbound", put there by hand.
    stray = inbound.new_key()
    inbound.service(core).store.add_key("plain", "Stray", inbound.digest(stray), 5, 1)

    refusals = {
        "no key": send(device, None),
        "a wrong key": send(device, inbound.new_key()),
        "not a bearer key": send(device, None, headers={"authorization": f"Basic {key}"}),
        "an app that does not exist": send(device, key, app_id="nosuchapp"),
        "a name the app does not declare": send(device, key, name="doorbell"),
        "another app's key": send(device, garden),
        "an app without the permission": send(device, stray, app_id="plain"),
    }
    install.disable(core, "garden")
    refusals["a switched-off app"] = send(device, garden, app_id="garden")
    owner.put("/api/apps/house/inbound/open", json={"open": False})
    refusals["an app its owner closed"] = send(device, key)
    owner.put("/api/apps/house/inbound/open", json={"open": True})
    core.settings.set("apps.inbound_enabled", False, actor="owner")
    refusals["the door switched off"] = send(device, key)

    first = shape(refusals["no key"])
    assert first[0] == 401
    for why, answer in refusals.items():
        assert shape(answer) == first, why
    assert waiting(core) == [] and waiting(core, "garden") == [] and waiting(core, "plain") == []


def test_a_key_for_one_app_is_refused_at_another(owner, device, core, tmp_path):
    home_app(core, tmp_path)
    home_app(core, tmp_path, "garden")
    garden = make_key(owner, "garden")
    assert send(device, garden, app_id="house").status_code == 401
    assert waiting(core) == [] and waiting(core, "garden") == []
    assert send(device, garden, app_id="garden").status_code == 202


def test_a_refusal_without_a_right_key_never_looks_at_the_named_app(
        owner, device, core, home, monkeypatch):
    make_key(owner)
    looked: list[str] = []
    real = bridge.find_app
    monkeypatch.setattr(bridge, "find_app", lambda c, a: looked.append(a) or real(c, a))
    send(device, inbound.new_key(), app_id="house")
    send(device, inbound.new_key(), app_id="nosuchapp")
    send(device, None, app_id="house")
    assert looked == []  # what the address names changes nothing about the work done


def test_a_session_cookie_alone_does_not_open_the_door(owner, core, home):
    make_key(owner)
    answer = send(owner, None)  # the signed-in owner's browser, cookie and all
    assert answer.status_code == 401 and waiting(core) == []
    assert "set-cookie" not in answer.headers  # nor is the session renewed by it


def _asgi(app, method: str, path: str) -> tuple[int, bytes]:
    """One request handed to the app as a server would, the path exactly as given (no
    client in between to tidy it)."""
    import asyncio  # noqa: PLC0415

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "root_path": "", "query_string": b"", "client": DEVICE,
             "server": ("testserver", 80),
             "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                         (b"content-length", b"2")]}
    sent: list[dict] = []
    bodies = [{"type": "http.request", "body": b"{}", "more_body": False}]

    async def receive():
        return bodies.pop(0) if bodies else {"type": "http.disconnect"}

    async def send_(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send_))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def test_nothing_but_the_device_door_or_not_found_answers_under_its_address(owner, core, home):
    # The owner is signed in and holds a key, so anything that slipped past the sign-in
    # gate to another route would show; this caller has no cookie and no key.
    make_key(owner)
    from jimmyhq.web.app import create_app  # noqa: PLC0415

    app = create_app(core)
    door = {json.dumps({"ok": False, "error": s}, separators=(",", ":")).encode()
            for s in (inbound_routes.REFUSED, inbound_routes.ONLY_POST,
                      inbound_routes.TURNED_AWAY)}
    not_found = ({"detail": "There is nothing at this address."},
                 {"detail": "Method Not Allowed"})
    probes = ["/api/in/house/event", "/api/in/house/event/", "/api/in/house/event/x",
              "/api/in/../apps/house/inbound/keys", "/api/in/house/../../apps/house/inbound",
              "/api/in/house/../../apps/house/inbound/keys", "/api/in//apps/house/inbound",
              "/api/in/x/y/z/w", "/api/in/house/inbound/keys", "/api/in/apps/house",
              "/api/in/house/event/../../../apps", "/api/in/./event"]
    for path in probes:
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
            status, body = _asgi(app, method, path)
            assert status in (401, 404, 405, 307, 429) and (
                body in door or body == b"" or json.loads(body) in not_found), (path, method,
                                                                               status, body)
    assert waiting(core) == [] and len(inbound.service(core).store.keys("house")) == 1


def test_the_device_door_answers_only_to_names_the_server_is_known_by(owner, device, core,
                                                                     home):
    key = make_key(owner)
    rebound = device.post("/api/in/house/event", content=b"{}", headers={
        "host": "rebound.example.com", "authorization": f"Bearer {key}",
        "content-type": "application/json"})
    assert rebound.status_code == 421 and waiting(core) == []


def test_messages_from_devices_waiting_never_hold_up_other_deliveries(owner, device, core,
                                                                     home):
    key = make_key(owner)
    for n in range(inbox.MAX_TAKE + 5):
        assert send(device, key, {"n": n}).status_code == 202
    inbox.deliver(core, "house", "call", {"name": "refresh", "args": {}})
    first = inbox.take(core, "house")
    assert first[0]["type"] == "call"


def test_a_request_with_an_origin_header_is_refused_and_no_cors_header_is_ever_sent(
        owner, device, core, home):
    key = make_key(owner)
    for origin in ("http://testserver", "https://evil.example.com", "null"):
        answer = send(device, key, headers={"origin": origin})
        assert answer.status_code == 403, origin
        assert not any(h.lower().startswith("access-control-") for h in answer.headers)
    preflight = device.options("/api/in/house/event", headers={
        "origin": "https://evil.example.com", "access-control-request-method": "POST"})
    assert preflight.status_code == 405
    assert not any(h.lower().startswith("access-control-") for h in preflight.headers)
    assert waiting(core) == []


def test_only_post_is_answered(owner, device, core, home):
    key = make_key(owner)
    for method in ("GET", "PUT", "DELETE", "PATCH"):
        answer = device.request(method, "/api/in/house/event",
                                headers={"authorization": f"Bearer {key}"})
        assert answer.status_code == 405, method
    assert waiting(core) == []


# --- what is sent ------------------------------------------------------------------------------


def test_a_message_larger_than_the_limit_is_refused_before_it_is_parsed(
        owner, device, core, home, monkeypatch):
    key = make_key(owner)
    core.settings.set("apps.inbound_max_kb", 1, actor="owner")
    parsed: list[bytes] = []
    real = inbound_routes.parse
    monkeypatch.setattr(inbound_routes, "parse", lambda raw: parsed.append(raw) or real(raw))
    large = json.dumps({"says": "x" * 2000}).encode()
    assert send(device, key, raw=large).status_code == 413
    streamed = device.post("/api/in/house/event", content=iter([large[:900], large[900:]]),
                           headers={"authorization": f"Bearer {key}",
                                    "content-type": "application/json"})
    assert streamed.status_code == 413  # no declared size, cut off as it grows
    assert parsed == [] and waiting(core) == []
    assert send(device, key, {"says": "short"}).status_code == 202


def test_a_body_that_is_not_one_json_object_is_refused(owner, device, core, home):
    key = make_key(owner)
    for raw in (b"[1, 2]", b'"on"', b"3", b"null", b"{not json", b'{"x": NaN}', b"",
                b"\xff\xfe"):
        assert send(device, key, raw=raw).status_code == 400, raw
    plain = send(device, key, headers={"content-type": "text/plain"})
    assert plain.status_code == 415
    assert waiting(core) == []


def test_a_number_too_large_for_json_or_a_lone_surrogate_is_refused_400_not_a_crash(
        owner, device, core, home):
    key = make_key(owner)
    for raw in (b'{"x": 1e400}', b'{"x": "\\ud800"}', b'{"\\udfff": 1}',
                b'{"x": ' + b"9" * 5000 + b"}", b"[" * 100_000):
        assert send(device, key, raw=raw).status_code in (400, 413), raw[:20]
    for raw in (b'{"x": 1e400}', b'{"x": "\\ud800"}'):
        assert send(device, key, raw=raw).status_code == 400, raw
    assert waiting(core) == []


def test_a_declared_size_too_large_for_a_number_is_refused_413_not_a_crash(
        owner, device, core, home):
    key = make_key(owner)
    answer = send(device, key, headers={"content-length": "9" * 30})
    assert answer.status_code == 413
    assert send(device, key, {"says": "short"}).status_code == 202


# --- how many ----------------------------------------------------------------------------------


def test_one_key_may_send_only_so_many_messages_a_minute(owner, device, core, home):
    core.settings.set("apps.inbound_per_minute", 3, actor="owner")
    key = make_key(owner)
    other = make_key(owner, label="Doorbell")
    assert [send(device, key).status_code for _ in range(3)] == [202, 202, 202]
    slowed = send(device, key)
    assert slowed.status_code == 429 and slowed.headers["retry-after"] == "60"
    assert send(device, other).status_code == 202  # counted for each key on its own
    assert len(waiting(core)) == 4


def test_an_address_that_keeps_getting_it_wrong_is_turned_away_but_never_a_right_key(
        owner, device, core, home):
    # Behind a proxy on the same computer every caller has the proxy's address, so a
    # stranger's noise must not lock the owner's own device out.
    core.settings.set("apps.inbound_bad_per_minute", 3, actor="owner")
    key = make_key(owner)
    assert [send(device, inbound.new_key()).status_code for _ in range(3)] == [401] * 3
    turned = send(device, inbound.new_key())
    assert turned.status_code == 429 and int(turned.headers["retry-after"]) > 60
    assert send(device, None).status_code == 429
    assert send(device, key, {"n": 1}).status_code == 202
    assert [d["data"]["body"] for d in waiting(core)] == [{"n": 1}]


def test_knocking_through_a_tunnel_never_turns_away_a_device_on_this_computer(
        owner, core, home):
    # A tunnel on this computer (Tailscale Serve) connects from the loopback address too.
    core.settings.set("apps.inbound_bad_per_minute", 3, actor="owner")
    key = make_key(owner)
    via = {"x-forwarded-for": "198.51.100.7"}
    with client_for(core) as here, client_for(core, address=("127.0.0.1", 5001)) as tunnel:
        assert [send(tunnel, inbound.new_key(), headers=via).status_code
                for _ in range(3)] == [401] * 3
        assert send(tunnel, None, headers=via).status_code == 429
        assert send(here, None).status_code == 401  # not turned away: counted apart
        assert send(tunnel, key, {"n": 1}, headers=via).status_code == 202


def test_a_turned_away_address_without_a_key_is_refused_before_any_key_is_looked_up(
        owner, device, core, home, monkeypatch):
    core.settings.set("apps.inbound_bad_per_minute", 2, actor="owner")
    for _ in range(2):
        send(device, None)
    looked: list[str] = []
    real = inbound.Store.find
    monkeypatch.setattr(inbound.Store, "find", lambda s, d: looked.append(d) or real(s, d))
    assert send(device, None).status_code == 429
    assert looked == []


def test_how_long_an_address_is_turned_away_is_a_setting(owner, device, core, home):
    core.settings.set("apps.inbound_bad_per_minute", 2, actor="owner")
    core.settings.set("apps.inbound_block_s", 30, actor="owner")
    for _ in range(2):
        send(device, inbound.new_key())
    turned = send(device, inbound.new_key())
    assert turned.status_code == 429 and 0 < int(turned.headers["retry-after"]) <= 31
    inbound.service(core).clock = lambda: 10.0**9  # long after
    assert send(device, inbound.new_key()).status_code == 401


def test_a_stranger_from_ever_new_addresses_meets_a_ceiling_but_a_right_key_still_gets_in(
        owner, core, home):
    core.settings.set("apps.inbound_bad_per_minute", 3, actor="owner")
    core.settings.set("apps.inbound_bad_all_per_minute", 10, actor="owner")
    key = make_key(owner)
    codes = []
    for n in range(30):  # a fresh address every time
        with client_for(core, address=(f"198.51.100.{n}", 5000)) as far:
            codes.append(send(far, inbound.new_key()).status_code)
    assert codes[:10] == [401] * 10 and set(codes[10:]) == {429}
    with client_for(core, address=("198.51.100.200", 5000)) as far:
        assert send(far, None).status_code == 429
        turned = send(far, inbound.new_key())
        assert turned.status_code == 429 and 0 < int(turned.headers["retry-after"]) <= 61
        assert send(far, key, {"n": 1}).status_code == 202  # a right key always passes
    inbound.service(core).clock = lambda: 10.0**9  # the minute has long moved on
    with client_for(core, address=("198.51.100.201", 5000)) as far:
        assert send(far, inbound.new_key()).status_code == 401


def test_the_ceiling_over_all_addresses_is_a_setting_next_to_the_one_per_address(core):
    where = {x["key"]: x for x in core.settings.describe()["settings"]}
    ceiling = where["apps.inbound_bad_all_per_minute"]
    assert ceiling["section"] == where["apps.inbound_bad_per_minute"]["section"]
    assert core.settings.get("apps.inbound_bad_all_per_minute") == 200
    assert len(ceiling["help"]) < 140 and not ceiling.get("advanced", False)


def test_an_address_cannot_push_its_own_block_out_of_memory_with_many_others():
    knocks = inbound.Knocks()
    knocks.record("forwarded:one", 1, 300.0, 0.0)
    assert knocks.blocked("forwarded:one", 1.0) > 0
    now = 1.0
    for n in range(inbound.MAX_SOURCES + 500):  # asking first, as the door does
        now += 0.001
        if not knocks.blocked(f"forwarded:other-{n}", now, 200):
            knocks.record(f"forwarded:other-{n}", 1, 300.0, now)
    assert knocks.blocked("forwarded:one", now) > 0


def test_how_many_receipts_each_app_keeps_is_a_setting(owner, device, core, home):
    core.settings.set("apps.inbound_receipts_max", 10, actor="owner")
    key = make_key(owner)
    for n in range(15):
        assert send(device, key, {"n": n}).status_code == 202
    assert len(inbound.service(core).store.receipts("house", 100)) == 10


def test_the_counts_kept_in_memory_stay_bounded(monkeypatch):
    monkeypatch.setattr(inbound, "MAX_SOURCES", 50)
    monkeypatch.setattr(inbound, "MAX_KEYS_PACED", 50)
    knocks, pace = inbound.Knocks(), inbound.Pace()
    for n in range(5000):
        knocks.record(f"198.51.100.{n}", 1, 300.0, float(n))
        pace.allow(f"key{n}", 10, float(n))
        pace.worth_a_receipt(f"key{n}", "too_many", float(n))
    assert knocks.remembered() <= 100
    assert len(pace._seen) <= 50 and len(pace._refused_at) <= 50


def test_a_full_queue_refuses_new_messages_and_drops_none_already_waiting(
        owner, device, core, home):
    core.settings.set("apps.inbound_queue_max", 2, actor="owner")
    key = make_key(owner)
    assert send(device, key, {"n": 1}).status_code == 202
    assert send(device, key, {"n": 2}).status_code == 202
    full = send(device, key, {"n": 3})
    assert full.status_code == 429 and full.headers["retry-after"]
    assert [d["data"]["body"] for d in waiting(core)] == [{"n": 1}, {"n": 2}]


# --- what comes back ---------------------------------------------------------------------------


def test_what_the_handler_returns_or_raises_never_reaches_the_sender(owner, device, core, home):
    key = make_key(owner)
    first = send(device, key, {"n": 1})
    second = send(device, key, {"n": 2})
    one, two = waiting(core)
    assert inbox.answer(core, "house", one["id"], ok=True, result={"leak": "answer"})
    assert inbox.answer(core, "house", two["id"], ok=False,
                        error="The app could not do that: no porch light.")
    third = send(device, key, {"n": 3})
    assert first.content == second.content == third.content == b'{"ok":true}'
    seen = owner.get("/api/apps/house/inbound").json()
    assert (seen["received"], seen["failed"]) == (3, 1)
    assert seen["last_error"] == "The app could not do that: no porch light."
    assert inbox.answer_of(core, one["id"]) is None  # an answered message is not kept


# --- where the body must never be ---------------------------------------------------------------


def test_bodies_never_appear_in_the_tape_the_receipts_or_the_logs(
        owner, device, core, home, caplog):
    caplog.set_level(logging.DEBUG)
    key = make_key(owner)
    core.settings.set("apps.inbound_max_kb", 1, actor="owner")
    send(device, key, {"says": MARK})
    send(device, key, [MARK])
    send(device, key, raw=json.dumps({"says": MARK * 100}).encode())
    send(device, key, raw=f"{{{MARK}".encode())
    send(device, inbound.new_key(), {"says": MARK})
    [delivery] = waiting(core)
    inbox.answer(core, "house", delivery["id"], ok=False, error="It failed.")

    tape = json.dumps([e.payload for e in core.tape.after(0, limit=10_000)])
    assert MARK not in tape
    receipts = inbound.service(core).store.receipts("house", 100)
    assert receipts and MARK not in json.dumps(receipts)
    assert {r["outcome"] for r in receipts} == {"queued", "not_object", "too_large", "not_json"}
    assert MARK not in caplog.text
    assert MARK not in json.dumps(owner.get("/api/apps/house/inbound").json())
    with sqlite3.connect(quotas.state_db(core)) as db:
        assert db.execute("SELECT COUNT(*) FROM inbox WHERE data LIKE ?",
                          (f"%{MARK}%",)).fetchone()[0] == 0


def test_a_private_app_may_take_messages_and_its_receipts_carry_no_more(
        owner, device, core, tmp_path):
    home_app(core, tmp_path, private=True)
    key = make_key(owner)
    assert send(device, key, {"says": MARK}).status_code == 202
    [line] = inbound.service(core).store.receipts("house")
    assert set(line) == {"name", "key_label", "at", "size", "outcome"}
    assert line["outcome"] == "queued" and line["key_label"] == "Home hub"


# --- keys --------------------------------------------------------------------------------------


def test_a_key_is_kept_only_as_its_digest_and_given_out_exactly_once(owner, core, home):
    answer = owner.post("/api/apps/house/inbound/keys", json={"label": "Home hub"}).json()
    key = answer["key"]
    assert key.startswith(inbound.KEY_PREFIX) and len(key) > 40
    assert key not in json.dumps(answer["inbound"])
    assert key not in json.dumps(owner.get("/api/apps/house/inbound").json())
    with sqlite3.connect(quotas.state_db(core)) as db:
        rows = db.execute("SELECT * FROM inbound_keys").fetchall()
    assert len(rows) == 1 and key not in json.dumps(rows)
    assert hashlib.sha256(key.encode()).hexdigest() in rows[0]
    made = [e for e in core.tape.after(0, limit=1000) if e.kind == "apps.inbound_key_made"]
    assert [e.payload["label"] for e in made] == ["Home hub"] and key not in str(made)


def test_removing_a_key_closes_the_door_to_it_at_once(owner, device, core, home):
    key = make_key(owner)
    other = make_key(owner, label="Doorbell")
    assert send(device, key).status_code == 202
    [kept] = [k for k in owner.get("/api/apps/house/inbound").json()["keys"]
              if k["label"] == "Home hub"]
    assert kept["uses"] == 1 and kept["last_used"]
    assert owner.delete(f"/api/apps/house/inbound/keys/{kept['id']}").status_code == 200
    assert send(device, key).status_code == 401
    assert send(device, other).status_code == 202


def test_the_door_is_closed_until_the_first_key_and_the_owner_can_close_it(owner, device, core,
                                                                          home):
    before = owner.get("/api/apps/house/inbound").json()
    assert before["open"] is False and before["keys"] == []
    assert before["declared"] == EVENTS and before["path"] == "/api/in/house/"
    key = make_key(owner)
    assert owner.get("/api/apps/house/inbound").json()["open"] is True
    closed = owner.put("/api/apps/house/inbound/open", json={"open": False}).json()
    assert closed["open"] is False and send(device, key).status_code == 401
    owner.put("/api/apps/house/inbound/open", json={"open": True})
    assert send(device, key).status_code == 202
    kinds = [e.kind for e in core.tape.after(0, limit=1000)
             if e.kind.startswith("apps.inbound")]
    assert kinds == ["apps.inbound_key_made", "apps.inbound_switched", "apps.inbound_switched",
                     "apps.inbound_switched"]


def test_an_app_has_at_most_the_set_number_of_keys_each_with_its_own_name(owner, core, home):
    core.settings.set("apps.inbound_keys_max", 2, actor="owner")
    make_key(owner, label="One")
    same = owner.post("/api/apps/house/inbound/keys", json={"label": " one "})
    assert same.status_code == 409 and "already has a key" in same.json()["detail"]
    make_key(owner, label="Two")
    third = owner.post("/api/apps/house/inbound/keys", json={"label": "Three"})
    assert third.status_code == 409 and "No key was made" in third.json()["detail"]
    for bad in ("", "   ", "x" * 61, "tab\there\x07"):
        assert owner.post("/api/apps/house/inbound/keys",
                          json={"label": bad}).status_code == 422, bad


def test_only_the_signed_in_owner_can_see_or_make_keys(device, core, home):
    assert device.get("/api/apps/house/inbound").status_code == 401
    assert device.post("/api/apps/house/inbound/keys", json={"label": "x"}).status_code == 401
    assert inbound.service(core).store.keys("house") == []


def test_an_app_that_takes_no_messages_has_no_keys(owner, core, tmp_path):
    install_full(core, tmp_path, "plain")
    assert owner.get("/api/apps/plain/inbound").json()["declared"] == []
    refused = owner.post("/api/apps/plain/inbound/keys", json={"label": "Home hub"})
    assert refused.status_code == 422


def test_keys_stay_across_a_restart_and_an_update(owner, device, core, cfg, tmp_path):
    home_app(core, tmp_path)
    key = make_key(owner)
    folder = full_app(tmp_path / "v2", "house", permissions=["inbound"], inbound=EVENTS,
                      version="1.1.0")
    plan = update.plan_folder(core, "house", folder)
    update.confirm_update(core, plan["plan"], plan["fingerprint"],
                          typed_name=plan["app"]["name"])
    assert send(device, key).status_code == 202
    assert owner.get("/api/apps/house/inbound").json()["open"] is True
    again = open_core(cfg)
    try:
        with client_for(again, address=DEVICE) as later:
            assert send(later, key).status_code == 202
    finally:
        again.close()


def test_an_update_changes_neither_the_doors_switch_nor_its_keys(owner, device, core, tmp_path):
    home_app(core, tmp_path)
    key = make_key(owner)
    other = make_key(owner, label="Doorbell")
    before = owner.get("/api/apps/house/inbound").json()
    with sqlite3.connect(quotas.state_db(core)) as db:
        digests = db.execute("SELECT id, digest FROM inbound_keys ORDER BY id").fetchall()
    for open_now in (True, False):
        owner.put("/api/apps/house/inbound/open", json={"open": open_now})
        version = "1.1.0" if open_now else "1.2.0"
        folder = full_app(tmp_path / version, "house", permissions=["inbound"], inbound=EVENTS,
                          version=version)
        plan = update.plan_folder(core, "house", folder)
        update.confirm_update(core, plan["plan"], plan["fingerprint"],
                              typed_name=plan["app"]["name"])
        after = owner.get("/api/apps/house/inbound").json()
        assert after["open"] is open_now
        assert [k["id"] for k in after["keys"]] == [k["id"] for k in before["keys"]]
        assert send(device, key).status_code == (202 if open_now else 401)
        assert send(device, other).status_code == (202 if open_now else 401)
    with sqlite3.connect(quotas.state_db(core)) as db:
        assert db.execute("SELECT id, digest FROM inbound_keys ORDER BY id").fetchall() == digests


def test_removing_an_app_with_its_data_removes_its_keys(owner, device, core, home):
    key = make_key(owner)
    install.remove(core, "house", keep_data=False)
    assert inbound.service(core).store.keys("house") == []
    assert send(device, key).status_code == 401


def test_removing_an_app_and_keeping_its_data_keeps_its_keys_and_turns_its_door_off(
        owner, device, core, tmp_path):
    folder = full_app(tmp_path, "house", permissions=["inbound"], inbound=EVENTS)
    install_folder(core, folder)
    key = make_key(owner)
    before = owner.get("/api/apps/house/inbound").json()
    assert before["open"] is True
    install.remove(core, "house", keep_data=True)
    assert send(device, key).status_code == 401  # nothing answers while it is not installed
    install_folder(core, folder)
    after = owner.get("/api/apps/house/inbound").json()
    assert after["open"] is False
    assert [(k["id"], k["label"]) for k in after["keys"]] == [
        (k["id"], k["label"]) for k in before["keys"]]
    assert send(device, key).status_code == 401
    owner.put("/api/apps/house/inbound/open", json={"open": True})
    assert send(device, key).status_code == 202


def test_a_door_the_owner_had_closed_is_still_closed_with_its_keys_after_remove_and_install(
        owner, device, core, tmp_path):
    folder = full_app(tmp_path, "house", permissions=["inbound"], inbound=EVENTS)
    install_folder(core, folder)
    key = make_key(owner)
    owner.put("/api/apps/house/inbound/open", json={"open": False})
    install.remove(core, "house", keep_data=True)
    install_folder(core, folder)
    after = owner.get("/api/apps/house/inbound").json()
    assert after["open"] is False and len(after["keys"]) == 1
    assert send(device, key).status_code == 401
    owner.put("/api/apps/house/inbound/open", json={"open": True})
    assert send(device, key).status_code == 202


def test_removing_an_app_with_its_data_leaves_neither_keys_nor_an_open_door_to_install_again(
        owner, device, core, tmp_path):
    folder = full_app(tmp_path, "house", permissions=["inbound"], inbound=EVENTS)
    install_folder(core, folder)
    key = make_key(owner)
    install.remove(core, "house", keep_data=False)
    install_folder(core, folder)
    after = owner.get("/api/apps/house/inbound").json()
    assert after["open"] is False and after["keys"] == []
    assert send(device, key).status_code == 401


def key_removals(core) -> list[dict]:
    return [e.payload for e in core.tape.after(0, limit=100000)
            if e.kind == "apps.inbound_key_removed"]


def test_a_different_app_installed_under_the_same_id_starts_with_none_of_the_kept_keys(
        owner, device, core, tmp_path):
    mine = full_app(tmp_path / "mine", "house", permissions=["inbound"], inbound=EVENTS)
    install_folder(core, mine)
    key = make_key(owner)
    made = owner.get("/api/apps/house/inbound").json()["keys"][0]
    assert send(device, key).status_code == 202
    install.remove(core, "house", keep_data=True)
    # Another app's author picked the same id, name and manifest: only where it came from
    # tells them apart, and that is not his to say.
    theirs = full_app(tmp_path / "theirs", "house", permissions=["inbound"], inbound=EVENTS)
    install_folder(core, theirs)
    after = owner.get("/api/apps/house/inbound").json()
    assert after["keys"] == [] and after["open"] is False
    assert (after["received"], after["recent"]) == (0, [])
    owner.put("/api/apps/house/inbound/open", json={"open": True})
    assert send(device, key).status_code == 401
    assert key_removals(core) == [{"app_id": "house", "key_id": made["id"],
                                   "label": "Home hub"}]
    install.remove(core, "house", keep_data=True)
    install_folder(core, mine)  # gone for good: the first app back does not bring them
    assert owner.get("/api/apps/house/inbound").json()["keys"] == []
    assert send(device, key).status_code == 401


def test_an_updated_app_is_still_the_same_app_when_it_is_installed_again_from_its_new_place(
        owner, device, core, tmp_path):
    install_folder(core, full_app(tmp_path / "first", "house", permissions=["inbound"],
                                  inbound=EVENTS))
    key = make_key(owner)
    newer = full_app(tmp_path / "newer", "house", permissions=["inbound"], inbound=EVENTS,
                     version="1.1.0")
    plan = update.plan_folder(core, "house", newer)
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert len(owner.get("/api/apps/house/inbound").json()["keys"]) == 1
    install.remove(core, "house", keep_data=True)
    install_folder(core, newer)
    after = owner.get("/api/apps/house/inbound").json()
    assert len(after["keys"]) == 1 and after["open"] is False
    owner.put("/api/apps/house/inbound/open", json={"open": True})
    assert send(device, key).status_code == 202 and key_removals(core) == []


def test_where_an_install_came_from_is_what_jimmyhq_saw_never_what_its_manifest_says(tmp_path):
    folder = {"kind": "folder", "repo": None}
    one, two = (tmp_path / "one", tmp_path / "two")
    assert install.origin_name("folder", folder, one, "house") != install.origin_name(
        "folder", folder, two, "house")
    assert install.origin_name("folder", folder, one, "house") == install.origin_name(
        "folder", folder, tmp_path / "x" / ".." / "one", "house")
    git = {"kind": "git", "repo": "https://example.org/house.git"}
    assert install.origin_name("git", git, None, "house") == "git:https://example.org/house.git"
    assert install.origin_name("bundled", folder, one, "house") == "example:house"
    assert install.origin_name("folder", folder, None, "house") is None


@pytest.mark.parametrize("kept, now, stays", [
    ("git:https://example.org/house.git", "git:https://example.org/house.git", True),
    ("git:https://example.org/house.git", "git:https://example.org/other.git", False),
    ("example:house", "example:house", True),
    (None, "folder:/srv/apps/house", False),  # an old folder install cannot be told apart
    ("example:house", None, False),
])
def test_keys_kept_by_an_install_made_before_origins_were_kept_go_back_only_to_the_same_app(
        core, kept, now, stays):
    store = inbound.service(core).store
    assert store.add_key("house", "Hub", "0" * 64, 5, 1800000000000)
    with store._lock:  # noqa: SLF001 - as a file from before version 6 has it
        store._db.execute("UPDATE inbound_apps SET origin=NULL")  # noqa: SLF001
    inbound.on_removed(core, "house", keep_data=True, origin=kept)
    inbound.on_installed(core, "house", now, actor="owner")
    assert (len(store.keys("house")) == 1) is stays and store.app("house")["open"] is False
    assert store.origin("house") == now


def test_a_version_5_apps_db_gains_the_install_origin_and_keeps_its_keys(tmp_path):
    path = tmp_path / "apps.db"
    conn = sqlite3.connect(path)
    conn.executescript(dbfile.STORE.schema)
    for step in dbfile.STORE.steps[:4]:
        storeversion.run_script(conn, step)
    conn.execute("INSERT INTO inbound_keys (id, app_id, label, digest, created) VALUES "
                 "('k1', 'alpha', 'Hub', ?, 1800000000000)", ("0" * 64,))
    conn.execute("INSERT INTO inbound_apps (app_id, open) VALUES ('alpha', 1)")
    conn.commit()
    conn.execute("PRAGMA user_version = 5")
    conn.close()

    opened = inbound.Store(path)
    try:
        assert [k["label"] for k in opened.keys("alpha")] == ["Hub"]
        assert opened.app("alpha")["open"] is True and opened.origin("alpha") is None
        opened.set_origin("alpha", "example:alpha")
        assert opened.origin("alpha") == "example:alpha"
    finally:
        opened.close()
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == dbfile.STORE.latest >= 6
    finally:
        conn.close()


# --- the manifest and the program ---------------------------------------------------------------


def manifest(**changes) -> dict:
    return {"manifest": 1, "id": "house", "name": "House", "summary": "Home events.",
            "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
            "permissions": ["inbound"], "server": {"entry": "house.main:app"},
            "inbound": EVENTS} | changes


def refused(**changes) -> ManifestError:
    with pytest.raises(ManifestError) as caught:
        parse_manifest(manifest(**changes))
    return caught.value


def test_a_manifest_reads_its_inbound_names():
    m = parse_manifest(manifest())
    assert [(i.name, i.label, i.help) for i in m.inbound] == [
        ("event", "Home events", "What your home says happened.")]
    assert parse_manifest(m.to_dict()) == m


def test_declaring_inbound_without_the_permission_is_refused_with_a_sentence():
    error = refused(permissions=[])
    assert error.fields == ("inbound",)
    assert "\"permissions\" does not ask for \"inbound\"" in str(error)


def test_asking_for_the_permission_without_declaring_inbound_is_refused_with_a_sentence():
    data = manifest()
    del data["inbound"]
    with pytest.raises(ManifestError) as caught:
        parse_manifest(data)
    assert caught.value.fields == ("permissions",)
    assert "\"inbound\" declares nothing" in str(caught.value)


def test_inbound_is_for_full_apps_only_with_short_names_and_at_most_eight():
    sandboxed = refused(tier="sandboxed", server=None)
    assert "inbound" in sandboxed.fields
    assert refused(inbound=[{"name": "Bad-Name", "label": "x"}]).fields == ("inbound[0].name",)
    assert refused(inbound=[{"name": "a", "label": "x"}, {"name": "a", "label": "y"}])
    many = [{"name": f"n{i}", "label": f"N{i}"} for i in range(9)]
    assert refused(inbound=many).fields == ("inbound",)
    assert refused(inbound=[{"name": "event", "label": "x", "body": {}}])


def test_a_program_may_handle_only_the_inbound_names_its_manifest_declares(core, tmp_path):
    from apps_full_helpers import AppClient

    home_app(core, tmp_path)
    install_full(core, tmp_path, "plain")
    with client_for(core) as web:
        house = AppClient(web, core.auth.mint_app_token("house"))
        said = house.put("/plugs", {"inbound": ["event"]}).json()
        assert said["ok"] and said["result"]["inbound"] == ["event"]
        assert not house.put("/plugs", {"inbound": ["doorbell"]}).json()["ok"]
        plain = AppClient(web, core.auth.mint_app_token("plain"))
        assert plain.put("/plugs", {"inbound": ["event"]}).json()["error"]["code"] \
            == "not_allowed"
        assert "inbound" not in plain.put("/plugs", {}).json()["result"]


def test_a_version_2_apps_db_gains_the_inbound_tables_and_keeps_its_rows(tmp_path):
    path = tmp_path / "apps.db"
    conn = sqlite3.connect(path)
    conn.executescript(dbfile.STORE.schema)
    storeversion.run_script(conn, dbfile.STORE.steps[0])
    conn.execute("INSERT INTO app_counts VALUES ('alpha', 3, 1800000000000)")
    conn.commit()
    conn.execute("PRAGMA user_version = 2")
    conn.close()

    opened = inbound.Store(path)
    try:
        assert opened.add_key("alpha", "Hub", "0" * 64, 5, 1800000000001)
    finally:
        opened.close()
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == dbfile.STORE.latest >= 3
        assert conn.execute("SELECT n FROM app_counts").fetchall() == [(3,)]
        assert conn.execute("SELECT open FROM inbound_apps").fetchall() == [(1,)]
    finally:
        conn.close()
