"""Requests relayed by a tunnel on this computer, such as Tailscale Serve, tried from the
side of whoever is on the far end of it. They connect from the loopback address, as the
owner's own browser does, and must never be taken for the computer itself."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from jimmyhq import config
from jimmyhq.core import open_core
from jimmyhq.settings import SettingError
from jimmyhq.web.app import COOKIE, create_app
from jimmyhq.web.locality import client_key, is_local_name, is_local_request, over_https

PASSWORD = "correct horse battery"
LOOPBACK = ("127.0.0.1", 5000)
TAILNET_NAME = "box.example-tailnet.example"
FAR_DEVICE = "198.51.100.7"


def relayed(**extra: str) -> dict[str, str]:
    """The headers tailscaled adds to a request it forwards."""
    return {"x-forwarded-for": FAR_DEVICE, "x-forwarded-proto": "https",
            "x-forwarded-host": f"{TAILNET_NAME}:8440", **extra}


@pytest.fixture
def cfg(cfg):
    return replace(cfg, port=8440, allowed_hosts=("testserver", TAILNET_NAME))


@pytest.fixture
def app(core):
    return create_app(core)


@pytest.fixture
def at_the_machine(app):
    """The owner's browser on the computer itself, at http://127.0.0.1."""
    with TestClient(app, base_url="http://127.0.0.1:8440", client=LOOPBACK) as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
        c.post("/api/auth/logout")
        yield c


@pytest.fixture
def tunnel(app, at_the_machine):
    """A device on the tailnet, as tailscaled relays it: from 127.0.0.1, tailnet name."""
    with TestClient(app, base_url=f"http://{TAILNET_NAME}:8440", client=LOOPBACK,
                    headers=relayed()) as c:
        yield c


def fake_request(peer: str, host: str, headers: dict[str, str] | None = None,
                 scheme: str = "http") -> Request:
    raw = [(b"host", host.encode())] + [(k.encode(), v.encode())
                                        for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw,
                    "client": (peer, 5000), "scheme": scheme, "server": ("127.0.0.1", 8440),
                    "query_string": b""})


# --- the name a request is addressed to (finding 2.1) ---------------------------------


@pytest.mark.parametrize("host", ["localhost:8440", "127.0.0.1:8440", "[::1]:8440"])
def test_a_relayed_request_that_writes_a_local_name_is_refused(app, host):
    with TestClient(app, base_url="http://127.0.0.1:8440", client=LOOPBACK) as c:
        assert c.get("/api/health", headers={"host": host}).status_code == 200
        for header in ("x-forwarded-for", "forwarded", "x-real-ip"):
            r = c.get("/api/health", headers={"host": host, header: FAR_DEVICE})
            assert r.status_code == 421, header
            assert "does not answer this request" in r.json()["detail"]


def test_a_relayed_request_that_writes_the_bound_address_is_refused(cfg):
    core = open_core(replace(cfg, host="192.0.2.20"))
    try:
        with TestClient(create_app(core), client=LOOPBACK) as c:
            assert c.get("/api/health", headers={"host": "192.0.2.20:8440"}).status_code == 200
            assert c.get("/api/health", headers={
                "host": "192.0.2.20:8440", "x-forwarded-for": FAR_DEVICE}).status_code == 421
    finally:
        core.close()


def store_hosts_as_before(core, hosts: list[str]) -> None:
    """A value saved before the rule against this computer's own names existed."""
    core.tape.print("setting.updated", {"key": "access.allowed_hosts", "value": hosts},
                    actor="owner", subject="setting:access.allowed_hosts")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "192.0.2.20"])
def test_a_relayed_request_at_this_computers_own_name_is_refused_even_when_config_lists_it(
        cfg, host):
    listed = ("testserver", TAILNET_NAME, "localhost", "127.0.0.1", "::1", "192.0.2.20")
    core = open_core(replace(cfg, host="192.0.2.20", allowed_hosts=listed))
    try:
        with TestClient(create_app(core), client=LOOPBACK) as c:
            assert c.get("/api/health", headers={"host": f"{host}:8440"}).status_code == 200
            through = c.get("/api/health", headers={"host": f"{host}:8440", **relayed()})
            assert through.status_code == 421
            assert c.get("/api/health", headers={"host": f"{TAILNET_NAME}:8440",
                                                 **relayed()}).status_code == 200
    finally:
        core.close()


def test_a_relayed_request_at_a_local_name_saved_in_settings_before_the_rule_is_refused(
        cfg, caplog):
    core = open_core(cfg)
    try:
        store_hosts_as_before(core, ["localhost", TAILNET_NAME, "127.0.0.1"])
        # The rest of the list is kept: the owner's tailnet name still works.
        assert core.settings.get("access.allowed_hosts") == [TAILNET_NAME]
        with caplog.at_level(logging.WARNING, logger="jimmyhq.web"):
            app = create_app(core)
        said = " ".join(r.getMessage() for r in caplog.records if r.name == "jimmyhq.web")
        assert "127.0.0.1, localhost" in said and "ignored" in said
        with TestClient(app, base_url="http://localhost:8440", client=LOOPBACK,
                        headers=relayed()) as c:
            assert c.get("/api/auth/state").status_code == 421
        with TestClient(app, base_url=f"http://{TAILNET_NAME}:8440", client=LOOPBACK,
                        headers=relayed()) as c:
            assert c.get("/api/auth/state").status_code == 200
    finally:
        core.close()


@pytest.mark.parametrize("name", ["localhost", "LOCALHOST.", "127.0.0.1", "127.0.0.2", "::1",
                                  "::ffff:127.0.0.1", "app.localhost", "0.0.0.0"])
def test_the_owner_cannot_add_this_computers_own_name_to_the_allowed_hosts(core, name):
    with pytest.raises(SettingError) as refused:
        core.settings.set("access.allowed_hosts", [TAILNET_NAME, name], actor="owner")
    said = str(refused.value)
    assert "this computer's own" in said and "nothing was saved" in said
    assert core.settings.get("access.allowed_hosts") == []
    assert is_local_name(name)
    assert core.settings.set("access.allowed_hosts", [TAILNET_NAME], actor="owner") == [
        TAILNET_NAME]


def test_a_local_browser_behind_an_extension_that_adds_a_forwarding_header_is_told_what_happened(
        app):
    with TestClient(app, base_url="http://127.0.0.1:8440", client=LOOPBACK,
                    headers={"x-forwarded-for": "127.0.0.1"}) as c:
        r = c.get("/api/auth/state")
    assert r.status_code == 421
    detail = r.json()["detail"]
    assert "forwarding header" in detail and "browser extension" in detail
    assert "will not change this" in detail and "Nothing was done" in detail
    # A name nobody added is still told to add it.
    with TestClient(app, base_url="http://unknown.example:8440", client=LOOPBACK,
                    headers=relayed()) as c:
        assert "Add the name under Settings, Access" in c.get("/api/health").json()["detail"]


def test_a_relayed_request_to_a_name_the_owner_allowed_is_answered(tunnel, core):
    assert tunnel.get("/api/health").status_code == 200
    core.settings.set("access.allowed_hosts", ["other.example"], actor="owner")
    assert tunnel.get("/api/health", headers={"host": "other.example"}).status_code == 200
    assert tunnel.get("/api/health", headers={"host": "unknown.example"}).status_code == 421


def test_a_relayed_sign_in_with_a_local_name_never_reaches_the_password_check(app, core,
                                                                             at_the_machine):
    with TestClient(app, base_url="http://localhost:8440", client=LOOPBACK,
                    headers=relayed()) as c:
        assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 421
    assert core.auth._conn.execute("SELECT COUNT(*) FROM sign_in_attempts").fetchone()[0] == 0


def test_a_request_from_the_public_internet_is_refused_and_told_how_to_close_it(tunnel):
    r = tunnel.get("/api/health", headers={"tailscale-funnel-request": "?1"})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "tailscale funnel --https=8440 off" in detail and "Nothing was done" in detail
    assert "allow_public" in detail
    assert tunnel.post("/api/auth/login", json={"password": PASSWORD},
                       headers={"tailscale-funnel-request": "?1"}).status_code == 403


def test_the_public_internet_is_refused_even_with_a_local_name_and_no_other_header(app):
    with TestClient(app, base_url="http://localhost:8440", client=LOOPBACK) as c:
        assert c.get("/api/health", headers={"tailscale-funnel-request": "?1"}).status_code == 403


def test_the_owner_can_choose_to_answer_the_public_internet_in_config(cfg):
    core = open_core(replace(cfg, allow_public=True))
    try:
        with TestClient(create_app(core), base_url=f"http://{TAILNET_NAME}:8440",
                        client=LOOPBACK, headers=relayed()) as c:
            assert c.get("/api/health",
                         headers={"tailscale-funnel-request": "?1"}).status_code == 200
    finally:
        core.close()


def test_answering_the_public_internet_is_off_unless_config_toml_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("JIMMYHQ_DATA", str(tmp_path))
    monkeypatch.delenv("JIMMYHQ_ALLOW_PUBLIC", raising=False)
    assert config.load().allow_public is False
    (tmp_path / "config.toml").write_text("[server]\nallow_public = true\n", encoding="utf-8")
    assert config.load().allow_public is True


# --- who a request is counted as (finding 2.2) ----------------------------------------


def test_wrong_passwords_through_the_tunnel_never_lock_out_the_owner_at_the_machine(
        tunnel, at_the_machine):
    codes = [tunnel.post("/api/auth/login", json={"password": "a wrong guess"}).status_code
             for _ in range(6)]
    assert codes == [401, 401, 401, 401, 401, 429]
    assert at_the_machine.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_wrong_passwords_through_the_tunnel_are_counted_as_remote(tunnel, core):
    tunnel.post("/api/auth/login", json={"password": "a wrong guess"})
    rows = core.auth._conn.execute("SELECT client, local FROM sign_in_attempts").fetchall()
    assert [(r["client"], r["local"]) for r in rows] == [(f"forwarded:{FAR_DEVICE}", 0)]


def test_wrong_passwords_at_the_machine_do_not_lock_out_a_device_on_the_tailnet(
        tunnel, at_the_machine):
    for _ in range(5):
        at_the_machine.post("/api/auth/login", json={"password": "a wrong guess"})
    assert at_the_machine.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429
    assert tunnel.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_a_wrong_current_password_through_the_tunnel_does_not_lock_the_machine_out(
        tunnel, at_the_machine):
    assert tunnel.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    for _ in range(5):
        tunnel.post("/api/auth/password", json={"current": "a wrong guess",
                                                "new": "nine quiet copper kettles"})
    assert at_the_machine.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_the_session_cookie_is_secure_when_the_tunnel_says_the_browser_used_https(tunnel):
    r = tunnel.post("/api/auth/login", json={"password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith(COOKIE) and "secure" in cookie


def test_the_session_cookie_is_not_marked_secure_for_plain_http_at_the_machine(at_the_machine):
    r = at_the_machine.post("/api/auth/login", json={"password": PASSWORD})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_only_a_proxy_on_this_computer_is_believed_about_https():
    assert over_https(fake_request("127.0.0.1", "x", {"x-forwarded-proto": "https"}))
    assert over_https(fake_request("::1", "x", {"x-forwarded-proto": "HTTPS, http"}))
    assert not over_https(fake_request("192.0.2.9", "x", {"x-forwarded-proto": "https"}))
    assert not over_https(fake_request("127.0.0.1", "x", {"x-forwarded-proto": "http"}))
    assert over_https(fake_request("192.0.2.9", "x", scheme="https"))


def test_an_app_token_relayed_through_the_tunnel_is_refused(app, core, at_the_machine):
    token = core.auth.mint_app_token("drafts")
    auth = {"authorization": f"Bearer {token}"}
    with TestClient(app, base_url=f"http://{TAILNET_NAME}:8440", client=LOOPBACK,
                    headers=relayed()) as c:
        assert c.get("/api/app/v1/nothing-here", headers=auth).status_code == 401
    assert at_the_machine.get("/api/app/v1/nothing-here", headers=auth).status_code != 401


def test_a_relayed_request_is_never_counted_as_this_computer():
    direct = fake_request("127.0.0.1", "localhost:8440")
    assert client_key(direct) == "127.0.0.1" and is_local_request(direct)
    through = fake_request("127.0.0.1", "localhost:8440", {"x-forwarded-for": FAR_DEVICE})
    assert client_key(through) == f"forwarded:{FAR_DEVICE}" and not is_local_request(through)
    other_name = fake_request("127.0.0.1", f"{TAILNET_NAME}:8440")
    assert client_key(other_name) == "forwarded:unknown"
    assert client_key(fake_request("::1", "x", {"x-real-ip": FAR_DEVICE})) == (
        f"forwarded:{FAR_DEVICE}")


def test_a_proxy_at_the_bound_address_is_counted_apart_but_a_direct_caller_there_is_not():
    bound = "192.0.2.20"
    assert client_key(fake_request(bound, f"{bound}:8440"), bound) == bound
    assert client_key(fake_request(bound, "x", {"forwarded": "for=192.0.2.9"}), bound) == (
        "forwarded:for=192.0.2.9")
    assert client_key(fake_request("192.0.2.9", "x", {"x-forwarded-for": "1"}), bound) == (
        "192.0.2.9")


def test_a_very_long_forwarding_header_is_cut_short():
    key = client_key(fake_request("127.0.0.1", "x", {"x-forwarded-for": "9" * 5000}))
    assert key.startswith("forwarded:") and len(key) <= 140


def test_a_relayed_request_is_counted_under_the_address_the_nearest_relay_added():
    # A proxy appends the address it took the request from; what comes before is the
    # client's own. So the client cannot choose its key, however much it writes first.
    for sent in ("203.0.113.1", "A" * 200, "203.0.113.2, 203.0.113.3", ""):
        chain = f"{sent}, {FAR_DEVICE}" if sent else FAR_DEVICE
        key = client_key(fake_request("127.0.0.1", TAILNET_NAME, {"x-forwarded-for": chain}))
        assert key == f"forwarded:{FAR_DEVICE}", sent
    rotating = {client_key(fake_request("127.0.0.1", TAILNET_NAME,
                                        {"x-forwarded-for": f"203.0.113.{i}, {FAR_DEVICE}"}))
                for i in range(50)}
    assert rotating == {f"forwarded:{FAR_DEVICE}"}
    long_last = client_key(fake_request("127.0.0.1", "x", {"x-forwarded-for": "1, " + "9" * 500}))
    assert len(long_last) <= len("forwarded:") + 64


# --- nodes joining through the tunnel (review of PR 78, finding 1) ---------------------


@pytest.fixture
def join_code(app, at_the_machine):
    assert at_the_machine.post("/api/auth/login", json={"password": PASSWORD}).is_success
    return at_the_machine.post("/api/nodes/join-codes").json()["code"]


def test_wrong_join_codes_through_the_tunnel_never_stop_a_join_at_the_machine(
        app, at_the_machine, join_code):
    with TestClient(app, base_url=f"http://{TAILNET_NAME}:8440", client=LOOPBACK,
                    headers=relayed()) as tunnel:
        codes = [tunnel.post("/api/nodes/join", json={"code": "WRONG-CODE", "name": "evil"})
                 .status_code for _ in range(6)]
    assert codes[-1] == 429 and set(codes[:5]) == {401}
    r = at_the_machine.post("/api/nodes/join", json={"code": join_code, "name": "mybox"})
    assert r.status_code == 200, r.text


def test_wrong_join_codes_through_the_tunnel_are_counted_as_remote(app, core, join_code):
    with TestClient(app, base_url=f"http://{TAILNET_NAME}:8440", client=LOOPBACK,
                    headers=relayed()) as tunnel:
        tunnel.post("/api/nodes/join", json={"code": "WRONG-CODE", "name": "evil"})
    rows = core.auth._conn.execute("SELECT client, local FROM node_attempts").fetchall()
    assert [(r["client"], r["local"]) for r in rows] == [(f"forwarded:{FAR_DEVICE}", 0)]


def test_nothing_in_the_server_reads_the_peer_address_except_locality():
    from pathlib import Path

    import jimmyhq

    root = Path(jimmyhq.__file__).parent
    found = [p.relative_to(root).as_posix() for p in root.rglob("*.py")
             if "request.client" in p.read_text(encoding="utf-8")
             or 'scope["client"]' in p.read_text(encoding="utf-8")]
    assert found == ["web/locality.py"]
