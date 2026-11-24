"""API tokens as the owner manages them: the routes under /api/auth/tokens and
`jimmyhq owner token`. Written from the side of a token, or a stranger, that should not
be able to see, make or end one."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jimmyhq import cli
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def app(core):
    return create_app(core)


@pytest.fixture
def owner(app):
    with TestClient(app, base_url="http://testserver") as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
        yield c


def as_token(app, secret: str) -> TestClient:
    return TestClient(app, base_url="http://testserver",
                      headers={"authorization": f"Bearer {secret}"})


def make(owner, label="wall screen", level="reader"):
    r = owner.post("/api/auth/tokens", json={"label": label, "level": level})
    assert r.status_code == 200, r.text
    return r.json()


def tape_text(core) -> str:
    return json.dumps([e.model_dump() for e in core.tape.after(0)])


def test_a_reader_token_is_made_shown_once_and_can_read_but_not_change(owner, app, core):
    made = make(owner)
    token, secret = made["token"], made["secret"]
    assert secret.startswith("jhq_")
    assert token["label"] == "wall screen" and token["level"] == "reader"
    assert token["scopes"] == ["read"] and token["revoked"] is None
    assert "secret" not in json.dumps(owner.get("/api/auth/tokens").json())
    assert secret not in json.dumps(owner.get("/api/auth/tokens").json())
    with as_token(app, secret) as reader:
        assert reader.get("/api/tape").status_code == 200
        assert reader.put("/api/settings/appearance.theme",
                          json={"value": "dark"}).status_code == 403


def test_a_writer_token_can_also_make_changes_but_is_never_the_owner(owner, app):
    secret = make(owner, "script", "writer")["secret"]
    with as_token(app, secret) as writer:
        assert writer.get("/api/tape").status_code == 200
        # Settings and credentials stay with the signed-in owner.
        assert writer.put("/api/settings/appearance.theme",
                          json={"value": "dark"}).status_code == 403
        assert writer.get("/api/auth/tokens").status_code == 403


def test_the_token_itself_is_never_kept_on_the_tape_or_in_the_database(owner, core):
    made = make(owner)
    assert made["secret"] not in tape_text(core)
    assert made["secret"].encode() not in core.config.auth_db.read_bytes()
    events = [e for e in core.tape.after(0) if e.kind == "token.created"]
    assert [e.payload for e in events] == [{"token_id": made["token"]["id"],
                                            "name": "wall screen", "scopes": ["read"]}]


def test_revoking_a_token_stops_it_on_its_next_request_and_is_recorded(owner, app, core):
    made = make(owner)
    with as_token(app, made["secret"]) as reader:
        assert reader.get("/api/tape").status_code == 200
        r = owner.post(f"/api/auth/tokens/{made['token']['id']}/revoke")
        assert r.status_code == 200 and r.json()["token"]["revoked"] is not None
        assert reader.get("/api/tape").status_code == 401
    again = owner.post(f"/api/auth/tokens/{made['token']['id']}/revoke")
    assert again.status_code == 200
    assert [e.kind for e in core.tape.after(0)].count("token.revoked") == 1


def test_revoking_a_token_that_is_not_known_says_so_and_changes_nothing(owner):
    r = owner.post("/api/auth/tokens/01JNOSUCHTOKEN0000000000000/revoke")
    assert r.status_code == 404 and "Nothing was revoked" in r.json()["detail"]


def test_a_token_cannot_list_make_or_revoke_tokens(owner, app):
    made = make(owner, "script", "writer")
    with as_token(app, made["secret"]) as writer:
        assert writer.get("/api/auth/tokens").status_code == 403
        assert writer.post("/api/auth/tokens",
                           json={"label": "more", "level": "writer"}).status_code == 403
        assert writer.post(
            f"/api/auth/tokens/{made['token']['id']}/revoke").status_code == 403
    assert len(owner.get("/api/auth/tokens").json()["tokens"]) == 1


def test_nobody_signed_out_can_see_or_make_tokens(owner, app):
    with TestClient(app, base_url="http://testserver") as stranger:
        assert stranger.get("/api/auth/tokens").status_code == 401
        assert stranger.post("/api/auth/tokens",
                             json={"label": "x", "level": "reader"}).status_code == 401


def test_another_site_cannot_make_a_token_with_the_owners_cookie(owner):
    r = owner.post("/api/auth/tokens", json={"label": "x", "level": "writer"},
                   headers={"origin": "https://elsewhere.example"})
    assert r.status_code == 403
    assert owner.get("/api/auth/tokens").json()["tokens"] == []


@pytest.mark.parametrize("body", [
    {"label": "", "level": "reader"},
    {"label": "   ", "level": "reader"},
    {"label": "has:colon", "level": "reader"},
    {"label": "x" * 65, "level": "reader"},
    {"label": "tab\tin it", "level": "reader"},
    {"label": "fine", "level": "owner"},
    {"label": "fine"},
])
def test_a_token_needs_a_short_label_and_a_reader_or_writer_level(owner, body):
    r = owner.post("/api/auth/tokens", json=body)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert owner.get("/api/auth/tokens").json()["tokens"] == []


def test_the_list_shows_every_token_oldest_first_with_revoked_ones_marked(owner):
    first = make(owner, "first")["token"]["id"]
    make(owner, "second", "writer")
    owner.post(f"/api/auth/tokens/{first}/revoke")
    listed = owner.get("/api/auth/tokens").json()["tokens"]
    assert [(t["label"], t["level"], t["revoked"] is not None) for t in listed] == [
        ("first", "reader", True), ("second", "writer", False)]


# --- the command ---------------------------------------------------------------------


@pytest.fixture
def command(cfg, core, monkeypatch, capsys):
    """Run `jimmyhq owner token ...` against the data folder a running server has open."""
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))

    def go(*argv: str):
        code = cli.main(["owner", "token", *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return go


def test_the_command_makes_a_token_prints_it_once_and_the_server_takes_it(command, owner, app):
    code, out, _ = command("create", "wall screen")
    assert code == 0 and "only time it is shown" in out
    secret = out.strip().splitlines()[-1]
    assert secret.startswith("jhq_")
    with as_token(app, secret) as reader:
        assert reader.get("/api/tape").status_code == 200
        assert reader.put("/api/settings/appearance.theme",
                          json={"value": "dark"}).status_code == 403
    listed = owner.get("/api/auth/tokens").json()["tokens"]
    assert [(t["label"], t["level"]) for t in listed] == [("wall screen", "reader")]


def test_the_command_lists_tokens_without_their_secrets_and_revokes_one(command, owner, app):
    made = make(owner, "script", "writer")
    code, out, _ = command("list")
    assert code == 0 and made["token"]["id"] in out and "script" in out and "writer" in out
    assert made["secret"] not in out and "live" in out
    code, out, _ = command("revoke", made["token"]["id"])
    assert code == 0 and "no longer works" in out
    with as_token(app, made["secret"]) as gone:
        assert gone.get("/api/tape").status_code == 401
    assert "revoked" in command("list")[1]


def test_the_command_refuses_a_bad_label_or_an_unknown_id_and_says_what_to_do(command, owner):
    code, _, err = command("create", "has:colon", "--level", "writer")
    assert code == 1 and "Nothing was made" in err
    code, _, err = command("revoke", "01JNOSUCHTOKEN0000000000000")
    assert code == 1 and "List the tokens" in err
    assert owner.get("/api/auth/tokens").json()["tokens"] == []


def test_listing_when_there_are_no_tokens_says_how_to_make_one(command, owner):
    code, out, _ = command("list")
    assert code == 0 and "owner token create" in out


def test_token_with_nothing_after_it_prints_help(command, owner):
    code, out, _ = command()
    assert code == 2 and "create" in out and "revoke" in out
