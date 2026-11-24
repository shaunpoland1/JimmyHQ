from __future__ import annotations

import sqlite3

import pytest

from jimmyhq.auth import AuthError

PASSWORD = "correct horse battery"


def test_an_owner_can_be_created_once_and_only_once(core):
    assert not core.auth.owner_exists()
    core.auth.create_owner(PASSWORD)
    assert core.auth.owner_exists()
    with pytest.raises(AuthError, match="already exists"):
        core.auth.create_owner("another password")


def test_a_short_password_is_refused_with_the_rule(core):
    with pytest.raises(AuthError, match="at least 10"):
        core.auth.create_owner("short")
    assert not core.auth.owner_exists()


def test_the_right_password_signs_in_and_the_wrong_one_does_not(core):
    core.auth.create_owner(PASSWORD)
    assert core.auth.sign_in(PASSWORD, "192.0.2.1") == "ok"
    assert core.auth.sign_in(PASSWORD + "x", "192.0.2.1") == "wrong"


def test_five_wrong_passwords_lock_that_client_and_a_right_one_first_clears_its_count(core):
    core.auth.create_owner(PASSWORD)
    for _ in range(4):
        assert core.auth.sign_in("wrong", "192.0.2.1") == "wrong"
    assert core.auth.sign_in(PASSWORD, "192.0.2.1") == "ok"
    for _ in range(5):
        assert core.auth.sign_in("wrong", "192.0.2.1") == "wrong"
    assert core.auth.sign_in(PASSWORD, "192.0.2.1") == "locked"


def test_one_client_guessing_cannot_lock_the_owner_out_from_another(core):
    core.auth.create_owner(PASSWORD)
    for _ in range(8):
        core.auth.sign_in("wrong", "192.0.2.66")
    assert core.auth.sign_in("wrong", "192.0.2.66") == "locked"
    assert core.auth.sign_in(PASSWORD, "192.0.2.1") == "ok"


def test_wrong_passwords_sent_together_cannot_slip_past_the_limit(core):
    import threading

    core.auth.create_owner(PASSWORD)
    barrier = threading.Barrier(12)
    outcomes = []

    def guess():
        barrier.wait()
        outcomes.append(core.auth.sign_in("wrong", "192.0.2.66"))

    threads = [threading.Thread(target=guess) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("wrong") == 5 and outcomes.count("locked") == 7


def test_guessing_from_many_addresses_never_locks_out_the_machine_itself(core):
    core.auth.create_owner(PASSWORD)
    for i in range(100):
        assert core.auth.sign_in("wrong", f"192.0.2.{i // 4}") == "wrong"
    assert core.auth.sign_in(PASSWORD, "192.0.2.200") == "locked"  # the overall ceiling
    assert core.auth.sign_in(PASSWORD, "127.0.0.1") == "ok"
    assert core.auth.sign_in(PASSWORD, "::ffff:127.0.0.1") == "ok"


def test_a_failed_check_is_not_counted_as_a_wrong_password(core, monkeypatch):
    from jimmyhq import auth

    core.auth.create_owner(PASSWORD)
    real = auth._hash

    def broken(*_):
        raise MemoryError

    monkeypatch.setattr(auth, "_hash", broken)
    for _ in range(6):
        with pytest.raises(MemoryError):
            core.auth.sign_in(PASSWORD, "192.0.2.1")
    monkeypatch.setattr(auth, "_hash", real)
    assert core.auth.sign_in(PASSWORD, "192.0.2.1") == "ok"


def test_a_password_that_cannot_be_encoded_is_refused_not_a_crash(core):
    with pytest.raises(AuthError, match="cannot be used"):
        core.auth.create_owner("\ud800" + PASSWORD)
    core.auth.create_owner(PASSWORD)
    assert core.auth.sign_in("\ud800" + PASSWORD, "192.0.2.1") == "wrong"


def test_a_session_is_valid_until_it_is_ended(core):
    token = core.auth.create_session(days=1)
    assert core.auth.session_valid(token)
    assert not core.auth.session_valid("not a session")
    assert not core.auth.session_valid(None)
    core.auth.end_session(token)
    assert not core.auth.session_valid(token)


def test_an_expired_session_is_not_valid(core):
    token = core.auth.create_session(days=0)
    assert not core.auth.session_valid(token)


def test_lowering_the_days_to_stay_signed_in_shortens_sessions_already_given_out(
        core, monkeypatch):
    from jimmyhq import auth

    start = 1_800_000_000_000
    clock = {"now": start}
    monkeypatch.setattr(auth, "now_ms", lambda: clock["now"])
    token = core.auth.create_session(days=30)
    assert core.auth.session_valid(token, 30)
    assert core.auth.session_valid(token, 1)  # cut down to one day from now
    clock["now"] = start + 10 * auth._DAY_MS  # ten days later, the setting still at 1
    assert not core.auth.session_valid(token, 1)
    assert not core.auth.session_valid(token, 30)  # raising it again does not bring it back


def test_a_session_under_the_current_days_is_left_as_it_was(core, monkeypatch):
    from jimmyhq import auth

    start = 1_800_000_000_000
    clock = {"now": start}
    monkeypatch.setattr(auth, "now_ms", lambda: clock["now"])
    token = core.auth.create_session(days=2)
    assert core.auth.session_valid(token, 30)
    clock["now"] = start + auth._DAY_MS + 1
    assert core.auth.session_valid(token, 30)
    clock["now"] = start + 2 * auth._DAY_MS
    assert not core.auth.session_valid(token, 30)


def test_lowering_the_setting_signs_out_a_browser_that_has_used_up_the_new_days(
        core, monkeypatch):
    from fastapi.testclient import TestClient

    from jimmyhq import auth
    from jimmyhq.web.app import create_app

    start = 1_800_000_000_000
    clock = {"now": start}
    monkeypatch.setattr(auth, "now_ms", lambda: clock["now"])
    with TestClient(create_app(core), base_url="http://testserver") as browser:
        assert browser.post("/api/auth/setup",
                            json={"name": "Sam", "password": PASSWORD}).is_success
        assert browser.put("/api/settings/access.session_days", json={"value": 1}).is_success
        assert browser.get("/api/settings").status_code == 200
        clock["now"] = start + 2 * auth._DAY_MS
        assert browser.get("/api/settings").status_code == 401
        assert browser.get("/api/auth/state").json()["signed_in"] is False


def test_an_api_token_carries_its_scopes_and_stops_working_when_revoked(core):
    token_id, token = core.auth.create_token("wall", ["read"])
    who = core.auth.check_token(token)
    assert who.actor == "token:wall"
    assert who.can("read") and not who.can("write") and not who.is_owner
    assert core.auth.revoke_token(token_id)
    assert core.auth.check_token(token) is None


def test_a_token_with_an_unknown_scope_is_refused(core):
    with pytest.raises(AuthError, match="Scopes must be chosen from"):
        core.auth.create_token("x", ["admin"])


def test_no_password_session_or_token_is_stored_in_the_clear(cfg, core):
    core.auth.create_owner(PASSWORD)
    session = core.auth.create_session(days=1)
    _, token = core.auth.create_token("wall", ["read"])
    conn = sqlite3.connect(cfg.auth_db)
    dump = "\n".join(str(row) for table in ("owner", "sessions", "tokens")
                     for row in conn.execute(f"SELECT * FROM {table}"))  # noqa: S608
    conn.close()
    for secret in (PASSWORD, session, token):
        assert secret not in dump


def test_secrets_are_kept_in_their_own_file_and_listed_by_name_only(cfg, core):
    core.secrets.set("provider.example.key", "value-123")
    assert core.secrets.get("provider.example.key") == "value-123"
    assert core.secrets.names() == ["provider.example.key"]
    assert core.secrets.delete("provider.example.key")
    assert core.secrets.get("provider.example.key") is None
    assert "value-123" not in "".join(str(e.payload) for e in core.tape.after(0))


def test_a_door_client_is_not_an_api_token_and_its_name_is_never_given_out_twice(core):
    client_id, token = core.auth.create_door_client("helper")
    assert token.startswith("jhqd_") and len(token) > 40
    assert core.auth.check_door_token(token) == (client_id, "helper")
    assert core.auth.check_token(token) is None
    for bad in (None, "", token + "x", token.upper(), "jhq_" + token[5:], "x" * 5000):
        assert core.auth.check_door_token(bad) is None
    with pytest.raises(AuthError, match="used for a client before"):
        core.auth.create_door_client("helper")
    for name in ("", "Has Space", "a:b", "UPPER", "x" * 33, None):
        with pytest.raises(AuthError):
            core.auth.create_door_client(name)
    new = core.auth.rotate_door_client(client_id)
    assert core.auth.check_door_token(token) is None
    assert core.auth.check_door_token(new) == (client_id, "helper")
    assert core.auth.revoke_door_client(client_id) and not core.auth.revoke_door_client(client_id)
    assert core.auth.check_door_token(new) is None
    assert core.auth.rotate_door_client(client_id) is None
    with pytest.raises(AuthError):
        core.auth.create_door_client("helper")
    [row] = core.auth.list_door_clients()
    assert row["name"] == "helper" and row["revoked"] and "digest" not in row
