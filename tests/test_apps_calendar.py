"""The owner's time zone and week start, as a full app's program is told them: in
``/hello``, and as a settings delivery when either changes. Nothing else about the owner."""

from __future__ import annotations

from collections import namedtuple

import pytest

from apps_plugs_helpers import client_for, handed, install_full, offer, speak
from jimmyhq.apps import plugs
from jimmyhq.apps.plugs import settings as app_settings

Event = namedtuple("Event", "kind payload")


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def clock(core, tmp_path, web):
    install_full(core, tmp_path, "clock")
    install_full(core, tmp_path, "other")
    for app_id in ("clock", "other"):
        offer(speak(web, core, app_id))
    return "clock"


def test_hello_tells_a_program_the_owners_time_zone_and_week_start(web, core, clock):
    core.settings.set("owner.timezone", "Europe/Paris", actor="owner")
    core.settings.set("owner.week_starts", "sunday", actor="owner")
    said = speak(web, core, clock).get("/hello").json()["result"]
    assert said["timezone"] == "Europe/Paris" and said["week_start"] == "sunday"


def test_hello_says_nothing_else_about_the_owner(web, core, clock):
    core.settings.set("owner.name", "Someone", actor="owner")
    said = speak(web, core, clock).get("/hello").json()["result"]
    assert set(said) == {"app", "name", "version", "api", "permissions", "private",
                         "settings", "timezone", "week_start"}
    assert "Someone" not in str(said)


def test_hello_says_an_empty_time_zone_when_the_owner_left_it_to_the_computer(web, core,
                                                                             clock):
    said = speak(web, core, clock).get("/hello").json()["result"]
    assert said["timezone"] == "" and said["week_start"] == "monday"


def test_a_change_to_the_owners_calendar_is_handed_to_every_full_app_like_a_setting(
        web, core, clock):
    core.settings.set("owner.timezone", "Asia/Tokyo", actor="owner")
    core.settings.set("owner.week_starts", "saturday", actor="owner")
    core.settings.reset("owner.timezone", actor="owner")
    for app_id in ("clock", "other"):
        assert plugs.follow(core, app_id) == 3
        got = sorted((d["type"], d["data"]["key"]) for d in handed(speak(web, core, app_id)))
        assert got == [("settings", "owner.timezone"), ("settings", "owner.timezone"),
                       ("settings", "owner.week_start")]


def test_the_owners_other_settings_are_still_never_handed_to_an_app(web, core, clock):
    core.settings.set("owner.name", "Someone", actor="owner")
    core.settings.set("appearance.theme", "dark", actor="owner")
    assert plugs.follow(core, clock) == 0


def test_settings_delivery_names_the_owners_calendar_with_a_dot_no_app_key_can_have():
    assert app_settings.settings_delivery("a", Event("setting.updated",
                                                     {"key": "owner.timezone"})) == {
        "key": "owner.timezone"}
    assert app_settings.settings_delivery("a", Event("setting.reset",
                                                     {"key": "owner.week_starts"})) == {
        "key": "owner.week_start"}
    assert app_settings.settings_delivery("a", Event("setting.updated",
                                                     {"key": "owner.name"})) is None
    assert all("." in name for name in app_settings.OWNER_CALENDAR.values())
