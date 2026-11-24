from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jimmyhq.web.app import COOKIE, create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    r = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
    assert r.status_code == 200
    return client


def test_a_new_install_says_setup_is_needed_and_lets_nobody_in(client):
    assert client.get("/api/auth/state").json() == {
        "setup_needed": True, "signed_in": False, "owner_name": None,
    }
    for path in ("/api/settings", "/api/tape", "/api/tape/stats", "/api/status", "/api/feed"):
        assert client.get(path).status_code == 401, path


def test_setup_creates_the_owner_signs_them_in_and_cannot_be_run_twice(owner, core):
    assert owner.get("/api/auth/state").json() == {
        "setup_needed": False, "signed_in": True, "owner_name": "Sam",
    }
    again = owner.post("/api/auth/setup", json={"name": "Eve", "password": PASSWORD})
    assert again.status_code == 409
    assert core.settings.get("owner.name") == "Sam"
    assert [e.kind for e in core.tape.after(0)][:2] == ["system.opened", "owner.created"]


def test_setup_refuses_a_short_password_and_says_the_rule(client):
    r = client.post("/api/auth/setup", json={"name": "Sam", "password": "short"})
    assert r.status_code == 422
    assert "at least 10" in r.json()["detail"]


def test_sign_out_then_sign_in_with_the_password(owner):
    assert owner.post("/api/auth/logout").status_code == 200
    assert owner.get("/api/settings").status_code == 401
    assert owner.post("/api/auth/login", json={"password": "wrong password"}).status_code == 401
    assert owner.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert owner.get("/api/settings").status_code == 200


def test_the_session_cookie_cannot_be_read_by_scripts(client):
    r = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith(COOKIE) and "httponly" in cookie and "samesite=lax" in cookie


def test_sign_in_is_refused_after_too_many_wrong_passwords(owner):
    owner.post("/api/auth/logout")
    for _ in range(5):
        owner.post("/api/auth/login", json={"password": "wrong password"})
    r = owner.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 429
    assert "15 minutes" in r.json()["detail"]


def test_a_request_from_another_site_cannot_change_anything(owner):
    r = owner.put("/api/settings/appearance.theme", json={"value": "dark"},
                  headers={"origin": "https://elsewhere.example"})
    assert r.status_code == 403
    assert owner.put("/api/settings/appearance.theme", json={"value": "dark"},
                     headers={"origin": "http://testserver"}).status_code == 200


def test_settings_can_be_read_changed_and_reset(owner):
    assert owner.put("/api/settings/appearance.theme", json={"value": "dark"}).json() == {
        "key": "appearance.theme", "value": "dark",
    }
    listed = {s["key"]: s["value"] for s in owner.get("/api/settings").json()["settings"]}
    assert listed["appearance.theme"] == "dark"
    assert owner.delete("/api/settings/appearance.theme").json()["value"] == "system"


def test_a_bad_setting_value_comes_back_as_a_readable_message(owner):
    r = owner.put("/api/settings/access.session_days", json={"value": 0})
    assert r.status_code == 422
    assert r.json()["detail"] == "Stay signed in for (days) cannot be less than 1."


def test_a_read_token_can_read_but_cannot_change_settings(owner, core):
    _, token = core.auth.create_token("wall", ["read"])
    owner.cookies.clear()
    headers = {"authorization": f"Bearer {token}"}
    assert owner.get("/api/tape/stats", headers=headers).status_code == 200
    r = owner.put("/api/settings/appearance.theme", json={"value": "dark"}, headers=headers)
    assert r.status_code == 403
    assert owner.get("/api/tape/stats", headers={"authorization": "Bearer nope"}).status_code == 401


def test_the_tape_endpoint_returns_newest_first_and_pages_backwards(owner):
    events = owner.get("/api/tape", params={"limit": 2}).json()["events"]
    assert len(events) == 2 and events[0]["seq"] > events[1]["seq"]
    older = owner.get("/api/tape", params={"before": events[1]["seq"]}).json()["events"]
    assert all(e["seq"] < events[1]["seq"] for e in older)


def test_status_reports_each_process_and_marks_one_that_never_started(owner):
    body = owner.get("/api/status").json()
    by_name = {p["name"]: p for p in body["processes"]}
    assert by_name["web"]["ok"] is True
    assert by_name["worker"] == {"name": "worker", "ok": False, "last_seen_ms": None}


def test_an_unknown_api_path_is_a_404_not_the_web_app(owner):
    assert owner.get("/api/nothing/here").status_code == 404


def test_the_web_app_cannot_be_used_to_read_files_outside_itself(tmp_path, monkeypatch, core):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>")
    (tmp_path / "private.txt").write_text("private")
    monkeypatch.setenv("JIMMYHQ_WEB_DIST", str(dist))
    with TestClient(create_app(core)) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/wall").text == "<html>app</html>"
        assert "private" not in c.get("/%2e%2e/private.txt").text
        assert "private" not in c.get("/..%2fprivate.txt").text


def test_a_browser_gets_a_plain_page_and_other_clients_json_while_the_web_app_is_not_built(
        tmp_path, monkeypatch, core):
    from jimmyhq.web import dist as web_dist_module

    monkeypatch.setattr(web_dist_module, "__file__", str(tmp_path / "a" / "b" / "c" / "dist.py"))
    monkeypatch.delenv("JIMMYHQ_WEB_DIST", raising=False)
    with TestClient(create_app(core)) as c:
        page = c.get("/", headers={"accept": "text/html,application/xhtml+xml"})
        assert page.status_code == 503 and page.headers["content-type"].startswith("text/html")
        assert "has not been built yet" in page.text and "npm run build" in page.text
        assert "no need to restart" in page.text and '{"detail"' not in page.text
        plain = c.get("/wall", headers={"accept": "application/json"})
        assert plain.status_code == 503 and "npm run build" in plain.json()["detail"]
        assert c.get("/api/health").status_code == 200  # the API is there all the same
        missing = c.get("/api/nothing/here", headers={"accept": "text/html"})
        assert missing.status_code == 401 and "detail" in missing.json()


def test_a_web_app_built_after_the_start_is_served_without_a_restart(
        tmp_path, monkeypatch, core):
    dist = tmp_path / "dist"
    monkeypatch.setenv("JIMMYHQ_WEB_DIST", str(dist))
    from jimmyhq.web import dist as web_dist_module

    monkeypatch.setattr(web_dist_module, "__file__", str(tmp_path / "a" / "b" / "c" / "dist.py"))
    with TestClient(create_app(core)) as c:
        assert c.get("/", headers={"accept": "text/html"}).status_code == 503
        dist.mkdir()
        (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
        assert c.get("/", headers={"accept": "text/html"}).text == "<html>app</html>"
        assert c.get("/wall").text == "<html>app</html>"


# --- found by review: each test below pins one fixed defect ----------------------


def test_a_request_addressed_to_a_name_the_server_does_not_know_is_refused(client):
    """Another site can point its own name at this machine (DNS rebinding)."""
    headers = {"host": "elsewhere.example:8440", "origin": "http://elsewhere.example:8440"}
    r = client.post("/api/auth/setup", json={"name": "Eve", "password": PASSWORD}, headers=headers)
    assert r.status_code == 421
    assert "does not answer to this address" in r.json()["detail"]
    assert client.get("/", headers={"host": "elsewhere.example"}).status_code == 421
    assert client.get("/api/auth/state").json()["setup_needed"] is True


@pytest.mark.parametrize("host", ["localhost:8440", "127.0.0.1:8440", "[::1]:8440", "LOCALHOST"])
def test_the_local_machines_own_names_are_always_answered(client, host):
    assert client.get("/api/health", headers={"host": host}).status_code == 200


def test_a_name_added_in_settings_is_answered(owner):
    assert owner.get("/api/health", headers={"host": "box.example"}).status_code == 421
    owner.put("/api/settings/access.allowed_hosts", json={"value": ["Box.Example"]})
    assert owner.get("/api/health", headers={"host": "box.example:8440"}).status_code == 200


def test_the_owner_account_can_only_be_created_on_the_machine_itself_by_default(cfg, tmp_path):
    from dataclasses import replace

    from jimmyhq.core import open_core

    core = open_core(replace(cfg, remote_setup=False))
    try:
        with TestClient(create_app(core), client=("192.0.2.9", 5000)) as remote:
            r = remote.post("/api/auth/setup", json={"name": "Eve", "password": PASSWORD})
            assert r.status_code == 403 and "computer JimmyHQ runs on" in r.json()["detail"]
        local = {"base_url": "http://localhost:8440", "client": ("127.0.0.1", 5000)}
        # A proxy or tunnel on the same machine also connects from the local
        # address; what it forwards is not a local request.
        with TestClient(create_app(core), client=("127.0.0.1", 5000)) as proxied:
            r = proxied.post("/api/auth/setup", json={"name": "Eve", "password": PASSWORD})
            assert r.status_code == 403  # addressed to a name other than the machine's own
        with TestClient(create_app(core), **local) as forwarded:
            r = forwarded.post("/api/auth/setup", json={"name": "Eve", "password": PASSWORD},
                               headers={"x-forwarded-for": "192.0.2.9"})
            assert r.status_code == 421  # a relayed request is not answered for a local name
        with TestClient(create_app(core), **local) as direct:
            r = direct.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
            assert r.status_code == 200
    finally:
        core.close()


def test_one_client_guessing_passwords_does_not_lock_out_another(owner, core):
    owner.post("/api/auth/logout")
    with TestClient(create_app(core), client=("192.0.2.66", 5000)) as attacker:
        codes = [attacker.post("/api/auth/login", json={"password": "wrong password"}).status_code
                 for _ in range(6)]
    assert codes == [401, 401, 401, 401, 401, 429]
    assert owner.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/auth/setup", {"name": "", "password": PASSWORD}),
        ("post", "/api/auth/setup", {"name": "Sam"}),
        ("post", "/api/auth/login", {}),
        ("post", "/api/auth/login", {"password": 123}),
        ("post", "/api/auth/login", {"password": "x" * 5000}),
    ],
)
def test_a_malformed_request_gets_one_readable_sentence_that_does_not_echo_it(
    client, method, path, body
):
    r = getattr(client, method)(path, json=body)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str) and detail.endswith(".")
    assert "xxxx" not in detail and PASSWORD not in detail


def test_a_body_that_is_not_json_is_named_as_such(client):
    r = client.post("/api/auth/login", content=b'{"password": "abc', headers={
        "content-type": "application/json"})
    assert r.status_code == 422 and r.json()["detail"] == "The request: is not valid JSON."


def test_odd_query_values_are_a_readable_422_not_a_crash(owner):
    for query in ("limit=abc", "before=99999999999999999999999", "before=-5", "limit=0"):
        r = owner.get(f"/api/tape?{query}")
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), query
    assert owner.get("/api/tape?before=9223372036854775807").status_code == 200


def test_text_that_cannot_be_saved_is_refused_with_a_message(owner):
    r = owner.put("/api/settings/owner.name", content=b'{"value": "\\ud800"}',
                  headers={"content-type": "application/json"})
    assert r.status_code == 422 and "cannot be saved" in r.json()["detail"]


def test_an_open_feed_ends_when_its_session_is_signed_out(owner, core):
    import threading

    cookie = owner.cookies.get(COOKIE)
    chunks = []

    def listen():
        with TestClient(create_app(core)) as other:
            other.cookies.set(COOKIE, cookie)
            with other.stream("GET", "/api/feed") as response:
                for chunk in response.iter_text():
                    chunks.append(chunk)

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    for _ in range(100):
        if chunks:
            break
        threading.Event().wait(0.05)
    assert chunks and "connected" in chunks[0]
    owner.post("/api/auth/logout")
    core.tape.print("setting.reset", {"key": "after.logout"}, actor="owner")
    thread.join(6)
    assert not thread.is_alive(), "the stream stayed open after sign-out"
    assert "after.logout" not in "".join(chunks)


def test_paths_the_file_system_cannot_represent_and_missing_assets_do_not_crash(
    tmp_path, monkeypatch, core
):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>")
    monkeypatch.setenv("JIMMYHQ_WEB_DIST", str(dist))
    with TestClient(create_app(core)) as c:
        assert c.get("/a%00b").status_code == 200
        assert c.get("/" + "a" * 5000).status_code == 200
        assert c.get("/assets/missing.js").status_code == 404
        assert c.head("/").status_code == 200
        assert c.head("/api/health").status_code == 200


# --- frames ---------------------------------------------------------------------------------


def test_the_shells_pages_may_frame_only_this_server(tmp_path, monkeypatch, core):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    monkeypatch.setenv("JIMMYHQ_WEB_DIST", str(dist))
    with TestClient(create_app(core)) as client:
        for path in ("/", "/index.html", "/some/screen", "/api/health", "/api/auth/state"):
            answer = client.get(path)
            assert answer.status_code == 200, path
            assert answer.headers["content-security-policy"] == "frame-src 'self'", path
            # What every answer already carried is still there.
            assert answer.headers["x-frame-options"] == "SAMEORIGIN"
            assert answer.headers["x-content-type-options"] == "nosniff"
            assert answer.headers["referrer-policy"] == "same-origin"


def test_an_apps_files_keep_their_own_policy_not_the_shells(client):
    answer = client.get("/appfiles/" + "a" * 43 + "/index.html")
    assert answer.status_code == 404
    policy = answer.headers["content-security-policy"]
    assert policy.startswith("sandbox allow-scripts allow-forms; default-src 'none';")
    assert "connect-src 'none'" in policy and "form-action 'none'" in policy


@pytest.mark.parametrize("dest", ["iframe", "frame", "embed", "object", "fencedframe", "IFrame"])
@pytest.mark.parametrize("path", ["/", "/api/health", "/api/auth/state", "/api/auth/login",
                                  "/index.html", "/appfilesx/a/b.html"])
def test_nothing_but_an_apps_files_is_ever_answered_into_a_frame(owner, dest, path):
    answer = owner.get(path, headers={"sec-fetch-dest": dest})
    assert answer.status_code == 403
    assert answer.json() == {
        "detail": "JimmyHQ shows only an app's own files inside a frame."}
    assert "set-cookie" not in answer.headers
    posted = owner.post(path, headers={"sec-fetch-dest": dest}, json={"password": PASSWORD})
    assert posted.status_code == 403 and "set-cookie" not in posted.headers


def test_a_frame_asking_for_an_apps_files_is_let_through_to_them(client):
    answer = client.get("/appfiles/" + "a" * 43 + "/index.html",
                        headers={"sec-fetch-dest": "iframe"})
    assert answer.status_code == 404 and answer.json() == {
        "detail": "There is nothing at this address."}


def test_ordinary_requests_are_not_touched_by_the_frame_rule(owner):
    for dest in ("document", "empty", "script", "style", "image"):
        assert owner.get("/api/settings", headers={"sec-fetch-dest": dest}).status_code == 200
