"""``ui.count``: a full app's program says how many things wait on the owner, and the list
of installed apps the sidebar reads carries it without the app being open. Tried from the
attacker's side too: a number out of range, a number that is not one, another app's line,
a page that tries the program's call, and a switched-off app."""

from __future__ import annotations

import sqlite3

import pytest

from apps_full_helpers import PASSWORD, full_app
from apps_install_helpers import install_folder
from apps_plugs_helpers import client_for, install_full, speak
from jimmyhq import storeversion
from jimmyhq.apps import counts, dbfile, install, store


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def two(core, tmp_path):
    install_full(core, tmp_path, "alpha")
    install_full(core, tmp_path, "beta")
    return "alpha", "beta"


def signed_in(web):
    assert web.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    return web


def listed(owner) -> dict[str, dict]:
    body = owner.get("/api/apps").json()
    return {a["id"]: a for a in body["apps"]}


# --- what a program may say ---------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 17, 999])
def test_a_whole_number_from_0_to_999_is_taken(web, core, two, n):
    assert speak(web, core, "alpha").ok("ui.count", {"n": n}) == {"n": n}


@pytest.mark.parametrize("n", [-1, 1000, 2.5, "3", True, None, [3], {"n": 3}])
def test_a_number_out_of_range_or_not_whole_is_refused_and_nothing_is_kept(web, core, two, n):
    app = speak(web, core, "alpha")
    app.ok("ui.count", {"n": 4})
    error = app.refused("ui.count", {"n": n})
    assert error["code"] == "invalid"
    assert "whole number from 0 to 999" in error["message"] or "needs" in error["message"]
    assert "Nothing was" in error["message"]
    assert counts.counts(core).get("alpha") == 4


def test_a_count_with_no_number_or_an_extra_argument_is_refused(web, core, two):
    app = speak(web, core, "alpha")
    assert app.refused("ui.count", {})["code"] == "invalid"
    assert app.refused("ui.count", {"n": 3, "app": "beta"})["code"] == "invalid"
    assert counts.counts(core).get("alpha") is None


def test_a_count_needs_no_permission(web, core, two):
    assert store.get(core, "alpha")["permissions"] == []
    assert speak(web, core, "alpha").ok("ui.count", {"n": 2}) == {"n": 2}


def test_a_page_cannot_make_the_programs_call_and_a_sandboxed_app_has_no_program(
        web, core, two):
    owner = signed_in(web)
    answer = owner.post("/api/apps/alpha/bridge/ui.count", json={"args": {"n": 5}}).json()
    assert not answer["ok"] and answer["error"]["code"] == "invalid"
    assert counts.counts(core).get("alpha") is None
    assert counts.CALL.tiers == ("full",)


# --- what is kept ---------------------------------------------------------------------------


def test_each_app_keeps_its_own_latest_count(web, core, two):
    speak(web, core, "alpha").ok("ui.count", {"n": 3})
    speak(web, core, "beta").ok("ui.count", {"n": 8})
    speak(web, core, "alpha").ok("ui.count", {"n": 5})
    assert counts.counts(core).every() == {"alpha": 5, "beta": 8}


def test_a_count_of_zero_is_kept_as_zero_not_as_unknown(web, core, two):
    speak(web, core, "alpha").ok("ui.count", {"n": 6})
    speak(web, core, "alpha").ok("ui.count", {"n": 0})
    assert listed(signed_in(web))["alpha"]["count"] == 0


def test_the_list_of_installed_apps_carries_each_apps_count(web, core, two):
    speak(web, core, "alpha").ok("ui.count", {"n": 3})
    apps = listed(signed_in(web))
    assert apps["alpha"]["count"] == 3
    assert apps["beta"]["count"] is None  # it has not said


def test_the_count_survives_a_restart_of_jimmyhq(core, tmp_path, cfg, web):
    from jimmyhq.core import open_core

    install_full(core, tmp_path, "alpha")
    speak(web, core, "alpha").ok("ui.count", {"n": 4})
    again = open_core(cfg)
    try:
        assert [a["count"] for a in store.shell_list(again)] == [4]
    finally:
        again.close()


def test_an_app_switched_off_has_no_count_shown_and_switched_on_shows_its_last_until_it_speaks(
        web, core, two):
    app = speak(web, core, "alpha")
    app.ok("ui.count", {"n": 3})
    install.disable(core, "alpha")
    assert listed(signed_in(web))["alpha"]["count"] is None
    # Off, it has no token that works, so it cannot change the number meanwhile.
    assert app.post("/call/ui.count", {"args": {"n": 9}}).status_code in (401, 403)
    install.enable(core, "alpha")
    assert listed(web)["alpha"]["count"] == 3  # what it last said, until its program speaks


def test_removing_an_app_clears_its_count_and_a_new_install_starts_with_none(
        web, core, tmp_path, two):
    speak(web, core, "alpha").ok("ui.count", {"n": 3})
    speak(web, core, "beta").ok("ui.count", {"n": 9})
    install.remove(core, "alpha", keep_data=True)
    assert counts.counts(core).every() == {"beta": 9}
    install_folder(core, full_app(tmp_path / "again", "alpha"))
    assert listed(signed_in(web))["alpha"]["count"] is None


def test_the_shells_list_says_how_often_to_look_again_and_the_owner_can_change_it(web, core, two):
    owner = signed_in(web)
    assert owner.get("/api/apps").json()["refresh_s"] == 60
    core.settings.set("apps.count_refresh_s", 120, actor="owner")
    assert owner.get("/api/apps").json()["refresh_s"] == 120


# --- apps.db --------------------------------------------------------------------------------


def test_a_version_1_apps_db_gains_the_counts_table_and_keeps_its_rows(tmp_path):
    path = tmp_path / "apps.db"
    conn = sqlite3.connect(path)
    conn.executescript(dbfile.STORE.schema)  # the file as the first release made it
    conn.execute("INSERT INTO heartbeats (app_id, at) VALUES ('alpha', 1800000000000)")
    conn.execute("INSERT INTO quota_counts VALUES ('alpha', 'calls_per_day', 1, 7)")
    conn.commit()
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    opened = counts.AppCounts(path)
    try:
        opened.set("alpha", 3, 1800000000001)
        assert opened.get("alpha") == 3
    finally:
        opened.close()

    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == dbfile.STORE.latest >= 2
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "app_counts" in tables
        assert conn.execute("SELECT at FROM heartbeats WHERE app_id='alpha'").fetchall() == [
            (1800000000000,)]
        assert conn.execute("SELECT used FROM quota_counts").fetchall() == [(7,)]
    finally:
        conn.close()


def test_the_first_schema_is_not_changed_by_the_counts_step():
    assert "app_counts" not in dbfile.STORE.schema
    assert "app_counts" in dbfile.STORE.steps[0]
