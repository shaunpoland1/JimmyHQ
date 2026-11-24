"""Typing in an app's secret, from the owner's side and from every side that must not."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from apps_full_helpers import AppClient, bearer, client_for, install_full
from jimmyhq.apps import secrets_entry
from jimmyhq.apps.secrets_entry import SecretRefused

PASSWORD = "correct horse battery"
KEY = "sk-test-SECRET-0123456789"
DECLARED = [{"name": "key", "label": "Booking key", "help": "From your account page."}]


@pytest.fixture
def web(core):
    with client_for(core) as client:
        assert client.post("/api/auth/setup", json={"name": "Sam",
                                                    "password": PASSWORD}).status_code == 200
        yield client


@pytest.fixture
def bare(core):
    """A client with no session: what a full app's program speaks with."""
    with client_for(core) as client:
        yield client


@pytest.fixture
def drafty(core, tmp_path):
    install_full(core, tmp_path, "drafty", secrets=DECLARED)
    install_full(core, tmp_path, "other", secrets=DECLARED)
    return "drafty"


def program(bare, core, app_id: str) -> AppClient:
    return AppClient(bare, core.auth.mint_app_token(app_id))


def put(web, value=KEY, app_id="drafty", name="key", **kw):
    return web.put(f"/api/apps/{app_id}/secrets/{name}", json={"value": value}, **kw)


def receipts(core):
    return [e for e in core.tape.after(0, limit=100000) if e.kind == "apps.secret_changed"]


def every_byte_but_the_store(core) -> bytes:
    """Everything JimmyHQ wrote under its data folder except the secret store itself."""
    store = Path(core.secrets._path).resolve()  # noqa: SLF001
    out = bytearray()
    for path in Path(core.config.data).rglob("*"):
        if path.is_file() and path.resolve() != store:
            out += path.read_bytes()
    return bytes(out)


# --- the owner ------------------------------------------------------------------------------


def test_the_owner_types_in_a_declared_secret_and_only_the_apps_program_reads_it(
        web, bare, core, drafty):
    answer = put(web)
    assert answer.status_code == 200 and answer.json() == {"name": "key", "set": True}
    assert program(bare, core, "drafty").ok("secrets.get", {"name": "key"}) == KEY
    assert program(bare, core, "other").ok("secrets.get", {"name": "key"}) is None


def test_the_list_says_set_or_not_set_with_the_manifests_words_and_never_the_value(
        web, core, drafty):
    before = web.get("/api/apps/drafty/secrets").json()
    assert before == {"secrets": [{"name": "key", "label": "Booking key",
                                   "help": "From your account page.", "set": False}]}
    put(web)
    after = web.get("/api/apps/drafty/secrets")
    assert after.json()["secrets"][0]["set"] is True
    assert KEY not in after.text and after.headers["cache-control"] == "no-store"


def test_a_pasted_secret_loses_the_spaces_and_line_ending_around_it(web, core, drafty):
    put(web, f"  {KEY}\n")
    assert core.secrets.get("app.drafty.key") == KEY


def test_clearing_removes_the_secret_and_clearing_again_changes_nothing(web, core, drafty):
    put(web)
    answer = web.delete("/api/apps/drafty/secrets/key")
    assert answer.json() == {"name": "key", "set": False}
    assert core.secrets.get("app.drafty.key") is None
    assert [e.payload["action"] for e in receipts(core)] == ["set", "cleared"]
    assert web.delete("/api/apps/drafty/secrets/key").status_code == 200
    assert len(receipts(core)) == 2


def test_the_receipt_says_which_secret_and_what_was_done_and_holds_nothing_of_the_value(
        web, core, drafty):
    put(web)
    [event] = receipts(core)
    assert event.payload == {"app_id": "drafty", "name": "key", "action": "set"}
    assert event.actor == "owner" and event.subject == "app:drafty"


# --- what is refused ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["other", "KEY", "key ", "app.drafty.key", "key.x", "_key"])
def test_a_name_the_manifest_does_not_declare_is_refused_and_nothing_is_kept(
        web, core, drafty, name):
    answer = put(web, name=name)
    assert answer.status_code == 404 and "nothing was kept" in answer.json()["detail"]
    assert "key" in answer.json()["detail"]  # it lists the names it declares
    assert core.secrets.names() == [] and receipts(core) == []


def test_a_secret_for_an_app_that_is_not_installed_is_refused(web, core, drafty):
    for app_id in ("nobody", "_installed", "Drafty"):
        assert put(web, app_id=app_id).status_code in (404, 405)
    assert core.secrets.names() == []


@pytest.mark.parametrize("body", [
    {"value": ""}, {"value": "   "}, {"value": 5}, {"value": None}, {"value": [KEY]},
    {"value": KEY, "also": 1}, {"secret": KEY}, {"value": f"{KEY}\nHost: x"},
    {"value": f"{KEY}\x00"},
])
def test_a_malformed_secret_is_refused_without_repeating_any_of_it(web, core, drafty, body):
    answer = web.put("/api/apps/drafty/secrets/key", json=body)
    assert answer.status_code == 422 and "Nothing was kept" in answer.text \
        or "nothing was kept" in answer.text
    assert KEY not in answer.text and core.secrets.names() == []


def test_a_secret_longer_than_the_setting_is_refused(web, core, drafty):
    core.settings.set(secrets_entry.MAX_CHARS, 16, actor="owner")
    answer = put(web, value=KEY)
    assert answer.status_code == 422 and "at most 16 characters" in answer.text
    assert KEY not in answer.text and core.secrets.names() == []
    huge = put(web, value="x" * 10_000)
    assert huge.status_code == 413 and core.secrets.names() == []


def test_a_body_that_is_not_json_is_refused_without_repeating_it(web, core, drafty):
    answer = web.put("/api/apps/drafty/secrets/key", content=KEY.encode(),
                     headers={"content-type": "application/json"})
    assert answer.status_code == 422 and KEY not in answer.text


# --- from the attacker's side ---------------------------------------------------------------


def test_another_apps_program_cannot_set_or_read_this_apps_secret(web, bare, core, drafty):
    put(web)
    other = core.auth.mint_app_token("other")
    for method in ("PUT", "GET", "DELETE"):
        path = "/api/apps/drafty/secrets" + ("" if method == "GET" else "/key")
        answer = bare.request(method, path, headers=bearer(other),
                             json={"value": "sk-test-other"})
        assert answer.status_code in (401, 403) and KEY not in answer.text
    refused = program(bare, core, "other").refused("secrets.get", {"name": "drafty.key"})
    assert refused["code"] == "not_found"
    assert core.secrets.get("app.drafty.key") == KEY


def test_this_apps_own_program_cannot_set_its_secret_either(bare, core, drafty):
    own = core.auth.mint_app_token("drafty")
    answer = bare.put("/api/apps/drafty/secrets/key", headers=bearer(own),
                     json={"value": "sk-test-planted"})
    assert answer.status_code in (401, 403) and core.secrets.names() == []


def test_a_token_with_every_scope_cannot_set_list_or_clear_a_secret(web, core, drafty):
    put(web)
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with client_for(core) as stranger:
        for method in ("PUT", "GET", "DELETE"):
            path = "/api/apps/drafty/secrets" + ("" if method == "GET" else "/key")
            answer = stranger.request(method, path, headers=bearer(secret),
                                      json={"value": "sk-test-other"})
            assert answer.status_code == 403 and KEY not in answer.text
    assert core.secrets.get("app.drafty.key") == KEY


@pytest.mark.parametrize("headers", [
    {"sec-fetch-dest": "iframe"}, {"sec-fetch-site": "cross-site"}, {"origin": "null"},
])
def test_a_request_from_an_apps_frame_or_another_site_is_refused(web, core, drafty, headers):
    answer = put(web, value="sk-test-planted", headers=headers)
    assert answer.status_code == 403 and core.secrets.names() == []
    assert web.get("/api/apps/drafty/secrets", headers=headers).status_code == 403


def test_the_secret_appears_in_no_response_no_log_line_and_nowhere_on_disk_but_the_store(
        web, core, drafty, caplog):
    caplog.set_level(logging.DEBUG)
    seen = [put(web).text, web.get("/api/apps/drafty/secrets").text,
            web.get("/api/apps/drafty").text, web.get("/api/apps/_installed").text,
            web.get("/api/settings").text,
            web.get("/api/tape", params={"limit": 500}).text]
    put(web, value=f"{KEY}\n\n")
    assert all(KEY not in text for text in seen)
    assert KEY not in caplog.text
    assert KEY.encode() not in every_byte_but_the_store(core)
    assert core.secrets.get("app.drafty.key") == KEY


# --- removing -------------------------------------------------------------------------------


@pytest.mark.parametrize("keep", [False, True])
def test_removing_the_app_removes_its_secrets_whether_its_data_is_kept_or_not(
        web, core, drafty, keep):
    put(web)
    put(web, app_id="other")
    assert web.post("/api/apps/drafty/remove", json={"keep_data": keep}).status_code == 200
    assert core.secrets.get("app.drafty.key") is None
    assert core.secrets.get("app.other.key") == KEY


# --- the rules on their own -----------------------------------------------------------------


def test_check_value_takes_one_line_of_text_within_the_limit():
    assert secrets_entry.check_value(f" {KEY} ", 100) == KEY
    for bad in ("", " ", "a\tb", "a\rb", 7, None):
        with pytest.raises(SecretRefused):
            secrets_entry.check_value(bad, 100)
    with pytest.raises(SecretRefused):
        secrets_entry.check_value("x" * 101, 100)


def test_read_value_wants_exactly_one_value_and_repeats_none_of_a_bad_body():
    assert secrets_entry.read_value(b'{"value": "abc"}', 100) == "abc"
    for raw in (b"", b"[]", b'{"value": "a", "b": 1}', KEY.encode(), b"\xff\xfe"):
        with pytest.raises(SecretRefused) as caught:
            secrets_entry.read_value(raw, 100)
        assert KEY not in caught.value.detail
