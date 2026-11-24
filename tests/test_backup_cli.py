"""``jimmyhq backup``: listing, verifying and, above all, restoring, which is the owner's act
at the terminal: it refuses while JimmyHQ runs, refuses a newer backup, asks first, and
never deletes the data that was there."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from jimmyhq import config, supervisor
from jimmyhq.backup import cli, service, snapshot
from jimmyhq.core import open_core


@pytest.fixture
def cfg(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    return config.Config(data=data, host="127.0.0.1", port=0)


@pytest.fixture
def backed_up(cfg, tmp_path):
    """A data folder with a task on the Tape and a note, and one backup of it."""
    dest = tmp_path / "backups"
    dest.mkdir()
    core = open_core(cfg)
    try:
        (cfg.data / "memory").mkdir(exist_ok=True)
        (cfg.data / "memory" / "note.md").write_text("Kept.\n", encoding="utf-8")
        core.tape.print("system.opened", {"version": "0.1.0"}, actor="system")
        core.settings.set("backup.destination", str(dest), actor="owner")
        [outcome] = service.run_now(core, actor="owner")
        assert outcome.ok
    finally:
        core.close()
    return snapshot.snapshots(dest)[0].path


def tape_rows(data):
    conn = sqlite3.connect(data / "tape.db")
    try:
        return [r[0] for r in conn.execute("SELECT kind FROM tape ORDER BY seq")]
    finally:
        conn.close()


def run_restore(cfg, path, *, yes=True, answer=None):
    out, err = io.StringIO(), io.StringIO()
    code = cli.restore_main(cfg, path, yes=yes, out=out, err=err, ask=lambda _q: answer)
    return code, out.getvalue(), err.getvalue()


def test_restore_moves_the_current_data_aside_and_the_restored_folder_opens_with_the_same_rows(
        cfg, backed_up):
    in_backup = tape_rows(backed_up / "data")
    assert "setting.updated" in in_backup
    # Things change after the backup: a new note, and the old one edited.
    (cfg.data / "memory" / "later.md").write_text("Written later.\n", encoding="utf-8")
    code, out, err = run_restore(cfg, backed_up)
    assert code == 0, err
    asides = [p for p in cfg.data.parent.iterdir() if "-before-restore-" in p.name]
    assert len(asides) == 1 and str(asides[0]) in out
    assert (asides[0] / "memory" / "later.md").exists()  # nothing that was there is deleted
    assert not (cfg.data / "memory" / "later.md").exists()
    assert (cfg.data / "memory" / "note.md").read_text(encoding="utf-8") == "Kept.\n"
    rows = tape_rows(cfg.data)
    assert rows == [*in_backup, "backup.restored"]
    core = open_core(cfg)  # it opens as a data folder again
    core.close()
    assert snapshot.verify(backed_up).passed  # the backup itself is left as it was


def test_restore_goes_ahead_when_a_file_manager_left_clutter_in_the_backup_and_skips_it(
        cfg, backed_up):
    (backed_up / "data" / ".DS_Store").write_bytes(b"x")
    (backed_up / "data" / "memory" / "Thumbs.db").write_bytes(b"x")
    code, _, err = run_restore(cfg, backed_up)
    assert code == 0, err
    assert (cfg.data / "memory" / "note.md").exists()
    assert not (cfg.data / ".DS_Store").exists()
    assert not (cfg.data / "memory" / "Thumbs.db").exists()


def test_restore_refuses_while_jimmyhq_is_running(cfg, backed_up):
    lock = supervisor.take_lock(cfg.data)
    try:
        code, _, err = run_restore(cfg, backed_up)
    finally:
        lock.close()
    assert code == 1 and "JimmyHQ is running" in err and "Nothing was changed" in err
    assert not [p for p in cfg.data.parent.iterdir() if "-before-restore-" in p.name]


def test_restore_refuses_a_backup_from_a_newer_jimmyhq_with_the_store_too_new_words(
        cfg, backed_up):
    target = backed_up / "data" / "tape.db"
    conn = sqlite3.connect(target)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    manifest_path = backed_up / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "tape.db":
            entry["size"], entry["sha256"] = snapshot.hash_file(target)
    manifest["stores"]["tape.db"]["version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, _, err = run_restore(cfg, backed_up)
    assert code == 1
    assert "Nothing was changed." in err and "written by a newer version of JimmyHQ" in err
    assert "this JimmyHQ knows up to version" in err


def test_restore_refuses_a_backup_that_fails_its_test(cfg, backed_up):
    (backed_up / "data" / "memory" / "note.md").write_text("Kxpt.\n", encoding="utf-8")
    code, _, err = run_restore(cfg, backed_up)
    assert code == 1 and "does not pass the restore test" in err


def test_restore_asks_for_the_word_and_does_nothing_without_it(cfg, backed_up):
    code, out, _ = run_restore(cfg, backed_up, yes=False, answer="yes")
    assert code == 1 and "Nothing was changed." in out
    code, _, err = run_restore(cfg, backed_up, yes=False, answer=None)
    assert code == 1 and "--yes" in err
    assert not [p for p in cfg.data.parent.iterdir() if "-before-restore-" in p.name]
    code, _, _ = run_restore(cfg, backed_up, yes=False, answer="restore")
    assert code == 0


def test_restore_refuses_a_folder_that_is_not_a_backup(cfg, tmp_path):
    code, _, err = run_restore(cfg, tmp_path)
    assert code == 1 and "not a backup made by JimmyHQ" in err


def test_list_shows_whole_backups_and_verify_runs_the_restore_test(cfg, backed_up):
    out = io.StringIO()
    assert cli.list_main([backed_up.parent], {snapshot.installation_id(cfg.data)}, out) == 0
    assert backed_up.name in out.getvalue() and "another installation" not in out.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert cli.verify_main(backed_up, out, err) == 0 and "It opens." in out.getvalue()
    (backed_up / "data" / "memory" / "note.md").unlink()
    assert cli.verify_main(backed_up, out, err) == 1
    assert "memory/note.md: It is missing" in err.getvalue()


def test_the_backup_command_is_part_of_jimmyhq(cfg, monkeypatch, capsys, backed_up):
    from jimmyhq import cli as main_cli

    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))
    monkeypatch.setenv("JIMMYHQ_PORT", "8440")
    assert main_cli.main(["backup", "verify", str(backed_up)]) == 0
    assert "It opens." in capsys.readouterr().out
    assert main_cli.main(["backup", "list"]) == 0
    assert backed_up.name in capsys.readouterr().out


def test_the_copy_of_a_backup_is_on_the_disk_before_the_data_folder_is_moved_aside(
        cfg, backed_up, monkeypatch):
    import os
    import sys

    from test_backup import _synced_before, _watch_disk

    log = _watch_disk(monkeypatch)
    code, _, err = run_restore(cfg, backed_up)
    assert code == 0, err
    aside = next(p.name for p in cfg.data.parent.iterdir() if "-before-restore-" in p.name)
    synced = _synced_before(log, aside)
    source = backed_up / "data"
    for here, _dirs, names in os.walk(source):  # each file and folder the backup holds
        there = cfg.data / os.path.relpath(here, source)
        for name in names:
            assert os.stat(there / name).st_ino in synced, name
        if sys.platform != "win32":
            assert os.stat(there).st_ino in synced, here


def test_a_restore_keeps_the_installation_id_so_its_older_backups_stay_its_own(cfg, backed_up):
    before = snapshot.installation_id(cfg.data)
    (cfg.data / snapshot.INSTALLATION_FILE).unlink()  # as on a new computer
    code, _, err = run_restore(cfg, backed_up)
    assert code == 0, err
    assert snapshot.installation_id(cfg.data) == before
    core = open_core(cfg)
    try:
        assert [s.name for s in service.mine(core, backed_up.parent)] == [backed_up.name]
    finally:
        core.close()


# --- a restore that cannot finish --------------------------------------------------------


def _renames(monkeypatch, decide, after=lambda *_: None):
    """Let ``decide(src, dst, n)`` fail or act on each rename the restore makes, and
    ``after`` act once it is made."""
    real, calls = os.rename, []

    def rename(src, dst):
        calls.append((str(src), str(dst)))
        decide(str(src), str(dst), len(calls))
        real(src, dst)
        after(str(src), str(dst), len(calls))

    monkeypatch.setattr(os, "rename", rename)
    return calls


def _asides(cfg):
    return [p for p in cfg.data.parent.iterdir() if "-before-restore-" in p.name]


def test_a_restore_that_cannot_put_the_copy_in_place_retries_putting_the_data_back(
        cfg, backed_up, monkeypatch):
    (cfg.data / "memory" / "later.md").write_text("Written later.\n", encoding="utf-8")
    failed_back = []

    def decide(src, dst, _n):
        if ".restoring-" in src:
            raise OSError(5, "Input/output error")
        if "-before-restore-" in src and not failed_back:
            failed_back.append(src)  # a virus scanner holds it for a moment
            raise PermissionError(13, "Permission denied")

    _renames(monkeypatch, decide)
    code, _, err = run_restore(cfg, backed_up)
    assert code == 1
    assert "put back as it was" in err and failed_back
    assert (cfg.data / "memory" / "later.md").exists() and not _asides(cfg)


def test_a_restore_that_cannot_put_the_data_back_names_both_folders_and_what_to_do(
        cfg, backed_up, monkeypatch):
    def decide(src, dst, _n):
        if ".restoring-" in src or "-before-restore-" in src:
            raise OSError(5, "Input/output error")

    _renames(monkeypatch, decide)
    code, _, err = run_restore(cfg, backed_up)
    assert code == 1
    assert "put back as it was" not in err and "Nothing was changed" not in err
    [aside] = _asides(cfg)
    [incoming] = [p for p in cfg.data.parent.iterdir() if ".restoring-" in p.name]
    assert not cfg.data.exists()
    assert str(aside) in err and str(incoming) in err
    assert f"rename {aside} to {cfg.data}" in err and f"rename {incoming} to {cfg.data}" in err
    assert (aside / "memory" / "note.md").exists()


def test_a_restore_refuses_to_put_the_copy_over_a_data_folder_that_appeared_meanwhile(
        cfg, backed_up, monkeypatch):
    def after(src, dst, n):
        if n == 1:  # JimmyHQ started in the moment the data folder was not there
            cfg.data.mkdir()

    _renames(monkeypatch, lambda *_: None, after)
    code, _, err = run_restore(cfg, backed_up)
    assert code == 1 and "a new folder appeared" in err
    assert list(cfg.data.iterdir()) == []  # left as it was found
    [aside] = _asides(cfg)
    assert (aside / "memory" / "note.md").exists() and str(aside) in err


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot rename a folder with a "
                    "file in it held open, so there the lock is let go before the swap")
def test_no_jimmyhq_can_start_on_either_folder_while_a_restore_swaps_them(
        cfg, backed_up, monkeypatch):
    tried = []

    def decide(src, dst, _n):
        for folder in (src, dst):
            if os.path.isdir(folder):
                try:
                    supervisor.take_lock(Path(folder)).close()
                    tried.append(("started", folder))
                except supervisor.AlreadyRunning:
                    tried.append(("refused", folder))

    _renames(monkeypatch, decide)
    code, _, err = run_restore(cfg, backed_up)
    assert code == 0, err
    assert tried and all(what == "refused" for what, _ in tried), tried


def test_restoring_through_a_linked_data_folder_restores_the_real_folder_and_keeps_the_link(
        tmp_path, backed_up):
    real = tmp_path / "data"
    link = tmp_path / "linked-data"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make links")
    (real / "memory" / "later.md").write_text("Written later.\n", encoding="utf-8")
    cfg = config.Config(data=link, host="127.0.0.1", port=0)
    code, out, err = run_restore(cfg, backed_up)
    assert code == 0, err
    assert link.is_symlink() and Path(os.path.realpath(link)) == real.resolve()
    assert (real / "memory" / "note.md").exists() and not (real / "memory" / "later.md").exists()
    [aside] = [p for p in tmp_path.iterdir() if "-before-restore-" in p.name]
    assert aside.name.startswith("data-") and (aside / "memory" / "later.md").exists()
    assert "links to" in out


def test_restoring_through_a_link_that_leads_nowhere_is_refused(tmp_path, backed_up):
    link = tmp_path / "broken-data"
    try:
        os.symlink(tmp_path / "gone", link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make links")
    code, _, err = run_restore(config.Config(data=link, host="127.0.0.1", port=0), backed_up)
    assert code == 1 and "Nothing was changed" in err and "leads to no folder" in err
    assert link.is_symlink() and not (tmp_path / "gone").exists()


def test_a_listing_never_fails_on_a_time_the_system_cannot_show():
    # Near 1970 (Windows cannot place it on the local clock), absurdly far away, or not a
    # number at all: each still gives a line to print.
    assert cli._when(1_000_001).startswith(("1970-01-01", "1969-12-31"))
    assert cli._when(10**20) == "time unknown"
    assert cli._when(None) == "time unknown"  # type: ignore[arg-type]
