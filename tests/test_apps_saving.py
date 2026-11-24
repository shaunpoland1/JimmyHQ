"""``hq.ui.save``: the owner's limits on what a page may hand them to keep. The rules of
the save itself (the touch, the file's name and type, the web-page policy) are the shell's
and are tested in web/tests/apps.saving.test.ts; here are the settings the shell is told."""

from __future__ import annotations

import pytest

from jimmyhq.apps import files
from jimmyhq.settings import SettingError
from test_apps_bridge_fakes import WATER, call, installs, refused


@pytest.fixture
def apps(monkeypatch, tmp_path):
    made = installs(monkeypatch, tmp_path)
    made.add(WATER)
    (made.apps["water"].folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    return made


def test_the_limits_on_saving_a_file_are_settings_with_the_defaults_the_platform_promises(core):
    where = {x["key"]: x for x in core.settings.describe()["settings"]}
    for key in ("apps.save_max_mb", "apps.save_parts", "apps.save_per_minute",
                "apps.save_html"):
        assert where[key]["section"] == "apps_limits", key
    assert core.settings.get("apps.save_max_mb") == 8
    assert core.settings.get("apps.save_parts") == 5
    assert core.settings.get("apps.save_per_minute") == 12
    assert core.settings.get("apps.save_html") is True
    # Fine tuning is tucked away; the switch for web pages is not.
    assert where["apps.save_max_mb"]["advanced"] and where["apps.save_parts"]["advanced"]
    assert not where["apps.save_html"].get("advanced", False)
    # A limit on how fast files can arrive is about safety, so it stays in sight.
    assert not where["apps.save_per_minute"].get("advanced", False)


@pytest.mark.parametrize("key,bad", [("apps.save_max_mb", 0), ("apps.save_max_mb", 101),
                                     ("apps.save_parts", 0), ("apps.save_parts", 11),
                                     ("apps.save_per_minute", 0),
                                     ("apps.save_per_minute", 61),
                                     ("apps.save_html", "yes")])
def test_a_save_limit_outside_its_bounds_is_refused_and_the_old_one_stays(core, key, bad):
    before = core.settings.get(key)
    with pytest.raises(SettingError):
        core.settings.set(key, bad, actor="owner")
    assert core.settings.get(key) == before


def test_one_click_may_hand_over_at_most_ten_files_whatever_the_owner_sets(core):
    assert core.settings.set("apps.save_parts", 10, actor="owner") == 10
    with pytest.raises(SettingError):
        core.settings.set("apps.save_parts", 11, actor="owner")


def test_the_mount_answer_tells_the_shell_the_save_limits(core, apps):
    made = files.mint_mount(core, "water")
    assert (made["save_max_mb"], made["save_parts"], made["save_per_minute"],
            made["save_html"]) == (8, 5, 12, True)
    core.settings.set("apps.save_max_mb", 20, actor="owner")
    core.settings.set("apps.save_parts", 2, actor="owner")
    core.settings.set("apps.save_per_minute", 4, actor="owner")
    core.settings.set("apps.save_html", False, actor="owner")
    made = files.mint_mount(core, "water")
    assert (made["save_max_mb"], made["save_parts"], made["save_per_minute"],
            made["save_html"]) == (20, 2, 4, False)


def test_a_page_cannot_ask_the_server_to_save_a_file_through_the_bridge(core, apps):
    from test_apps_bridge_fakes import refused
    error = refused(core, "water", "ui.save", {"text": "x"})
    assert error.code == "invalid"
    assert "answered by the JimmyHQ screen itself" in error.message


# --- hq.ui.limits: a page, or a program, asks what it may save and how big an answer may be ----

LIMITS = {"save_max_mb", "save_parts", "save_per_minute", "save_html", "call_max_kb",
          "picture_px"}


def test_an_app_that_asks_its_limits_is_told_the_owners_numbers_and_nothing_else(core, apps):
    told = call(core, "water", "ui.limits", {})
    assert told == {"save_max_mb": 8, "save_parts": 5, "save_per_minute": 12,
                    "save_html": True, "call_max_kb": 1024, "picture_px": 2048}
    assert set(told) == LIMITS
    assert all(type(v) is int for k, v in told.items() if k != "save_html")
    assert told["save_html"] is True


def test_the_limits_an_app_is_told_follow_the_owners_settings_as_they_are_now(core, apps):
    core.settings.set("apps.save_max_mb", 20, actor="owner")
    core.settings.set("apps.save_parts", 2, actor="owner")
    core.settings.set("apps.save_per_minute", 4, actor="owner")
    core.settings.set("apps.save_html", False, actor="owner")
    core.settings.set("apps.call_max_kb", 256, actor="owner")
    core.settings.set("apps.picture_px", 1024, actor="owner")
    assert call(core, "water", "ui.limits", {}) == {
        "save_max_mb": 20, "save_parts": 2, "save_per_minute": 4, "save_html": False,
        "call_max_kb": 256, "picture_px": 1024}


def test_asking_the_limits_needs_no_permission_and_takes_no_arguments(core, apps):
    from jimmyhq.apps import bridge
    from jimmyhq.apps.permissions import SANDBOXED
    assert bridge.CALLS["ui.limits"].permission is None
    assert SANDBOXED in bridge.CALLS["ui.limits"].tiers
    assert call(core, "water", "ui.limits", None)["save_parts"] == 5
    error = refused(core, "water", "ui.limits", {"app": "other"})
    assert error.code == "invalid" and "Nothing" in error.message


def test_a_program_asks_its_limits_through_the_app_api_the_same_way(core, tmp_path):
    from jimmyhq_app import App
    from jimmyhq_app._transport import HttpTransport

    from apps_full_helpers import client_for, install_full

    install_full(core, tmp_path, "drafty")
    core.settings.set("apps.call_max_kb", 64, actor="owner")
    with client_for(core) as web:
        app = App(transport=HttpTransport(client=web, token=core.auth.mint_app_token("drafty")),
                  app_id="drafty", data_dir=tmp_path / "data")
        told = app.limits()
    assert told.call_max_kb == 64 and told.save_max_mb == 8 and told.save_html is True
    assert told.picture_px == 2048 and told.save_parts == 5 and told.save_per_minute == 12
