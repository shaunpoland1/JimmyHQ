"""Backups with a running JimmyHQ: the settings, the two folders, the schedule, the record,
the card on the Bench and who may press the buttons."""

from __future__ import annotations

import io
import shutil
import sys

import pytest
from fastapi.testclient import TestClient

from jimmyhq import bench, config, modules
from jimmyhq.backup import cli, service, snapshot
from jimmyhq.core import open_core
from jimmyhq.settings import SettingError
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
HOUR, DAY = service.HOUR_MS, service.DAY_MS


@pytest.fixture
def core(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    cfg = config.Config(data=data, host="127.0.0.1", port=0, allowed_hosts=("testserver",),
                        remote_setup=True)
    core = open_core(cfg)
    (data / "memory").mkdir(exist_ok=True)
    (data / "memory" / "note.md").write_text("A note.\n", encoding="utf-8")
    yield core
    core.close()


@pytest.fixture
def first(tmp_path, core):
    folder = tmp_path / "first"
    folder.mkdir()
    core.settings.set("backup.destination", str(folder), actor="owner")
    return folder


@pytest.fixture
def second(tmp_path, core):
    folder = tmp_path / "second"
    folder.mkdir()
    core.settings.set("backup.second_destination", str(folder), actor="owner")
    return folder


def kinds_printed(core, prefix="backup."):
    return [e.kind for e in core.tape.after(0, limit=1000) if e.kind.startswith(prefix)]


def needs_cards(core):
    return [c for lane in bench.board(core)["lanes"].values() for c in lane
            if c["kind"] == service.CARD_KIND]


# --- settings ------------------------------------------------------------------------


def test_backups_are_a_module_and_off_until_a_folder_is_chosen(core):
    assert "backup" in modules.MODULES
    assert core.settings.get("backup.destination") == ""
    assert service.is_on(core) is False
    assert service.due(core) == ([], [])


def test_a_backup_folder_must_be_a_full_path_and_quotes_are_taken_off(core, tmp_path):
    with pytest.raises(SettingError, match="full path"):
        core.settings.set("backup.destination", "backups/here", actor="owner")
    with pytest.raises(SettingError, match="one line"):
        core.settings.set("backup.destination", f"{tmp_path}\nother", actor="owner")
    assert core.settings.set("backup.destination", f'"{tmp_path}"', actor="owner") == \
        str(tmp_path)
    assert core.settings.set("backup.destination", "  ", actor="owner") == ""
    with pytest.raises(SettingError):
        core.settings.set("backup.keep", 0, actor="owner")


# --- making ----------------------------------------------------------------------------


def test_back_up_now_makes_a_backup_in_each_folder_and_records_it_without_a_path(
        core, first, second, tmp_path):
    outcomes = service.run_now(core, actor="owner")
    assert [o.ok for o in outcomes] == [True, True]
    assert len(snapshot.snapshots(first)) == len(snapshot.snapshots(second)) == 1
    made = [e for e in core.tape.after(0, limit=1000) if e.kind == "backup.made"]
    assert [e.payload["destination"] for e in made] == ["first", "second"]
    assert all(str(tmp_path) not in str(e.payload) for e in made)
    assert made[0].actor == "owner"


def test_the_second_folder_gets_its_copy_when_the_first_fails_and_the_other_way_round(
        core, first, second):
    first.rmdir()
    outcomes = service.run_now(core, actor="owner")
    assert [(o.which, o.ok) for o in outcomes] == [("first", False), ("second", True)]
    first.mkdir()
    shutil.rmtree(second)
    outcomes = service.run_now(core, actor="owner")
    assert [(o.which, o.ok) for o in outcomes] == [("first", True), ("second", False)]
    failed = [e for e in core.tape.after(0, limit=1000) if e.kind == "backup.failed"]
    assert [e.payload["destination"] for e in failed] == ["first", "second"]
    assert "is not there" in failed[0].payload["reason"]


def test_a_folder_inside_the_data_folder_is_refused_and_recorded_as_a_failure(core):
    inside = core.config.data / "memory"
    core.settings.set("backup.destination", str(inside), actor="owner")
    [outcome] = service.run_now(core, actor="owner")
    assert not outcome.ok and "inside JimmyHQ's data folder" in outcome.sentence
    assert kinds_printed(core) == ["backup.failed"]


def test_the_second_folder_cannot_be_the_first_again(core, first):
    core.settings.set("backup.second_destination", str(first), actor="owner")
    outcomes = service.run_now(core, actor="owner")
    assert [(o.which, o.ok) for o in outcomes] == [("first", True), ("second", False)]
    assert "same as the first" in outcomes[1].sentence


def test_old_backups_are_pruned_to_the_number_kept_only_after_a_good_one(core, first):
    core.settings.set("backup.keep", 2, actor="owner")
    for n in range(3):
        service.back_up_one(core, "first", first, actor="owner", now=1_000_000 + n)
    assert len(snapshot.snapshots(first)) == 2
    made = [e.payload["pruned"] for e in core.tape.after(0, limit=1000)
            if e.kind == "backup.made"]
    assert made == [0, 0, 1]


def test_a_failed_backup_prunes_nothing(core, first, monkeypatch):
    core.settings.set("backup.keep", 1, actor="owner")
    service.back_up_one(core, "first", first, actor="owner", now=1_000_000)
    monkeypatch.setattr(snapshot, "verify", lambda *_a, **_k: snapshot.Check(
        passed=False, full=False, problems=[("tape.db", "It is damaged.")]))
    outcome = service.back_up_one(core, "first", first, actor="owner", now=2_000_000)
    assert not outcome.ok
    assert [s.made_ms for s in snapshot.snapshots(first)] == [1_000_000]


def test_only_one_backup_runs_at_a_time_within_this_process(core, first):
    held = service.try_lock(core.config.data)
    try:
        with pytest.raises(service.Busy):
            service.run_now(core, actor="owner")
        assert service.busy(core.config.data) is True
    finally:
        held.release()
    assert service.busy(core.config.data) is False


def test_two_installations_with_the_same_data_path_never_prune_or_test_each_others_backups(
        tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    data = tmp_path / "data"

    def installation():
        data.mkdir()
        cfg = config.Config(data=data, host="127.0.0.1", port=0)
        core = open_core(cfg)
        core.settings.set("backup.destination", str(shared), actor="owner")
        core.settings.set("backup.keep", 1, actor="owner")
        return core

    first = installation()
    try:
        for n in range(2):
            service.back_up_one(first, "first", shared, actor="owner", now=1_000_000 + n)
        theirs = {s.name for s in snapshot.snapshots(shared)}
    finally:
        first.close()
    data.rename(tmp_path / "the-first-computer")  # the second computer: the same path
    second = installation()
    try:
        assert service.source(second) != snapshot.place_of(data)
        service.back_up_one(second, "first", shared, actor="owner", now=2_000_000)
        service.back_up_one(second, "first", shared, actor="owner", now=2_000_001)
        names = {s.name for s in snapshot.snapshots(shared)}
        assert theirs <= names and len(names) == len(theirs) + 1
        [tested] = [service.test_one(second, "first", shared, actor="owner")]
        assert snapshot.snapshot_name(2_000_001) in tested.sentence
    finally:
        second.close()


def test_the_installation_id_is_made_once_owner_only_and_goes_into_each_backup(core, first):
    made = service.source(core)
    assert made == service.source(core) == snapshot.installation_id(core.config.data)
    path = core.config.data / snapshot.INSTALLATION_FILE
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600
    service.run_now(core, actor="owner")
    newest = snapshot.snapshots(first)[0]
    assert (newest.path / "data" / snapshot.INSTALLATION_FILE).read_text("ascii").strip() == made
    assert newest.source == made


def test_backups_made_before_installation_ids_are_still_this_installations_own(core, first):
    older = [snapshot.make(core.config.data, first, now_ms=1_000_000 + n, version="0.1.0",
                           source=snapshot.place_of(core.config.data)).name for n in range(2)]
    assert [s.name for s in service.mine(core, first)] == older[::-1]
    [tested] = service.run_now(core, actor="owner", test=True)
    assert tested.ok and older[1] in tested.sentence
    core.settings.set("backup.keep", 2, actor="owner")
    service.run_now(core, actor="owner")
    assert older[0] not in {s.name for s in snapshot.snapshots(first)}  # pruned as its own
    out = io.StringIO()
    cli.list_main([first], {service.source(core), service.older_source(core)}, out)
    assert "another installation" not in out.getvalue()


def test_a_run_that_never_finishes_is_reported_on_the_bench_after_the_hours_set(
        core, first, monkeypatch):
    core.settings.set("backup.stuck_after_hours", 12, actor="owner")
    real = service.now_ms
    monkeypatch.setattr(service, "now_ms", lambda: real() - 13 * HOUR)
    held = service.try_lock(core.config.data)  # a run that began 13 hours ago, still going
    monkeypatch.setattr(service, "now_ms", real)
    try:
        assert service.due(core)[0] == ["first"]
        assert service.job(core) is None  # it cannot start while that one holds the lock
        [card] = needs_cards(core)
        assert "running for 13 hours" in card["summary"]
        assert service.status(core)["running_since"] is not None
    finally:
        held.release()
    assert service.running_since(core.config.data) is None
    thread = service.job(core)  # the lock is free again: the backup runs
    thread.join(30)
    assert needs_cards(core) == []


def test_a_run_shorter_than_the_hours_set_is_no_problem(core, first):
    held = service.try_lock(core.config.data)
    try:
        assert service.job(core) is None
        assert service.problems_now(core) == [] and needs_cards(core) == []
    finally:
        held.release()


_HOLD = """
import sys
from pathlib import Path
from jimmyhq.backup import service
held = service.try_lock(Path(sys.argv[1]))
print("held" if held else "busy", flush=True)
sys.stdin.read()
"""


def test_a_backup_lock_held_by_another_process_stops_a_backup_here(core, first):
    import subprocess

    child = subprocess.Popen([sys.executable, "-c", _HOLD, str(core.config.data)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        assert service.busy(core.config.data) is True
        with pytest.raises(service.Busy):
            service.run_now(core, actor="owner")
        assert service.job(core) is None
        assert service.running_since(core.config.data) is not None  # read across processes
        assert snapshot.snapshots(first) == []
    finally:
        child.stdin.close()
        child.wait(30)
        child.stdout.close()
    assert service.busy(core.config.data) is False


# --- testing -----------------------------------------------------------------------------


def test_the_restore_test_passes_on_a_good_backup_and_fails_on_a_changed_one(core, first):
    service.run_now(core, actor="owner")
    [passed] = service.run_now(core, actor="owner", test=True)
    assert passed.ok
    newest = snapshot.snapshots(first)[0]
    (newest.path / "data" / "memory" / "note.md").write_text("A nate.\n", encoding="utf-8")
    [failed] = service.run_now(core, actor="owner", test=True)
    assert not failed.ok
    event = [e for e in core.tape.after(0, limit=1000) if e.kind == "backup.test_failed"][-1]
    assert event.payload["problems"] == [
        "A file in memory/: Its contents have changed since the backup was made."]


def test_asking_for_a_test_with_no_backup_says_so(core, first):
    [outcome] = service.run_now(core, actor="owner", test=True)
    assert not outcome.ok and "no backup" in outcome.sentence


# --- the schedule --------------------------------------------------------------------------


def test_a_backup_is_due_at_once_then_after_the_interval_and_a_failure_is_retried_sooner(
        core, first):
    core.settings.set("backup.every_hours", 24, actor="owner")
    core.settings.set("backup.retry_hours", 2, actor="owner")
    assert service.due(core, now=0) == (["first"], [])
    service.back_up_one(core, "first", first, actor="system", now=10 * HOUR)
    made_at = [e.ts for e in core.tape.after(0, limit=1000) if e.kind == "backup.made"][0]
    assert service.due(core, now=made_at + 23 * HOUR)[0] == []
    assert service.due(core, now=made_at + 24 * HOUR)[0] == ["first"]
    shutil.rmtree(first)
    service.back_up_one(core, "first", first, actor="system")
    failed_at = [e.ts for e in core.tape.after(0, limit=1000) if e.kind == "backup.failed"][0]
    assert service.due(core, now=failed_at + HOUR)[0] == []
    assert service.due(core, now=failed_at + 2 * HOUR)[0] == ["first"]


def test_the_restore_test_is_due_on_its_own_schedule_after_a_backup(core, first):
    core.settings.set("backup.test_every_days", 7, actor="owner")
    service.back_up_one(core, "first", first, actor="system")
    made_at = [e.ts for e in core.tape.after(0, limit=1000) if e.kind == "backup.made"][0]
    assert service.due(core, now=made_at + 6 * DAY)[1] == []
    assert service.due(core, now=made_at + 7 * DAY)[1] == ["first"]


def test_the_worker_job_runs_what_is_due_in_its_own_thread(core, first):
    thread = service.job(core)
    assert thread is not None
    thread.join(30)
    assert kinds_printed(core) == ["backup.made"]
    assert service.job(core) is None  # nothing is due now


def test_a_backup_and_test_dated_ahead_of_the_clock_are_due_at_once_and_said_until_redone(
        core, first, monkeypatch):
    from jimmyhq.tape import registry

    real = registry.now_ms
    monkeypatch.setattr(registry, "now_ms", lambda: real() + 30 * DAY)  # the clock ran ahead
    monkeypatch.setattr(service, "now_ms", lambda: real() + 30 * DAY)
    service.run_now(core, actor="owner")
    service.run_now(core, actor="owner", test=True)
    ahead = snapshot.snapshots(first)[0].name
    monkeypatch.setattr(registry, "now_ms", real)  # and was put right
    monkeypatch.setattr(service, "now_ms", real)
    assert service.due(core) == (["first"], ["first"])
    [problem] = service.problems_now(core)
    assert "dated later than this computer's clock" in problem
    service.keep_card(core)
    assert len(needs_cards(core)) == 1
    service.run_now(core, actor="owner")
    [tested] = service.run_now(core, actor="owner", test=True)
    assert tested.ok and ahead not in tested.sentence  # the new one is the newest now
    assert snapshot.snapshots(first)[-1].name == ahead and snapshot.snapshots(first)[-1].ahead
    assert service.due(core) == ([], [])
    assert service.problems_now(core) == []
    service.keep_card(core)
    assert needs_cards(core) == []


def test_a_failure_recorded_after_a_backup_counts_as_the_latest_even_in_the_same_millisecond(
        core, first, monkeypatch):
    from jimmyhq.tape import registry

    monkeypatch.setattr(registry, "now_ms", lambda: 5 * DAY)
    service.back_up_one(core, "first", first, actor="system")
    shutil.rmtree(first)
    service.back_up_one(core, "first", first, actor="system")
    assert service.due(core, now=5 * DAY + 2 * HOUR)[0] == ["first"]  # retried after 2 hours


# --- what the owner sees ---------------------------------------------------------------------


def test_the_status_says_each_folders_last_backup_and_test(core, first):
    service.run_now(core, actor="owner")
    service.run_now(core, actor="owner", test=True)
    found = service.status(core)
    assert found["on"] is True and found["running"] is False and found["problem"] is None
    one, two = found["destinations"]
    assert one["which"] == "first" and one["set"] is True
    assert one["last_backup"]["files"] > 0 and one["last_test"]["passed"] is True
    assert two["set"] is False and two["last_backup"] is None


def test_a_failed_test_files_one_card_on_the_bench_and_a_good_one_withdraws_it(core, first):
    service.run_now(core, actor="owner")
    newest = snapshot.snapshots(first)[0]
    (newest.path / "data" / "memory" / "note.md").write_text("A nate.\n", encoding="utf-8")
    service.run_now(core, actor="owner", test=True)
    [card] = needs_cards(core)
    assert card["title"] == "Backups need a look" and "restore test" in card["summary"]
    service.keep_card(core)
    assert len(needs_cards(core)) == 1  # not filed twice
    service.run_now(core, actor="owner")  # a new, good backup
    assert needs_cards(core) == []


def test_an_old_backup_is_a_standing_problem_and_a_never_working_folder_too(core, first):
    core.settings.set("backup.warn_after_days", 3, actor="owner")
    service.back_up_one(core, "first", first, actor="system")
    made_at = [e.ts for e in core.tape.after(0, limit=1000) if e.kind == "backup.made"][0]
    assert service.problems_now(core, now=made_at + 2 * DAY) == []
    [old] = service.problems_now(core, now=made_at + 4 * DAY)
    assert old == "The newest backup in the backup folder is 4 days old."
    service.keep_card(core, now=made_at + 4 * DAY)
    assert len(needs_cards(core)) == 1
    core.settings.set("backup.destination", "", actor="owner")  # backups off: nothing to say
    service.keep_card(core, now=made_at + 4 * DAY)
    assert needs_cards(core) == []
    notes = [e.payload.get("note") for e in core.tape.after(0, limit=1000)
             if e.kind == "bench.withdrawn"]
    assert notes == ["Backups are switched off, so there is nothing to look at."]


def test_a_folder_that_never_worked_is_a_problem_once_it_has_failed(core, tmp_path):
    core.settings.set("backup.destination", str(tmp_path / "missing"), actor="owner")
    assert service.problems_now(core) == []
    service.run_now(core, actor="owner")
    [problem] = service.problems_now(core)
    assert problem.startswith("No backup has been made in the backup folder yet.")


# --- who may ---------------------------------------------------------------------------------


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam",
                                                "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def token(owner, core):
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(owner.app, base_url="http://testserver",
                    headers={"authorization": f"Bearer {secret}"}) as c:
        yield c


def test_a_read_token_sees_the_status_but_cannot_press_either_button(token, first):
    assert token.get("/api/backup/status").status_code == 200
    assert token.post("/api/backup/now").status_code == 403
    assert token.post("/api/backup/test").status_code == 403


def test_nobody_signed_out_sees_anything(client):
    assert client.get("/api/backup/status").status_code == 401


def test_the_owner_starts_a_backup_and_a_test_and_the_status_follows(owner, core, first):
    from jimmyhq.backup.routes import runs

    answer = owner.post("/api/backup/now")
    assert answer.status_code == 202 and answer.json()["started"] is True
    runs(core).last.join(30)
    answer = owner.post("/api/backup/test")
    assert answer.status_code == 202
    runs(core).last.join(30)
    status = owner.get("/api/backup/status").json()
    assert status["destinations"][0]["last_test"]["passed"] is True
    assert kinds_printed(core) == ["backup.made", "backup.test_passed"]


def test_the_buttons_say_so_when_backups_are_off_or_one_is_running(owner, core, first):
    held = service.try_lock(core.config.data)
    try:
        answer = owner.post("/api/backup/now")
        assert answer.status_code == 409 and "already running" in answer.json()["detail"]
    finally:
        held.release()
    core.settings.set("backup.destination", "", actor="owner")
    answer = owner.post("/api/backup/now")
    assert answer.status_code == 409 and "Backups are off" in answer.json()["detail"]


def test_no_route_restores_a_backup(owner):
    paths = {getattr(r, "path", "") for r in owner.app.routes}
    assert not [p for p in paths if p.startswith("/api/backup") and "restore" in p]
