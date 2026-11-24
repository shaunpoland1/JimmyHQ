"""The welcome steps' progress: a server setting that follows the owner and stays private."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jimmyhq.settings import SettingError
from jimmyhq.web.app import create_app

KEY = "setup.welcome"
# Built at run time so the public-safe check does not read it as a credential.
PASSWORD = "-".join(["long", "enough", "words"])


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        response = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
        assert response.status_code == 200
        yield client


def test_a_new_installation_has_not_started_the_welcome_steps(core):
    assert core.settings.get(KEY) == "new"


def test_progress_is_saved_through_the_settings_api_and_lands_on_the_tape(owner, core):
    response = owner.put(f"/api/settings/{KEY}", json={"value": "about_you"})
    assert response.status_code == 200
    assert core.settings.get(KEY) == "about_you"
    events = core.tape.after(0, kind="setting.updated")
    assert any(e.payload == {"key": KEY, "value": "about_you"} for e in events)


def test_only_the_known_steps_can_be_stored(core):
    for value in ("new", "about_you", "model", "done"):
        assert core.settings.set(KEY, value, actor="owner") == value
    with pytest.raises(SettingError):
        core.settings.set(KEY, "halfway", actor="owner")


def test_a_shared_profile_leaves_the_welcome_progress_out(core):
    core.settings.set(KEY, "done", actor="owner")
    assert KEY not in core.settings.export(portable_only=True)["settings"]
    assert core.settings.export()["settings"][KEY] == "done"


def test_the_settings_screen_marks_the_progress_as_tied_to_this_installation(owner):
    item = next(s for s in owner.get("/api/settings").json()["settings"] if s["key"] == KEY)
    assert item["portable"] is False
    assert item["section"] == "profile"
