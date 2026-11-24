"""An app's own settings: on the Settings screen for the owner to change, and a word to
the app's program when one changes. Tried from the attacker's side: another app's
setting, a core setting, and an app that is switched off."""

from __future__ import annotations

import pytest

from apps_plugs_helpers import client_for, handed, install_full, offer, speak
from jimmyhq import settings as registry
from jimmyhq.apps import install, plugs
from jimmyhq.apps.plugs import settings as app_settings

GOAL = {"key": "goal", "type": "int", "default": 8, "min": 1, "max": 30,
        "label": "Glasses a day", "help": "Your daily goal."}


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def counter(core, tmp_path, web):
    install_full(core, tmp_path, "counter", settings=[GOAL])
    install_full(core, tmp_path, "rival", settings=[GOAL])
    for app_id in ("counter", "rival"):
        offer(speak(web, core, app_id))
    return "counter"


def test_an_installed_apps_settings_are_on_a_page_of_their_own_under_apps(core, counter):
    described = core.settings.describe()
    where = {x["key"]: x["section"] for x in described["settings"]}
    assert where["app.counter.goal"] == "app.counter"
    assert described["layout"]["sections"]["app.counter"]["group"] == "apps"
    assert core.settings.get("app.counter.goal") == 8


def test_the_owner_changes_an_apps_setting_like_any_other_and_the_app_reads_it(web, core,
                                                                              counter):
    core.settings.set("app.counter.goal", 12, actor="owner")
    assert speak(web, core, counter).ok("settings.get", {"key": "goal"}) == 12


def test_a_changed_setting_is_handed_to_its_app_by_its_short_key(web, core, counter):
    app = speak(web, core, counter)
    core.settings.set("app.counter.goal", 12, actor="owner")
    core.settings.reset("app.counter.goal", actor="owner")
    assert plugs.follow(core, counter) == 2
    assert [(d["type"], d["data"]) for d in handed(app)] == [
        ("settings", {"key": "goal"}), ("settings", {"key": "goal"})]


def test_an_app_never_hears_of_another_apps_settings_or_the_cores(web, core, counter):
    core.settings.set("app.rival.goal", 3, actor="owner")
    core.settings.set("appearance.theme", "dark", actor="owner")
    assert plugs.follow(core, counter) == 0
    assert handed(speak(web, core, counter)) == []


def test_a_change_made_while_the_app_was_off_is_handed_over_when_it_is_on(web, core,
                                                                         counter):
    install.disable(core, counter)
    core.settings.set("app.counter.goal", 20, actor="owner")
    assert plugs.follow(core, counter) == 0
    install.enable(core, counter)
    assert plugs.follow(core, counter) == 1


def test_a_removed_apps_settings_page_is_taken_off_the_settings_screen(core, counter):
    install.remove(core, counter)
    assert "app.counter.goal" not in {s.key for s in registry.declared()}
    assert "app.counter" not in dict(registry.sections())
    assert "app.rival.goal" in {s.key for s in registry.declared()}


def test_declaring_again_changes_nothing(core, counter):
    assert app_settings.declare_all(core) == []


def test_the_line_under_an_apps_settings_page_is_one_short_sentence(core, counter):
    import dataclasses

    from jimmyhq.apps import store

    m = store.manifest_of(core, counter)
    for summary in ("word " * 39 + "end", "Counts things"):
        line = app_settings.page_line(dataclasses.replace(m, summary=summary))
        assert line.endswith(".") and len(line) <= 140


def test_a_settings_delivery_is_only_for_a_key_of_the_apps_own():
    class Event:
        def __init__(self, kind, key):
            self.kind, self.payload = kind, {"key": key}

    assert app_settings.settings_delivery("a", Event("setting.updated", "app.a.goal")) == {
        "key": "goal"}
    assert app_settings.settings_delivery("a", Event("setting.updated", "app.ab.goal")) is None
    assert app_settings.settings_delivery("a", Event("setting.updated", "app.a.")) is None
    assert app_settings.settings_delivery("a", Event("bench.decided", "app.a.goal")) is None


def test_withdrawing_a_settings_page_takes_its_settings_with_it():
    registry.section("app.scratch", "Scratch", group="apps", description="A page.")
    registry.declare(registry.Setting(key="app.scratch.on", section="app.scratch",
                                      type="bool", default=True, label="On", help="On."))
    assert registry.withdraw_section("app.scratch") == ["app.scratch.on"]
    assert "app.scratch" not in dict(registry.sections())
