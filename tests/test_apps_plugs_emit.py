"""``app.emit``: a full app's program nudges its own open pages. Tried from the attacker's
side: an event named like one JimmyHQ sends, too much data, a sandboxed app, another
app's events, and a token on the owner's route."""

from __future__ import annotations

import pytest

from apps_full_helpers import PASSWORD, bearer
from apps_plugs_helpers import client_for, install_full, speak
from jimmyhq.apps.plugs import emit


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def boards(core, tmp_path):
    install_full(core, tmp_path, "boards")
    install_full(core, tmp_path, "rival")
    return "boards"


def signed_in(web):
    assert web.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    return web


def test_an_app_emits_an_event_its_open_pages_can_read(web, core, boards):
    app = speak(web, core, boards)
    seq = app.ok("app.emit", {"event": "board.changed", "data": {"post": 12}})["seq"]
    owner = signed_in(web)
    found = owner.get("/api/apps/boards/emitted?after=0").json()
    assert found["events"] == [{"seq": seq, "event": "board.changed", "data": {"post": 12},
                                "at": found["events"][0]["at"]}]
    assert found["next"] == seq
    assert owner.get(f"/api/apps/boards/emitted?after={seq}").json()["events"] == []


def test_a_page_that_just_opened_starts_after_what_was_emitted_before(web, core, boards):
    app = speak(web, core, boards)
    seq = app.ok("app.emit", {"event": "board.changed"})["seq"]
    assert signed_in(web).get("/api/apps/boards/emitted").json() == {"events": [],
                                                                     "next": seq}


@pytest.mark.parametrize("name", sorted(emit.CORE_EVENTS))
def test_an_app_cannot_emit_an_event_jimmyhq_sends_itself(web, core, boards, name):
    error = speak(web, core, boards).refused("app.emit", {"event": name})
    assert error["code"] == "invalid" and "JimmyHQ sends itself" in error["message"]


@pytest.mark.parametrize("name", ["Board", "a b", "x" * 80, "", "a..b", 5])
def test_an_events_name_is_a_short_dotted_lower_case_name(web, core, boards, name):
    assert speak(web, core, boards).refused("app.emit", {"event": name})["code"] == "invalid"


def test_too_much_data_in_one_event_is_refused(web, core, boards):
    error = speak(web, core, boards).refused(
        "app.emit", {"event": "board.changed", "data": "x" * (emit.MAX_DATA + 1)})
    assert error["code"] == "too_large" and "Nothing was sent" in error["message"]


def test_one_apps_events_never_reach_another_apps_pages(web, core, boards):
    speak(web, core, "rival").ok("app.emit", {"event": "board.changed"})
    owner = signed_in(web)
    assert owner.get("/api/apps/boards/emitted?after=0").json()["events"] == []


def test_an_app_token_cannot_read_what_was_emitted(web, core, boards):
    token = core.auth.mint_app_token(boards)
    assert web.get("/api/apps/boards/emitted?after=0",
                   headers=bearer(token)).status_code == 401


def test_a_sandboxed_app_has_no_program_and_cannot_emit():
    assert emit.CALL.tiers == ("full",)
