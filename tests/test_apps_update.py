"""Updating, rolling back and removing an app, tried from the owner's side and the
attacker's: an update that quietly asks for more, a rollback to a version that was never
installed, a remove-with-erase that leaves something behind, a path that leaves the app's
folder. No network: git runs against local repositories, uv is a stand-in."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps_full_helpers import bearer, full_app, install_full
from apps_install_helpers import (
    URL,
    commit_all,
    example,
    install_folder,
    make_repo,
    staging,
    use_local,
    write_manifest,
)
from jimmyhq import bench
from jimmyhq.apps import environment, install, runtime, store, update
from jimmyhq.apps.plugs import crashes
from jimmyhq.apps.store import AppError
from jimmyhq.ids import now_ms
from jimmyhq.web.app import create_app
from test_apps_bridge_fakes import call

PASSWORD = "correct horse battery"
NET = "net:api.example.com"


def events(core, kind: str | None = None):
    found = [e for e in core.tape.after(0, limit=5000)]
    return [e for e in found if kind is None or e.kind == kind]


def kinds_of(core) -> list[str]:
    return [e.kind for e in events(core) if e.kind.startswith("apps.")]


def marker() -> str:
    return "zq" + "upd" + "marker" + "5521"  # built here, so it is in no file of the repo


@pytest.fixture
def water(core, tmp_path):
    install_folder(core, example(tmp_path, "water"))
    return "water"


def newer(tmp_path: Path, version: str, **changes) -> Path:
    """A copy of the water example at another version, free to change."""
    folder = example(tmp_path / version, "water")
    write_manifest(folder, version=version, **changes)
    return folder


def put_data(core, app_id: str, text: str) -> Path:
    data = store.data_dir(core, app_id)
    data.mkdir(parents=True, exist_ok=True)
    (data / "app.db").write_text(text, encoding="utf-8")
    return data


def updated(core, tmp_path, version: str = "1.1.0", **changes) -> dict:
    plan = update.plan_folder(core, "water", newer(tmp_path, version, **changes))
    return update.confirm_update(core, plan["plan"], plan["fingerprint"],
                                 typed_name=plan["app"]["name"])


# --- the rules, one by one ---------------------------------------------------------------------


def test_versions_are_ordered_as_semantic_versions():
    order = ["0.9.9", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0", "1.0.1", "1.2.0",
             "1.10.0", "2.0.0"]
    assert sorted(order, key=update.version_key) == order


def test_a_kept_version_is_named_by_a_version_and_a_hash_and_never_by_a_path():
    assert update.check_version_name("1.0.0-0a1b2c3d") == "1.0.0-0a1b2c3d"
    for bad in ("../../etc", "1.0.0-0a1b2c3d/..", "1.0.0", "..-0a1b2c3d", "", None, 7,
                "1.0.0-0a1b2c3d\\x", "/abs-0a1b2c3d"):
        with pytest.raises(AppError) as caught:
            update.check_version_name(bad)
        assert "Nothing was changed" in caught.value.detail


def test_a_full_app_has_stopped_only_when_a_fresh_status_says_so():
    asked = 10_000
    now = asked + 1000
    running = {"written": asked + 500, "apps": {"a": {"state": "running"}}}
    assert runtime.stopped({}, "a", asked, now=now)  # no supervisor at all
    assert runtime.stopped({"written": 1, "apps": {}}, "a", asked, now=asked + 10**7)  # stale
    assert not runtime.stopped(running, "a", asked, now=now)
    assert not runtime.stopped({"written": asked - 1, "apps": {}}, "a", asked, now=now)
    assert not runtime.stopped({"written": asked + 1, "apps": {"a": {"state": "stopping"}}},
                               "a", asked, now=now)
    assert runtime.stopped({"written": asked + 1, "apps": {}}, "a", asked, now=now)
    assert runtime.stopped({"written": asked - 1, "apps": {"a": {"state": "crashing"}}},
                           "a", asked, now=now)


def test_a_paused_full_app_is_left_out_of_run_json_until_it_is_resumed(core, tmp_path):
    install_full(core, tmp_path, "steady")
    listed = lambda: [a["id"] for a in json.loads(  # noqa: E731
        runtime.run_file(core.config.data).read_text(encoding="utf-8"))["apps"]]
    runtime.publish(core)
    assert listed() == ["steady"]
    runtime.pause(core, "steady")
    try:
        assert listed() == []
    finally:
        runtime.resume(core, "steady")
    assert listed() == ["steady"]


def test_a_crash_loop_began_after_an_update_only_when_the_new_version_never_ran_steadily():
    row = {"switched": {"how": "updated", "at": 1000},
           "previous": {"version": "1.0.0", "tree": "a" * 64}}
    assert update.began_after_update(row, {"restarts": [1100, 1200, 1300], "failures": 3})
    assert not update.began_after_update(row, {"restarts": [1100, 1200, 1300], "failures": 2})
    assert not update.began_after_update(row, {"restarts": [500], "failures": 5})
    assert not update.began_after_update(row | {"switched": {"how": "rolled_back", "at": 1000}},
                                         {"restarts": [1100], "failures": 1})
    assert not update.began_after_update({}, {"restarts": [1100], "failures": 1})


# --- updating -------------------------------------------------------------------------------


def test_a_new_version_is_staged_beside_the_old_and_nothing_changes_before_consent(
        core, tmp_path, water):
    before = store.code_dir(core, water)
    plan = update.plan_folder(core, water, newer(tmp_path, "1.1.0"))
    assert plan["update"]["from_version"] == "1.0.0" and plan["app"]["version"] == "1.1.0"
    assert plan["update"]["ask"] == [] and plan["update"]["needs_consent"] is False
    assert store.get(core, water)["version"] == "1.0.0" and before.is_dir()
    assert len(staging(core)) == 1
    assert "apps.updated" not in kinds_of(core)


def test_an_update_that_asks_for_nothing_new_is_put_in_place_with_its_data_copied_first(
        core, tmp_path, water):
    put_data(core, water, "glasses: 3")
    old_name = update.current_name(store.get(core, water))
    done = updated(core, tmp_path)
    assert done["version"] == "1.1.0"
    assert kinds_of(core) == ["apps.installed", "apps.updated"]
    event = events(core, "apps.updated")[0]
    assert event.payload["from_version"] == "1.0.0" and event.actor == "owner"
    backup = update.backup_dir(core, water, old_name)
    assert (backup / "app.db").read_text(encoding="utf-8") == "glasses: 3"
    assert (store.data_dir(core, water) / "app.db").read_text(encoding="utf-8") == "glasses: 3"
    assert staging(core) == []
    assert [v["version"] for v in done["versions"]] == ["1.1.0", "1.0.0"]


def test_an_update_that_adds_a_permission_lists_only_what_is_new_and_records_the_consent(
        core, tmp_path, water):
    plan = update.plan_folder(core, water, newer(tmp_path, "1.1.0",
                                                 permissions=["tasks.write", NET]))
    assert [line["id"] for line in plan["update"]["ask"]] == [NET]
    assert plan["update"]["needs_consent"] is True
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert kinds_of(core) == ["apps.installed", "apps.permissions_granted", "apps.updated"]
    granted = events(core, "apps.permissions_granted")[0].payload
    assert granted["added"] == [NET] and granted["version"] == "1.1.0"
    assert store.granted(core, water) == ("tasks.write", NET)


def test_an_update_that_quietly_adds_a_permission_after_it_was_shown_is_refused(
        core, tmp_path, water):
    plan = update.plan_folder(core, water, newer(tmp_path, "1.1.0"))
    staged = staging(core)[0] / "tree"
    write_manifest(staged, version="1.1.0", permissions=["tasks.write", NET])
    with pytest.raises(AppError) as caught:
        update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert caught.value.status == 409 and "Nothing was changed" in caught.value.detail
    assert store.get(core, water)["version"] == "1.0.0"
    assert store.granted(core, water) == ("tasks.write",)
    assert staging(core) == [] and kinds_of(core) == ["apps.installed"]


def test_an_update_whose_grant_changed_since_it_was_shown_is_refused(core, tmp_path, water):
    plan = update.plan_folder(core, water, newer(tmp_path, "1.1.0",
                                                 permissions=["tasks.write", NET]))
    updated(core, tmp_path, "1.0.5")  # something else moved the app on in between
    with pytest.raises(AppError) as caught:
        update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert "changed after this update was shown" in caught.value.detail
    assert NET not in store.granted(core, water)


def test_an_update_needs_the_plans_own_fingerprint(core, tmp_path, water):
    plan = update.plan_folder(core, water, newer(tmp_path, "1.1.0"))
    with pytest.raises(AppError) as caught:
        update.confirm_update(core, plan["plan"], "0" * 64)
    assert caught.value.status == 409 and store.get(core, water)["version"] == "1.0.0"


def test_an_install_plan_cannot_be_confirmed_as_an_update(core, tmp_path, water):
    plan = install.plan_folder(core, example(tmp_path, "books_read"))
    with pytest.raises(AppError) as caught:
        update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert caught.value.status == 404 and store.get(core, "books_read") is None


def test_the_system_never_puts_in_place_an_update_that_asks_for_more(core, tmp_path, water):
    plan = update.plan_folder(core, water, newer(tmp_path, "1.1.0",
                                                 permissions=["tasks.write", NET]))
    with pytest.raises(AppError) as caught:
        update.confirm_update(core, plan["plan"], plan["fingerprint"], actor="system")
    assert caught.value.status == 403 and "only you can agree" in caught.value.detail
    assert store.get(core, water)["version"] == "1.0.0"


def test_a_sandboxed_app_that_becomes_full_needs_its_name_typed(core, tmp_path):
    folder = full_app(tmp_path / "one", "shifty")
    write_manifest(folder, tier="sandboxed", server=None)
    data = json.loads((folder / "jimmyhq.app.json").read_text(encoding="utf-8"))
    del data["server"]
    data["screens"] = [{"id": "main", "label": "Shifty", "entry": "described"}]
    data["tables"] = {"note": {"fields": {"title": {"type": "text"}}}}
    data["views"] = [{"id": "all", "label": "All", "table": "note"}]
    (folder / "jimmyhq.app.json").write_text(json.dumps(data), encoding="utf-8")
    install_folder(core, folder)
    plan = update.plan_folder(core, "shifty", full_app(tmp_path / "two", "shifty",
                                                       version="1.1.0"))
    assert plan["update"]["tier"] == ["sandboxed", "full"] and plan["needs_typed_name"]
    assert plan["warning"]
    with pytest.raises(AppError) as caught:
        update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert "type its name" in caught.value.detail
    update.confirm_update(core, plan["plan"], plan["fingerprint"], typed_name="Shifty")
    assert store.get(core, "shifty")["tier"] == "full"


@pytest.mark.parametrize("version", ["1.0.0", "0.9.0"])
def test_an_update_must_have_a_higher_version_number(core, tmp_path, water, version):
    with pytest.raises(AppError) as caught:
        update.plan_folder(core, water, newer(tmp_path, version))
    assert "not an update" in caught.value.detail and staging(core) == []


def test_another_app_is_never_taken_as_an_update(core, tmp_path, water):
    with pytest.raises(AppError) as caught:
        update.plan_folder(core, water, newer(tmp_path, "1.1.0", id="tea"))
    assert "not a version of Water" in caught.value.detail and staging(core) == []


def test_a_path_in_an_update_that_leaves_the_apps_folder_is_refused(core, tmp_path,
                                                                    monkeypatch, water):
    outside = tmp_path / "escaped.txt"
    listing = (b"100644 blob " + b"a" * 40 + b"      5\tjimmyhq.app.json\0"
               + b"100644 blob " + b"b" * 40 + b"      5\t../../../escaped.txt\0")
    monkeypatch.setattr(install, "fetch_commit", lambda *a, **k: None)
    monkeypatch.setattr(install, "_git", lambda core, args, **k: listing)
    with pytest.raises(AppError) as caught:
        update.plan_git(core, water, URL, commit="c" * 40)
    assert "nothing was installed" in caught.value.detail
    assert not outside.exists() and staging(core) == []
    assert store.get(core, water)["version"] == "1.0.0"


def test_a_link_in_an_update_is_refused(core, tmp_path, water):
    folder = newer(tmp_path, "1.1.0")
    try:
        os.symlink(tmp_path, folder / "out")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make links")
    with pytest.raises(AppError) as caught:
        update.plan_folder(core, water, folder)
    assert "is a link" in caught.value.detail and staging(core) == []


# --- versions kept and rolling back -------------------------------------------------------------


def test_only_the_last_versions_are_kept_with_their_backups(core, tmp_path, water):
    put_data(core, water, "v1")
    first = update.current_name(store.get(core, water))
    updated(core, tmp_path, "1.1.0")
    second = update.current_name(store.get(core, water))
    put_data(core, water, "v2")
    updated(core, tmp_path, "1.2.0")
    home = store.app_dir(core, water)
    assert not (home / "versions" / first).exists()
    assert not (home / "backups" / first).exists()
    assert (home / "versions" / second).is_dir()
    assert (home / "backups" / second / "app.db").read_text(encoding="utf-8") == "v2"
    assert [v["version"] for v in update.kept(core, water)] == ["1.2.0", "1.1.0"]


def test_more_versions_are_kept_when_the_owner_asks(core, tmp_path, water):
    core.settings.set("apps.keep_versions", 3, actor="owner")
    updated(core, tmp_path, "1.1.0")
    updated(core, tmp_path, "1.2.0")
    assert [v["version"] for v in update.kept(core, water)] == ["1.2.0", "1.1.0", "1.0.0"]


def test_rolling_back_switches_back_and_can_put_that_versions_data_back(core, tmp_path, water):
    put_data(core, water, "before")
    old = update.current_name(store.get(core, water))
    updated(core, tmp_path)
    put_data(core, water, "after")
    new = update.current_name(store.get(core, water))
    kept = {v["name"]: v for v in update.kept(core, water)}
    assert kept[old]["has_backup"] and not kept[new]["has_backup"]
    done = update.rollback(core, water, old, restore=True)
    assert done["version"] == "1.0.0"
    assert (store.data_dir(core, water) / "app.db").read_text(encoding="utf-8") == "before"
    assert (update.backup_dir(core, water, new) / "app.db").read_text(encoding="utf-8") == "after"
    event = events(core, "apps.rolled_back")[0]
    assert event.payload["data_restored"] is True and event.payload["reason"] == "owner"


def test_rolling_back_without_the_data_leaves_the_data_as_it_is(core, tmp_path, water):
    put_data(core, water, "before")
    old = update.current_name(store.get(core, water))
    updated(core, tmp_path)
    put_data(core, water, "after")
    update.rollback(core, water, old)
    assert (store.data_dir(core, water) / "app.db").read_text(encoding="utf-8") == "after"


def test_rolling_back_returns_the_grant_to_what_was_agreed_for_that_version(
        core, tmp_path, water):
    old = update.current_name(store.get(core, water))
    plan = update.plan_folder(core, water, newer(tmp_path, "1.1.0",
                                                 permissions=["tasks.write", NET]))
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert NET in store.granted(core, water)
    update.rollback(core, water, old)
    assert store.granted(core, water) == ("tasks.write",)


def test_a_rollback_to_a_version_that_was_never_installed_is_refused(core, tmp_path, water):
    planted = store.app_dir(core, water) / "versions" / "0.0.1-deadbeef"
    planted.mkdir(parents=True)
    (planted / "jimmyhq.app.json").write_text("{}", encoding="utf-8")
    for name in ("0.0.1-deadbeef", "9.9.9-0a1b2c3d", "../../outside", "1.0.0"):
        with pytest.raises(AppError) as caught:
            update.rollback(core, water, name)
        assert "never ran it here" in caught.value.detail
    assert store.get(core, water)["version"] == "1.0.0"
    assert "apps.rolled_back" not in kinds_of(core)


def test_a_rollback_to_the_version_in_use_or_without_a_backup_is_refused(core, tmp_path, water):
    old = update.current_name(store.get(core, water))
    updated(core, tmp_path)  # the app had no data, so nothing was backed up
    current = update.current_name(store.get(core, water))
    with pytest.raises(AppError) as caught:
        update.rollback(core, water, current)
    assert "already on version" in caught.value.detail
    with pytest.raises(AppError) as caught:
        update.rollback(core, water, old, restore=True)
    assert "no copy" in caught.value.detail and store.get(core, water)["version"] == "1.1.0"


def test_a_full_app_that_does_not_stop_is_not_switched(core, tmp_path, monkeypatch):
    install_full(core, tmp_path / "a", "steady")
    monkeypatch.setattr(runtime, "wait_stopped", lambda *a, **k: False)
    plan = update.plan_folder(core, "steady", full_app(tmp_path / "b", "steady",
                                                       version="1.1.0"))
    with pytest.raises(AppError) as caught:
        update.confirm_update(core, plan["plan"], plan["fingerprint"], typed_name="Steady")
    assert "did not stop in time" in caught.value.detail
    assert store.get(core, "steady")["version"] == "1.0.0"
    run = json.loads(runtime.run_file(core.config.data).read_text(encoding="utf-8"))
    assert [a["id"] for a in run["apps"]] == ["steady"]  # listed again, as it was


def test_a_full_app_is_left_out_of_run_json_while_its_version_changes(core, tmp_path,
                                                                    monkeypatch):
    install_full(core, tmp_path / "a", "steady")
    seen = []

    def watch(core_, app_id, asked, **k):
        run = json.loads(runtime.run_file(core.config.data).read_text(encoding="utf-8"))
        seen.append([a["id"] for a in run["apps"]])
        return True
    monkeypatch.setattr(runtime, "wait_stopped", watch)
    plan = update.plan_folder(core, "steady", full_app(tmp_path / "b", "steady",
                                                       version="1.1.0"))
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert seen == [[]]
    run = json.loads(runtime.run_file(core.config.data).read_text(encoding="utf-8"))
    assert run["apps"][0]["version"].startswith("1.1.0-")


# --- a crash loop -----------------------------------------------------------------------------


def crashing(core, app_id: str, *, starts: int, failures: int) -> None:
    at = now_ms()
    runtime.write_json(runtime.status_file(core.config.data), {"written": at, "apps": {
        app_id: {"state": "crashing", "version": "x", "generation": 0, "pid": None,
                 "started_at": at, "failures": failures,
                 "restarts": [at + n for n in range(starts)],
                 "last_exit": {"code": 1, "at": at + starts, "ran_s": 0.2}}}})


@pytest.fixture
def shaky(core, tmp_path):
    install_full(core, tmp_path / "a", "shaky")
    put_data(core, "shaky", "before")  # so the update takes a copy of it
    plan = update.plan_folder(core, "shaky", full_app(tmp_path / "b", "shaky",
                                                      version="1.1.0"))
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    return "shaky"


def told(core) -> list[dict]:
    lanes = bench.board(core)["lanes"]
    return [c for cards in lanes.values() for c in cards if c["kind"] == crashes.CARD_KIND]


def test_a_crashing_update_is_only_held_back_by_default(core, shaky):
    crashing(core, shaky, starts=5, failures=5)
    assert core.settings.get("apps.crash_loop") == "hold_and_tell"
    crashes.notice(core)
    row = store.get(core, shaky)
    assert row["version"] == "1.1.0" and row["enabled"]
    assert events(core, "apps.held_back")[0].payload["then"] == "held"


def test_a_crashing_app_is_switched_off_when_the_owner_chose_that(core, shaky):
    core.settings.set("apps.crash_loop", "switch_off", actor="owner")
    crashing(core, shaky, starts=5, failures=5)
    crashes.notice(core)
    assert not store.get(core, shaky)["enabled"]
    assert events(core, "apps.disabled")[0].payload["reason"] == "crashing"
    assert events(core, "apps.held_back")[0].payload["then"] == "switched_off"
    assert "switched off" in told(core)[0]["title"]


def test_an_update_that_crashes_from_the_start_is_rolled_back_when_the_owner_chose_that(
        core, shaky):
    core.settings.set("apps.crash_loop", "roll_back", actor="owner")
    put_data(core, shaky, "after")
    crashing(core, shaky, starts=5, failures=5)
    crashes.notice(core)
    assert store.get(core, shaky)["version"] == "1.0.0"
    event = events(core, "apps.rolled_back")[0]
    assert event.actor == "system" and event.payload["reason"] == "crashing"
    assert event.payload["data_restored"] is True
    assert (store.data_dir(core, shaky) / "app.db").read_text(encoding="utf-8") == "before"
    assert events(core, "apps.held_back")[0].payload["then"] == "rolled_back"
    card = told(core)[0]
    assert "went back to 1.0.0" in card["title"]
    card = bench.get_card(core, card["id"])
    assert "Its data went back to the copy taken before the update" in card["body"]
    assert "left as it is" not in card["body"]
    assert "kept as a backup" in card["body"] and "until" in card["body"]


def test_going_back_after_crashing_leaves_the_data_alone_when_the_owner_switched_that_off(
        core, shaky):
    core.settings.set("apps.crash_loop", "roll_back", actor="owner")
    core.settings.set("apps.crash_rollback_restores_data", False, actor="owner")
    crashing(core, shaky, starts=5, failures=5)
    crashes.notice(core)
    assert store.get(core, shaky)["version"] == "1.0.0"
    assert events(core, "apps.rolled_back")[0].payload["data_restored"] is False
    body = bench.get_card(core, told(core)[0]["id"])["body"]
    assert "Its data was left as it is" in body and "went back to the copy" not in body


def test_an_update_that_ran_steadily_before_crashing_is_held_back_not_rolled_back(core, shaky):
    core.settings.set("apps.crash_loop", "roll_back", actor="owner")
    crashing(core, shaky, starts=8, failures=5)
    crashes.notice(core)
    assert store.get(core, shaky)["version"] == "1.1.0"
    assert events(core, "apps.held_back")[0].payload["then"] == "held"
    assert "stopped" in told(core)[0]["title"]


# --- looking for updates ----------------------------------------------------------------------


@pytest.fixture
def from_git(core, tmp_path, monkeypatch):
    repo = example(tmp_path, "water")
    make_repo(repo)
    use_local(monkeypatch, repo)
    plan = install.plan_git(core, URL, ref="main")
    install.confirm(core, plan["plan"], plan["fingerprint"])
    return repo


def bump(repo: Path, version: str, **changes) -> str:
    write_manifest(repo, version=version, **changes)
    return commit_all(repo, version)


def test_looking_finds_a_new_commit_on_the_branch_it_was_installed_from(core, from_git):
    assert store.get(core, "water")["ref"] == "main"
    assert update.look(core, "water") is None
    commit = bump(from_git, "1.1.0")
    found = update.look(core, "water")
    assert found["commit"] == commit and found["source"] == "git"
    assert install.detail(core, "water")["update_available"] == f"commit {commit[:8]}"
    assert store.get(core, "water")["version"] == "1.0.0"  # nothing installs itself


def test_a_found_update_waits_for_the_click_by_default_and_then_goes_in(core, from_git):
    bump(from_git, "1.1.0")
    assert update.check_for_updates(core) == []
    plan = update.plan_found(core, "water")
    assert install.detail(core, "water")["update_available"] == "1.1.0"
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert store.get(core, "water")["version"] == "1.1.0"
    assert update.waiting(core, "water") is None


def test_an_update_goes_in_by_itself_when_the_owner_allowed_it_and_tells_them(core, from_git):
    core.settings.set("apps.auto_update", "auto", actor="owner")
    bump(from_git, "1.1.0")
    assert update.check_for_updates(core) == ["water"]
    assert store.get(core, "water")["version"] == "1.1.0"
    assert events(core, "apps.updated")[0].actor == "system"
    lanes = bench.board(core)["lanes"]
    assert any(c["title"] == "Water was updated to 1.1.0" for c in lanes["done"])


def test_an_update_asking_for_more_waits_even_when_updates_go_in_by_themselves(core, from_git):
    core.settings.set("apps.auto_update", "auto", actor="owner")
    bump(from_git, "1.1.0", permissions=["tasks.write", NET])
    assert update.check_for_updates(core) == []
    assert store.get(core, "water")["version"] == "1.0.0"
    assert update.waiting(core, "water")["needs_consent"] is True
    assert staging(core) == []


def test_one_apps_own_setting_wins_over_the_one_for_every_app(core, from_git):
    core.settings.set("apps.auto_update", "auto", actor="owner")
    core.settings.set("app.water.auto_update", "wait", actor="owner")
    assert not update.auto_allowed(core, "water")
    core.settings.set("apps.auto_update", "wait", actor="owner")
    core.settings.set("app.water.auto_update", "auto", actor="owner")
    assert update.auto_allowed(core, "water")
    bump(from_git, "1.1.0")
    assert update.check_for_updates(core) == ["water"]


def test_looking_waits_for_the_hours_set_and_never_looks_at_zero(core, from_git):
    now = now_ms()
    update.check_for_updates(core, now=now)
    bump(from_git, "1.1.0")
    update.check_for_updates(core, now=now + 3600 * 1000)
    assert update.waiting(core, "water") is None  # not due yet
    core.settings.set("apps.update_check_hours", 0, actor="owner")
    update.check_for_updates(core, now=now + 48 * 3600 * 1000)
    assert update.waiting(core, "water") is None
    core.settings.set("apps.update_check_hours", 24, actor="owner")
    update.check_for_updates(core, now=now + 48 * 3600 * 1000)
    assert update.waiting(core, "water") is not None


def test_the_per_app_update_setting_is_on_every_apps_page_and_never_sent_to_the_app(core, water):
    from jimmyhq.apps.plugs import settings as app_settings
    from jimmyhq.tape.registry import Event

    install._publish(core)  # noqa: SLF001
    assert core.settings.get("app.water.auto_update") == "default"
    event = Event(seq=1, id="e", ts=1, kind="setting.updated", v=1, actor="owner",
                  payload={"key": "app.water.auto_update", "value": "auto"})
    assert app_settings.settings_delivery("water", event) is None


# --- an app's own environment -------------------------------------------------------------------


class FakeUv:
    """Stands in for uv: records what it was asked and makes an interpreter."""

    def __init__(self, code: int = 0):
        self.code, self.calls = code, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.code == 0:
            python = runtime.venv_python(Path(kw["env"]["UV_PROJECT_ENVIRONMENT"]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
        return subprocess.CompletedProcess(argv, self.code, b"", b"no wheel for this system\n")


def locked(folder: Path) -> Path:
    (folder / "pyproject.toml").write_text("[project]\nname = \"x\"\nversion = \"1\"\n",
                                           encoding="utf-8")
    (folder / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return folder


def test_a_full_app_with_a_lock_file_gets_its_own_environment_from_uv_sync_frozen(
        core, tmp_path, monkeypatch):
    fake = FakeUv()
    monkeypatch.setattr(environment, "runner", fake)
    monkeypatch.setattr(environment, "uv_program", lambda core: "uv-here")
    install_folder(core, locked(full_app(tmp_path, "locky")))
    [(argv, kw)] = fake.calls
    code = store.code_dir(core, "locky")
    assert argv == ["uv-here", "sync", "--frozen", "--no-dev", "--no-build",
                    "--no-install-project", "--project", str(code)]
    venv = Path(kw["env"]["UV_PROJECT_ENVIRONMENT"])
    assert venv == environment.venv_dir(core, "locky", code.name)
    assert "JIMMYHQ_DATA" not in kw["env"] and kw.get("shell") is not True
    assert runtime.python_for(core, "locky", code.name) == str(runtime.venv_python(venv))
    assert install.detail(core, "locky")["environment_note"] is None


def test_without_uv_a_full_app_runs_with_jimmyhqs_python_and_the_screen_says_so(
        core, tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "uv_program", lambda core: None)
    install_folder(core, locked(full_app(tmp_path, "locky")))
    code = store.code_dir(core, "locky")
    import sys
    assert runtime.python_for(core, "locky", code.name) == sys.executable
    assert "uv was not found" in install.detail(core, "locky")["environment_note"]


def test_a_failed_build_installs_nothing_and_leaves_no_environment(core, tmp_path, monkeypatch):
    monkeypatch.setattr(environment, "runner", FakeUv(code=2))
    monkeypatch.setattr(environment, "uv_program", lambda core: "uv-here")
    with pytest.raises(AppError) as caught:
        install_folder(core, locked(full_app(tmp_path, "locky")))
    assert "no wheel for this system" in caught.value.detail
    assert store.get(core, "locky") is None
    assert not (store.app_dir(core, "locky") / "venvs").exists() or not any(
        (store.app_dir(core, "locky") / "venvs").iterdir())


def test_an_app_without_a_lock_file_has_no_environment_built(core, tmp_path, monkeypatch):
    fake = FakeUv()
    monkeypatch.setattr(environment, "runner", fake)
    install_full(core, tmp_path, "plain")
    assert fake.calls == []


# --- removing ---------------------------------------------------------------------------------


def test_removing_keeps_the_data_its_backups_and_its_record_by_default(core, tmp_path, water):
    put_data(core, water, "kept")
    updated(core, tmp_path)
    call(core, water, "tasks.create", {"title": "Drink water"})
    done = install.remove(core, water)
    assert done["data"] == "kept" and done["erased_events"] == 0
    assert (store.data_dir(core, water) / "app.db").exists()
    assert (store.app_dir(core, water) / "backups").is_dir()
    assert all(not e.erased for e in events(core))
    assert events(core, "apps.removed")[0].payload["tape_erased"] is False


def test_removing_with_its_data_deleted_leaves_no_data_backup_log_or_words_behind(
        core, tmp_path, water):
    secret = marker()
    put_data(core, water, f"note: {secret}")
    updated(core, tmp_path)
    put_data(core, water, f"note again: {secret}")
    call(core, water, "tasks.create", {"title": f"Task {secret}"})
    log = runtime.log_file(core.config.log_dir, water)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"printed {secret}\n", encoding="utf-8")
    log.with_name(log.name + ".1").write_text(f"older {secret}\n", encoding="utf-8")

    done = install.remove(core, water, keep_data=False)
    assert done["data"] == "deleted" and done["erased_events"] >= 1
    assert not store.app_dir(core, water).exists()
    assert not log.exists() and not log.with_name(log.name + ".1").exists()
    for path in Path(core.config.data).rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes(), path
    removed = events(core, "apps.removed")[0]
    assert removed.payload == {"app_id": "water", "version": "1.1.0", "data": "deleted",
                               "tape_erased": True}
    assert events(core, "apps.installed")[0].payload["app_id"] == "water"  # the record stays
    assert [e.payload["reason"] for e in events(core, "tape.erased")] == ["apps.removed"]


def test_the_owner_may_delete_the_data_and_still_keep_what_it_wrote_on_the_record(
        core, water):
    call(core, water, "tasks.create", {"title": "Drink water"})
    install.remove(core, water, keep_data=False, erase_record=False)
    assert all(not e.erased for e in events(core))


def test_erasing_takes_only_what_the_app_wrote(core, tmp_path, water):
    install_folder(core, example(tmp_path, "books_read"))
    call(core, water, "tasks.create", {"title": "Drink water"})
    from jimmyhq import tasks
    tasks.service(core).create("The owner's own", actor="owner")
    assert update.erase_record(core, water) == 1
    erased = [e for e in events(core) if e.erased]
    assert [e.actor for e in erased] == ["app:water"]


# --- over HTTP --------------------------------------------------------------------------------


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


def test_the_owner_updates_rolls_back_and_removes_over_http(core, tmp_path, owner, water):
    old = update.current_name(store.get(core, water))
    plan = owner.post(f"/api/apps/{water}/update/plan",
                      json={"folder": str(newer(tmp_path, "1.1.0"))}).json()
    assert plan["update"]["from_version"] == "1.0.0"
    done = owner.post(f"/api/apps/{water}/update/confirm",
                      json={"plan": plan["plan"], "fingerprint": plan["fingerprint"]})
    assert done.status_code == 200 and done.json()["version"] == "1.1.0"
    detail = owner.get(f"/api/apps/{water}").json()
    assert [v["name"] for v in detail["versions"]][1] == old
    back = owner.post(f"/api/apps/{water}/rollback", json={"version": old})
    assert back.status_code == 200 and back.json()["version"] == "1.0.0"
    gone = owner.post(f"/api/apps/{water}/remove", json={"keep_data": False,
                                                       "erase_record": True})
    assert gone.status_code == 200 and gone.json()["data"] == "deleted"


def test_an_update_plan_cannot_be_confirmed_for_another_app(core, tmp_path, owner, water):
    install_folder(core, example(tmp_path, "books_read"))
    plan = owner.post(f"/api/apps/{water}/update/plan",
                      json={"folder": str(newer(tmp_path, "1.1.0"))}).json()
    answer = owner.post("/api/apps/books_read/update/confirm",
                        json={"plan": plan["plan"], "fingerprint": plan["fingerprint"]})
    assert answer.status_code == 409 and store.get(core, water)["version"] == "1.0.0"


@pytest.mark.parametrize("path, body", [
    ("/api/apps/water/update/plan", {}), ("/api/apps/water/update/check", {}),
    ("/api/apps/water/update/confirm", {"plan": "x", "fingerprint": "y"}),
    ("/api/apps/water/rollback", {"version": "1.0.0-00000000"}),
])
def test_an_apps_own_token_cannot_update_or_roll_back_anything(core, tmp_path, path, body):
    install_full(core, tmp_path, "drafty")
    install_folder(core, example(tmp_path, "water"))
    token = core.auth.mint_app_token("drafty")
    with TestClient(create_app(core), base_url="http://testserver") as client:
        answer = client.post(path, json=body, headers=bearer(token))
    assert answer.status_code == 401
    assert kinds_of(core).count("apps.updated") == 0
