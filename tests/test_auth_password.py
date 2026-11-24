"""Changing the owner's password, signing out other browsers, and the way back in
from the computer itself. Written from the side of someone who should be kept out."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from jimmyhq import cli, owner_cli
from jimmyhq.auth import AuthError
from jimmyhq.web.app import COOKIE, create_app

OLD = "correct horse battery"
NEW = "nine quiet copper kettles"
THIRD = "seven loud paper lanterns"


@pytest.fixture
def app(core):
    return create_app(core)


@pytest.fixture
def here(app):
    """The browser the owner is sitting at."""
    with TestClient(app, base_url="http://testserver") as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": OLD}).status_code == 200
        yield c


def another_browser(app, password=OLD):
    c = TestClient(app, base_url="http://testserver")
    assert c.post("/api/auth/login", json={"password": password}).status_code == 200
    return c


def kinds(core):
    return [e.kind for e in core.tape.after(0)]


# --- the store ---------------------------------------------------------------------


def test_a_changed_password_signs_in_and_the_old_one_no_longer_does(core):
    core.auth.create_owner(OLD)
    assert core.auth.change_password(OLD, NEW, "192.0.2.1", None) == ("ok", 0, None)
    assert core.auth.sign_in(OLD, "192.0.2.1") == "wrong"
    assert core.auth.sign_in(NEW, "192.0.2.1") == "ok"


def test_a_wrong_current_password_changes_nothing_and_ends_no_session(core):
    core.auth.create_owner(OLD)
    kept, other = core.auth.create_session(30), core.auth.create_session(30)
    assert core.auth.change_password(
        "not the password", NEW, "192.0.2.1", kept) == ("wrong", 0, None)
    assert core.auth.sign_in(NEW, "192.0.2.2") == "wrong"
    assert core.auth.sign_in(OLD, "192.0.2.2") == "ok"
    assert core.auth.session_valid(kept) and core.auth.session_valid(other)


def test_a_wrong_current_password_counts_as_a_failed_sign_in_and_locks_like_one(core):
    core.auth.create_owner(OLD)
    for _ in range(5):
        assert core.auth.change_password("a wrong guess", NEW, "192.0.2.66", None)[0] == "wrong"
    # Locked now: even the right current password is refused, and sign-in is too.
    assert core.auth.change_password(OLD, NEW, "192.0.2.66", None) == ("locked", 0, None)
    assert core.auth.sign_in(OLD, "192.0.2.66") == "locked"
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"  # nothing was changed


def test_wrong_sign_ins_and_wrong_current_passwords_share_one_count(core):
    core.auth.create_owner(OLD)
    for _ in range(3):
        core.auth.sign_in("a wrong guess", "192.0.2.66")
    for _ in range(2):
        core.auth.change_password("a wrong guess", NEW, "192.0.2.66", None)
    assert core.auth.change_password(OLD, NEW, "192.0.2.66", None)[0] == "locked"


def test_a_new_password_that_breaks_the_rules_is_refused_before_the_current_one_is_checked(core):
    core.auth.create_owner(OLD)
    with pytest.raises(AuthError, match="at least 10"):
        core.auth.change_password(OLD, "short", "192.0.2.1", None)
    with pytest.raises(AuthError, match="too long"):
        core.auth.change_password(OLD, "x" * 1025, "192.0.2.1", None)
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"


def test_the_same_password_again_is_refused_and_ends_no_session(core):
    core.auth.create_owner(OLD)
    other = core.auth.create_session(30)
    with pytest.raises(AuthError, match="Nothing was changed"):
        core.auth.change_password(OLD, OLD, "192.0.2.1", None)
    assert core.auth.session_valid(other)


def test_changing_the_password_ends_every_session_and_replaces_the_one_that_asked(core):
    core.auth.create_owner(OLD)
    mine, a, b = (core.auth.create_session(30) for _ in range(3))
    outcome, ended, fresh = core.auth.change_password(OLD, NEW, "192.0.2.1", mine, 30)
    assert (outcome, ended) == ("ok", 2)  # the count is of the others, as before
    assert fresh and fresh != mine and core.auth.session_valid(fresh)
    assert not core.auth.session_valid(mine)  # the old token stops working at once
    assert not core.auth.session_valid(a) and not core.auth.session_valid(b)
    assert core.auth.end_other_sessions(fresh) == 0  # exactly one session is left


def test_a_token_that_is_no_live_session_is_not_turned_into_one_by_a_password_change(core):
    core.auth.create_owner(OLD)
    other = core.auth.create_session(30)
    assert core.auth.change_password(
        OLD, NEW, "192.0.2.1", "not a session at all", 30) == ("ok", 1, None)
    assert not core.auth.session_valid(other)
    assert core.auth.end_all_sessions() == 0  # nothing was created


def test_a_failed_password_change_replaces_no_session(core):
    core.auth.create_owner(OLD)
    mine = core.auth.create_session(30)
    assert core.auth.change_password("a wrong guess", NEW, "192.0.2.1", mine, 30)[2] is None
    with pytest.raises(AuthError):
        core.auth.change_password(OLD, OLD, "192.0.2.1", mine, 30)
    with pytest.raises(AuthError):
        core.auth.change_password(OLD, "short", "192.0.2.1", mine, 30)
    assert core.auth.session_valid(mine)
    assert core.auth.end_other_sessions(mine) == 0  # and no second session appeared


def test_the_new_hash_and_the_new_session_are_stored_together_or_not_at_all(core, monkeypatch):
    core.auth.create_owner(OLD)
    mine, other = core.auth.create_session(30), core.auth.create_session(30)

    def no_token(_n):
        raise RuntimeError("no randomness today")

    monkeypatch.setattr("jimmyhq.auth.secrets.token_urlsafe", no_token)
    with pytest.raises(RuntimeError):
        core.auth.change_password(OLD, NEW, "192.0.2.1", mine, 30)
    monkeypatch.undo()
    assert core.auth.session_valid(mine) and core.auth.session_valid(other)
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"
    assert core.auth.sign_in(NEW, "192.0.2.1") == "wrong"


def test_ending_other_sessions_keeps_only_the_one_named_and_counts_the_rest(core):
    kept, a, b = (core.auth.create_session(30) for _ in range(3))
    assert core.auth.end_other_sessions(kept) == 2
    assert core.auth.session_valid(kept)
    assert not core.auth.session_valid(a) and not core.auth.session_valid(b)
    assert core.auth.end_other_sessions(kept) == 0


def test_a_token_that_is_no_session_keeps_nothing_alive(core):
    a = core.auth.create_session(30)
    assert core.auth.end_other_sessions("not a session at all") == 1
    assert not core.auth.session_valid(a)


def test_setting_the_password_without_the_old_one_ends_all_sessions_and_clears_a_lockout(core):
    core.auth.create_owner(OLD)
    a, b = core.auth.create_session(30), core.auth.create_session(30)
    for _ in range(5):
        core.auth.sign_in("a wrong guess", "192.0.2.1")
    assert core.auth.sign_in(OLD, "192.0.2.1") == "locked"
    assert core.auth.set_password(NEW) == 2
    assert not core.auth.session_valid(a) and not core.auth.session_valid(b)
    assert core.auth.sign_in(NEW, "192.0.2.1") == "ok"
    assert core.auth.sign_in(OLD, "192.0.2.2") == "wrong"


def test_setting_the_password_cannot_create_an_owner_where_there_is_none(core):
    with pytest.raises(AuthError, match="no owner yet"):
        core.auth.set_password(NEW)
    assert not core.auth.owner_exists()


def test_a_password_change_leaves_api_tokens_working(core):
    core.auth.create_owner(OLD)
    _, token = core.auth.create_token("wall", ["read"])
    core.auth.change_password(OLD, NEW, "192.0.2.1", None)
    core.auth.set_password(THIRD)
    assert core.auth.check_token(token) is not None


# --- the routes --------------------------------------------------------------------


def test_the_owner_changes_the_password_and_this_browser_stays_signed_in(here, app, core):
    r = here.post("/api/auth/password", json={"current": OLD, "new": NEW})
    assert r.status_code == 200 and r.json() == {"ok": True, "sessions_ended": 0}
    assert here.get("/api/settings").status_code == 200
    fresh = TestClient(app, base_url="http://testserver")
    assert fresh.post("/api/auth/login", json={"password": OLD}).status_code == 401
    assert fresh.post("/api/auth/login", json={"password": NEW}).status_code == 200


def test_the_token_this_browser_held_stops_working_when_the_password_changes(here, app):
    old = here.cookies.get(COOKIE)
    assert here.post("/api/auth/password", json={"current": OLD, "new": NEW}).status_code == 200
    thief = TestClient(app, base_url="http://testserver")
    thief.cookies.set(COOKIE, old)
    assert thief.get("/api/settings").status_code == 401
    assert thief.get("/api/auth/state").json()["signed_in"] is False


def test_a_password_change_hands_this_browser_a_new_cookie_that_works(here, app):
    old = here.cookies.get(COOKIE)
    r = here.post("/api/auth/password", json={"current": OLD, "new": NEW})
    new = r.cookies.get(COOKIE)
    assert new and new != old and here.cookies.get(COOKIE) == new
    assert len(r.headers.get_list("set-cookie")) == 1  # set once, not also by renewal
    assert here.get("/api/settings").status_code == 200
    assert here.get("/api/auth/state").json()["signed_in"] is True
    assert new not in r.text  # the token travels in the cookie only


def session_cookie_flags(response):
    """A Set-Cookie header for the session, with the token itself left out."""
    (header,) = [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{COOKIE}=")]
    return sorted(part.strip().lower() for part in header.split(";")[1:])


@pytest.mark.parametrize("base", ["http://testserver", "https://testserver"])
def test_the_new_cookie_is_set_exactly_as_sign_in_sets_it(app, core, base):
    core.auth.create_owner(OLD)
    core.settings.set("access.session_days", 9, actor="owner")
    with TestClient(app, base_url=base) as c:
        signed_in = c.post("/api/auth/login", json={"password": OLD})
        changed = c.post("/api/auth/password", json={"current": OLD, "new": NEW})
        assert changed.status_code == 200
    flags = session_cookie_flags(changed)
    assert flags == session_cookie_flags(signed_in)
    assert "httponly" in flags and "samesite=lax" in flags and f"max-age={9 * 86400}" in flags
    assert ("secure" in flags) == base.startswith("https")


def test_a_failed_change_over_the_api_sets_no_cookie_and_leaves_the_old_one_working(here):
    old = here.cookies.get(COOKIE)
    for body in ({"current": "a wrong guess", "new": NEW}, {"current": OLD, "new": "short"},
                 {"current": OLD, "new": OLD}):
        r = here.post("/api/auth/password", json=body)
        assert r.status_code in (403, 422)
        assert "set-cookie" not in r.headers
    assert here.cookies.get(COOKIE) == old
    assert here.get("/api/settings").status_code == 200


def test_other_browsers_stop_working_after_a_password_change(here, app):
    phone, laptop = another_browser(app), another_browser(app)
    assert phone.get("/api/settings").status_code == 200
    r = here.post("/api/auth/password", json={"current": OLD, "new": NEW})
    assert r.json()["sessions_ended"] == 2
    assert phone.get("/api/settings").status_code == 401
    assert laptop.get("/api/settings").status_code == 401
    assert phone.get("/api/auth/state").json()["signed_in"] is False
    assert here.get("/api/settings").status_code == 200


def test_a_wrong_current_password_over_the_api_changes_nothing_and_signs_nobody_out(here, app):
    phone = another_browser(app)
    r = here.post("/api/auth/password", json={"current": "a wrong guess", "new": NEW})
    assert r.status_code == 403  # not 401: this browser is still signed in
    assert "Nothing was changed" in r.json()["detail"]
    assert here.get("/api/settings").status_code == 200
    assert phone.get("/api/settings").status_code == 200
    fresh = TestClient(app, base_url="http://testserver")
    assert fresh.post("/api/auth/login", json={"password": NEW}).status_code == 401


def test_guessing_the_current_password_over_the_api_is_stopped_after_five_tries(here):
    for _ in range(5):
        r = here.post("/api/auth/password", json={"current": "a wrong guess", "new": NEW})
        assert r.status_code == 403
    r = here.post("/api/auth/password", json={"current": OLD, "new": NEW})
    assert r.status_code == 429 and "Nothing was changed" in r.json()["detail"]
    assert here.get("/api/settings").status_code == 200  # limited, not signed out


def test_the_route_refuses_a_new_password_that_breaks_the_rules_and_says_the_rule(here):
    r = here.post("/api/auth/password", json={"current": OLD, "new": "short"})
    assert r.status_code == 422 and "at least 10" in r.json()["detail"]
    r = here.post("/api/auth/password", json={"current": OLD, "new": "x" * 2000})
    assert r.status_code == 422
    assert "x" * 20 not in r.text  # what was sent is not echoed back


def test_sign_out_everywhere_else_ends_the_others_says_how_many_and_keeps_this_one(here, app):
    phone, laptop = another_browser(app), another_browser(app)
    r = here.post("/api/auth/sessions/end-others")
    assert r.status_code == 200 and r.json() == {"ok": True, "sessions_ended": 2}
    assert phone.get("/api/settings").status_code == 401
    assert laptop.get("/api/settings").status_code == 401
    assert here.get("/api/settings").status_code == 200
    assert here.post("/api/auth/sessions/end-others").json()["sessions_ended"] == 0


def test_nobody_signed_out_can_change_the_password_or_end_sessions(here, app):
    stranger = TestClient(app, base_url="http://testserver")
    assert stranger.post("/api/auth/password",
                         json={"current": OLD, "new": NEW}).status_code == 401
    assert stranger.post("/api/auth/sessions/end-others").status_code == 401
    stranger.cookies.set(COOKIE, "a made up session")
    assert stranger.post("/api/auth/password",
                         json={"current": OLD, "new": NEW}).status_code == 401
    assert here.get("/api/settings").status_code == 200


def test_an_api_token_cannot_change_the_password_or_end_sessions_even_knowing_it(here, app, core):
    _, token = core.auth.create_token("helper", ["read", "write"])
    robot = TestClient(app, base_url="http://testserver")
    robot.headers["authorization"] = f"Bearer {token}"
    assert robot.get("/api/settings").status_code == 200
    r = robot.post("/api/auth/password", json={"current": OLD, "new": NEW})
    assert r.status_code == 403
    assert robot.post("/api/auth/sessions/end-others").status_code == 403
    assert here.get("/api/settings").status_code == 200
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"
    assert "owner.password_changed" not in kinds(core)


def test_another_sites_page_cannot_change_the_password_with_the_owners_cookie(here, core):
    r = here.post("/api/auth/password", json={"current": OLD, "new": NEW},
                  headers={"origin": "http://elsewhere.example.com"})
    assert r.status_code == 403
    assert here.post("/api/auth/sessions/end-others",
                     headers={"origin": "http://elsewhere.example.com"}).status_code == 403
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"


# --- the record --------------------------------------------------------------------


def test_the_tape_says_the_password_changed_and_sessions_ended_but_only_on_success(
        here, app, core):
    another_browser(app)
    here.post("/api/auth/password", json={"current": "a wrong guess", "new": NEW})
    assert "owner.password_changed" not in kinds(core)
    here.post("/api/auth/password", json={"current": OLD, "new": NEW})
    another_browser(app, NEW)
    here.post("/api/auth/sessions/end-others")
    events = {e.kind: e for e in core.tape.after(0)}
    assert events["owner.password_changed"].payload == {"via": "settings", "sessions_ended": 1}
    assert events["owner.password_changed"].actor == "owner"
    assert events["owner.sessions_ended"].payload == {"sessions_ended": 1}


def test_no_password_reaches_the_tape_the_logs_the_replies_or_any_file(
        here, app, core, cfg, caplog, capsys, monkeypatch):
    caplog.set_level(logging.DEBUG)
    replies = []
    for body in ({"current": "a wrong guess!", "new": THIRD},
                 {"current": OLD, "new": "short"},
                 {"current": OLD, "new": NEW}):
        replies.append(here.post("/api/auth/password", json=body).text)
    monkeypatch.setattr(owner_cli, "at_a_terminal", lambda: True)
    monkeypatch.setattr(owner_cli.getpass, "getpass", lambda prompt="": THIRD)
    assert owner_cli.set_password(core, *_streams()) == 0
    said = capsys.readouterr()
    secrets = (OLD, NEW, THIRD, "a wrong guess!")
    record = json.dumps([[e.kind, e.actor, e.payload] for e in core.tape.after(0)])
    text = "\n".join([record, caplog.text, said.out, said.err, *replies, *_STREAMS])
    for secret in secrets:
        assert secret not in text
    core.auth._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for path in cfg.data.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            for secret in secrets:
                assert secret.encode() not in data, path.name
    # Only the two facts are recorded: where from, and how many sessions ended.
    changed = [e.payload for e in core.tape.after(0) if e.kind == "owner.password_changed"]
    assert changed == [{"via": "settings", "sessions_ended": 0},
                       {"via": "command", "sessions_ended": 1}]


_STREAMS: list[str] = []


def _streams():
    import io

    class Kept(io.StringIO):
        def write(self, text):
            _STREAMS.append(text)
            return super().write(text)

    return Kept(), Kept()


# --- the command -------------------------------------------------------------------


@pytest.fixture
def command(core, cfg, monkeypatch, capsys):
    """Run `jimmyhq owner ...` against the data folder a running server (`core`) has open."""
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))
    typed: list[str] = []
    prompts: list[str] = []

    def getpass(prompt=""):
        prompts.append(prompt)
        return typed.pop(0)

    monkeypatch.setattr(owner_cli.getpass, "getpass", getpass)
    monkeypatch.setattr(owner_cli, "at_a_terminal", lambda: True)

    def go(*lines: str, argv=("owner", "set-password")):
        typed[:] = lines
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    go.prompts = prompts
    return go


def test_the_command_sets_a_new_password_and_ends_all_sessions_while_the_server_runs(
        command, here, app, core):
    phone = another_browser(app)
    code, out, _ = command(NEW, NEW)
    assert code == 0 and "2 browsers were signed out" in out
    # The running server sees it on the next request: nobody is left signed in.
    assert here.get("/api/settings").status_code == 401
    assert phone.get("/api/settings").status_code == 401
    assert here.post("/api/auth/login", json={"password": OLD}).status_code == 401
    assert here.post("/api/auth/login", json={"password": NEW}).status_code == 200
    last = [e for e in core.tape.after(0) if e.kind == "owner.password_changed"][-1]
    assert last.payload == {"via": "command", "sessions_ended": 2} and last.actor == "owner"


def test_the_command_works_when_no_server_is_running(cfg, monkeypatch, capsys):
    from jimmyhq.core import open_core

    first = open_core(cfg)
    first.auth.create_owner(OLD)
    session = first.auth.create_session(30)
    first.close()
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))
    monkeypatch.setattr(owner_cli.getpass, "getpass", lambda prompt="": NEW)
    monkeypatch.setattr(owner_cli, "at_a_terminal", lambda: True)
    assert cli.main(["owner", "set-password"]) == 0
    assert "1 browser was signed out" in capsys.readouterr().out
    after = open_core(cfg)
    try:
        assert not after.auth.session_valid(session)
        assert after.auth.sign_in(NEW, "192.0.2.1") == "ok"
    finally:
        after.close()


def test_the_command_lets_a_locked_out_owner_back_in(command, here):
    for _ in range(5):
        here.post("/api/auth/login", json={"password": "a wrong guess"})
    assert here.post("/api/auth/login", json={"password": OLD}).status_code == 429
    assert command(NEW, NEW)[0] == 0
    assert here.post("/api/auth/login", json={"password": NEW}).status_code == 200


def test_the_command_takes_no_password_as_an_argument_and_does_not_repeat_one(
        command, here, core, capsys):
    code, out, err = command(argv=("owner", "set-password", NEW))
    assert code == 1 and "Nothing was changed" in err and "history" in err
    assert NEW not in out + err
    with pytest.raises(SystemExit) as stopped:
        command(argv=("owner", "set-password", "--password", NEW))
    assert stopped.value.code == 2
    assert NEW not in capsys.readouterr().err
    assert here.get("/api/settings").status_code == 200
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"
    assert command.prompts == []


def test_the_command_reads_nothing_from_a_pipe_and_says_what_to_do(
        command, here, monkeypatch, core):
    monkeypatch.setattr(owner_cli, "at_a_terminal", lambda: False)
    code, _, err = command(NEW, NEW)
    assert code == 1 and "Nothing was changed" in err and "terminal" in err
    assert command.prompts == []
    assert here.get("/api/settings").status_code == 200
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"


def test_two_passwords_that_differ_or_break_the_rules_change_nothing(command, here, core):
    code, _, err = command(NEW, NEW + "x")
    assert code == 1 and "do not match" in err and "Nothing was changed" in err
    code, _, err = command("short")
    assert code == 1 and "at least 10" in err and "Nothing was changed" in err
    assert here.get("/api/settings").status_code == 200
    assert core.auth.sign_in(OLD, "192.0.2.1") == "ok"
    assert "owner.password_changed" not in kinds(core)


def test_the_command_on_an_install_with_no_owner_changes_nothing_and_says_where_to_go(
        command, core):
    code, _, err = command(NEW, NEW)
    assert code == 1 and "no owner yet" in err and "Nothing was changed" in err
    assert not core.auth.owner_exists()


def test_owner_with_no_command_prints_help_and_changes_nothing(command, here):
    code, out, _ = command(argv=("owner",))
    assert code == 2 and "set-password" in out
    assert here.get("/api/settings").status_code == 200
