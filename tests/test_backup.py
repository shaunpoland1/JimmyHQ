"""Snapshots of the data folder: what goes in, what is left out, that a live database is
copied whole, that a half-made one is never listed, pruning, and the restore test."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from jimmyhq.backup import snapshot
from jimmyhq.backup.snapshot import BackupError
from jimmyhq.tape.store import connect

NOW = 1_795_366_080_123  # 2026-11-22, in unix ms


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "memory").mkdir()
    (folder / "memory" / "note.md").write_text("A note.\n", encoding="utf-8")
    (folder / "secrets.json").write_text("{}", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(folder / "secrets.json", 0o600)
    (folder / "logs").mkdir()
    (folder / "logs" / "web.log").write_text("a line\n", encoding="utf-8")
    (folder / "supervisor.lock").write_bytes(b"")
    venv = folder / "apps" / "demo" / "venvs" / "1.0-abcdef12"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = here\n", encoding="utf-8")
    (folder / "apps" / "demo" / "data").mkdir()
    (folder / "apps" / "demo" / "data" / "values.json").write_text("{}", encoding="utf-8")
    conn = connect(folder / "views.db")  # a store JimmyHQ can rebuild
    conn.execute("CREATE TABLE t (x)")
    conn.close()
    return folder


@pytest.fixture
def dest(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    return folder


def make(data, dest, now=NOW, **kw):
    return snapshot.make(data, dest, now_ms=now, version="0.1.0", source="0123456789ab", **kw)


def live_store(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS rows (n INTEGER)")
    return conn


def test_a_snapshot_of_a_live_write_ahead_store_holds_its_committed_rows_and_not_the_uncommitted(
        data, dest):
    writer = live_store(data / "tape.db")
    # Hold off checkpoints, so the committed rows are only in the -wal file.
    writer.execute("PRAGMA wal_autocheckpoint=0")
    for n in range(50):
        writer.execute("INSERT INTO rows VALUES (?)", (n,))
    assert (data / "tape.db-wal").stat().st_size > 0
    other = connect(data / "tape.db")
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO rows VALUES (999)")  # not committed while the backup runs
    try:
        made = make(data, dest)
    finally:
        other.execute("ROLLBACK")
        other.close()
        writer.close()
    copy = sqlite3.connect(made.path / "data" / "tape.db")
    try:
        assert copy.execute("SELECT count(*), max(n) FROM rows").fetchone() == (50, 49)
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        copy.close()
    assert not (made.path / "data" / "tape.db-wal").exists()


def test_the_manifest_lists_every_file_with_a_sha256_that_matches_the_copy(data, dest):
    made = make(data, dest)
    manifest = json.loads((made.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["jimmyhq_backup"] == 1 and manifest["made_ms"] == NOW
    assert manifest["version"] == "0.1.0" and manifest["source"] == "0123456789ab"
    paths = {f["path"] for f in manifest["files"]}
    assert {"memory/note.md", "secrets.json", "apps/demo/data/values.json"} <= paths
    for entry in manifest["files"]:
        size, digest = snapshot.hash_file(made.path / "data" / entry["path"])
        assert (size, digest) == (entry["size"], entry["sha256"])
    assert made.files == len(manifest["files"])


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no permission bits")
def test_an_owner_only_file_stays_owner_only_in_the_backup(data, dest):
    made = make(data, dest)
    assert (made.path / "data" / "secrets.json").stat().st_mode & 0o777 == 0o600
    assert made.path.stat().st_mode & 0o777 == 0o700


def test_logs_locks_app_environments_and_rebuildable_stores_are_left_out_by_default(data, dest):
    made = make(data, dest)
    inside = made.path / "data"
    assert not (inside / "logs").exists()
    assert not (inside / "supervisor.lock").exists()
    assert not (inside / "apps" / "demo" / "venvs").exists()
    assert not (inside / "views.db").exists()
    assert {"logs", "locks", "app_environments", "rebuildable"} <= set(made.left_out)


def test_only_jimmyhqs_own_lock_files_at_the_top_are_left_out_and_an_apps_uv_lock_goes_in(
        data, dest):
    for name in snapshot.OWN_LOCKS:
        (data / name).write_bytes(b"")
    version = data / "apps" / "demo" / "1.0-abcdef12"
    version.mkdir(parents=True)
    (version / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (version / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (data / "memory" / "notes.lock").write_text("a note's name\n", encoding="utf-8")
    made = make(data, dest)
    inside = made.path / "data"
    assert (inside / "apps" / "demo" / "1.0-abcdef12" / "uv.lock").read_text(
        encoding="utf-8") == "version = 1\n"
    assert (inside / "memory" / "notes.lock").exists()
    assert not [n for n in snapshot.OWN_LOCKS if (inside / n).exists()]
    assert made.left_out["locks"] == len(snapshot.OWN_LOCKS)
    assert snapshot.verify(made.path).passed


def test_os_clutter_is_never_copied_and_does_not_fail_the_test_when_it_appears_later(
        data, dest):
    for name in (".DS_Store", "._note.md", "Thumbs.db", "desktop.ini"):
        (data / "memory" / name).write_bytes(b"\x00clutter")
    made = make(data, dest)
    inside = made.path / "data"
    assert sorted(p.name for p in (inside / "memory").iterdir()) == ["note.md"]
    assert made.left_out["clutter"] == 4
    # A file manager opening the backup drops its own.
    (inside / ".DS_Store").write_bytes(b"x")
    (inside / "memory" / "._note.md").write_bytes(b"x")
    (inside / "memory" / "THUMBS.DB").write_bytes(b"x")
    assert snapshot.verify(made.path, full=False).passed
    found = snapshot.verify(made.path, full=True)
    assert found.passed, found.lines()


def test_no_bookmark_in_a_backup_is_ahead_of_the_tape_it_comes_with(data, dest, monkeypatch):
    """While a backup runs, events keep arriving and other stores note how far they have
    read. Whatever moment each file is copied at, the copy of the Tape holds every event
    a copied bookmark points at, and every Vault file an event in it names."""
    from jimmyhq.tape.store import Tape

    tape = Tape(data / "tape.db")
    marks = sqlite3.connect(data / "village.db", isolation_level=None)
    marks.execute("CREATE TABLE cursors (name TEXT PRIMARY KEY, seq INTEGER NOT NULL)")
    marks.execute("INSERT INTO cursors VALUES ('tape', 0)")
    (data / "vault").mkdir()
    real_sqlite, real_file = snapshot._copy_sqlite, snapshot.copy_file
    order = []

    def busy_moment():
        event = tape.print("system.opened", {"version": "0.1.0"}, actor="system")
        (data / "vault" / f"{event.seq:04d}").write_text("named by an event", encoding="utf-8")
        marks.execute("UPDATE cursors SET seq = ?", (event.seq,))

    def copying_sqlite(source, target):
        order.append(source.name)
        found = real_sqlite(source, target)
        busy_moment()
        return found

    def copying_file(source, target):
        order.append(source.parent.name + "/" + source.name)
        return real_file(source, target)

    busy_moment()
    monkeypatch.setattr(snapshot, "_copy_sqlite", copying_sqlite)
    monkeypatch.setattr(snapshot, "copy_file", copying_file)
    try:
        made = make(data, dest)
    finally:
        tape.close()
        marks.close()
    inside = made.path / "data"
    copied = sqlite3.connect(inside / "tape.db")
    bookmark = sqlite3.connect(inside / "village.db")
    try:
        last = copied.execute("SELECT max(seq) FROM tape").fetchone()[0]
        assert bookmark.execute("SELECT seq FROM cursors").fetchone()[0] <= last
        for (seq,) in copied.execute("SELECT seq FROM tape"):
            assert (inside / "vault" / f"{seq:04d}").exists()
    finally:
        copied.close()
        bookmark.close()
    assert order.index("tape.db") == max(i for i, n in enumerate(order) if n.endswith(".db"))
    assert order.index("tape.db") < min(i for i, n in enumerate(order) if "/" in n)


def test_the_rebuildable_stores_go_in_when_the_owner_asks(data, dest):
    made = make(data, dest, include_rebuildable=True)
    assert (made.path / "data" / "views.db").exists()


def test_a_link_is_never_followed_out_of_the_data_folder(data, dest, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("not JimmyHQ's", encoding="utf-8")
    try:
        os.symlink(outside, data / "memory" / "elsewhere", target_is_directory=True)
        os.symlink(outside / "private.txt", data / "linked.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make links")
    made = make(data, dest)
    assert not (made.path / "data" / "memory" / "elsewhere").exists()
    assert not (made.path / "data" / "linked.txt").exists()
    assert made.left_out["links"] == 2


def test_a_half_made_backup_is_never_listed_and_is_cleared_by_the_next(data, dest):
    first = make(data, dest)
    half = dest / f".{snapshot.snapshot_name(NOW + 5)}-{snapshot.tag_of('0123456789ab')}.partial"
    half.mkdir()
    (half / "manifest.json").write_text(json.dumps({"jimmyhq_backup": 1}), encoding="utf-8")
    (dest / snapshot.snapshot_name(NOW + 6)).mkdir()  # the right name, but no manifest
    assert [s.name for s in snapshot.snapshots(dest)] == [first.name]
    make(data, dest, now=NOW + 10)
    assert not half.exists()
    assert len(snapshot.snapshots(dest)) == 2


def test_leftovers_of_another_installations_backup_in_progress_are_left_alone_for_a_day(
        data, dest):
    mine = dest / f".{snapshot.snapshot_name(NOW - 9)}-{snapshot.tag_of('0123456789ab')}.partial"
    theirs = dest / f".{snapshot.snapshot_name(NOW - 8)}-{snapshot.tag_of('ffffffffffff')}.partial"
    stale = dest / f".{snapshot.snapshot_name(NOW - 7)}-{snapshot.tag_of('eeeeeeeeeeee')}.pruning"
    older_kind = dest / f".{snapshot.snapshot_name(NOW - 6)}.partial"  # before ids
    for folder in (mine, theirs, stale, older_kind):
        (folder / "data").mkdir(parents=True)
    long_ago = (NOW - 2 * snapshot.LEFTOVER_MS) / 1000
    for folder in (stale, older_kind):
        for path in (folder / "data", folder):
            os.utime(path, (long_ago, long_ago))
    make(data, dest)
    assert not mine.exists() and not stale.exists() and not older_kind.exists()
    assert theirs.exists()  # another computer may be writing it right now


def test_a_snapshot_name_is_safe_everywhere_sorts_by_time_and_reads_back(data):
    name = snapshot.snapshot_name(NOW)
    assert name == "jimmyhq-backup-20261122T164800123Z"
    assert not set(name) & set(':<>"/\\|?*')
    assert snapshot.made_at(name) == NOW
    assert snapshot.snapshot_name(NOW + 1) > name
    assert snapshot.made_at("jimmyhq-backup-x") is None


def test_pruning_keeps_the_newest_n_of_this_data_folder_and_leaves_others_alone(data, dest):
    names = [make(data, dest, now=NOW + n).name for n in range(4)]
    other = snapshot.make(data, dest, now_ms=NOW - 1, version="0.1.0", source="ffffffffffff")
    removed = snapshot.prune(dest, keep=2, source="0123456789ab", protect=names[-1])
    assert removed == [names[1], names[0]]
    assert {s.name for s in snapshot.snapshots(dest)} == {names[3], names[2], other.name}


def test_a_backup_dated_ahead_of_the_clock_is_listed_last_and_does_not_hold_a_kept_place(
        data, dest):
    import time

    now = int(time.time() * 1000)
    ahead = make(data, dest, now=now + 30 * 86_400_000).name  # made while the clock ran ahead
    names = [make(data, dest, now=now - 3000 + n).name for n in range(3)]
    listed = snapshot.snapshots(dest)
    assert [s.name for s in listed] == [*names[::-1], ahead]
    assert listed[-1].ahead and not listed[0].ahead
    removed = snapshot.prune(dest, keep=3, source="0123456789ab", protect=names[-1])
    assert removed == [ahead]


def test_pruning_never_removes_the_only_good_backup(data, dest):
    made = make(data, dest)
    assert snapshot.prune(dest, keep=0, source="0123456789ab", protect=made.name) == []
    assert [s.name for s in snapshot.snapshots(dest)] == [made.name]


def test_a_whole_backup_passes_the_restore_test(data, dest):
    made = make(data, dest)
    found = snapshot.verify(made.path, full=True)
    assert found.passed, found.lines()
    assert found.files == made.files and found.bytes == made.bytes


def test_a_changed_file_fails_the_restore_test(data, dest):
    made = make(data, dest)
    note = made.path / "data" / "memory" / "note.md"
    note.write_text("A nate.\n", encoding="utf-8")  # same size, different bytes
    found = snapshot.verify(made.path, full=True)
    assert not found.passed
    assert found.lines() == ["memory/note.md: Its contents have changed since the backup "
                             "was made."]
    # On the record a file deeper than the top is named only by its folder.
    assert found.short() == ["A file in memory/: Its contents have changed since the backup "
                             "was made."]


def test_a_damaged_database_fails_the_restore_test(data, dest):
    conn = live_store(data / "tape.db")
    conn.executemany("INSERT INTO rows VALUES (?)", [(n,) for n in range(2000)])
    conn.close()
    made = make(data, dest)
    target = made.path / "data" / "tape.db"
    raw = bytearray(target.read_bytes())
    raw[4096: 4096 + 64] = b"\xff" * 64  # scribble over the second page's header
    target.write_bytes(bytes(raw))
    manifest_path = made.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:  # the manifest agrees, so only SQLite can tell
        if entry["path"] == "tape.db":
            entry["sha256"] = snapshot.hash_file(target)[1]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    found = snapshot.verify(made.path, full=True)
    assert not found.passed
    assert any(rel == "tape.db" for rel, _ in found.problems)


def test_a_file_missing_or_added_fails_even_the_quick_check(data, dest):
    made = make(data, dest)
    (made.path / "data" / "memory" / "note.md").unlink()
    (made.path / "data" / "extra.txt").write_text("x", encoding="utf-8")
    found = snapshot.verify(made.path, full=False)
    assert not found.passed
    assert {rel for rel, _ in found.problems} == {"memory/note.md", "extra.txt"}


def test_a_manifest_that_names_a_file_outside_the_backup_is_refused(data, dest):
    made = make(data, dest)
    manifest_path = made.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({"path": "../outside.txt", "size": 1, "sha256": "0" * 64})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    found = snapshot.verify(made.path)
    assert not found.passed and "outside the backup" in found.lines()[0]


def test_a_store_from_a_newer_jimmyhq_fails_the_restore_test_with_the_store_too_new_words(
        data, dest):
    from jimmyhq.tape.store import Tape

    Tape(data / "tape.db").close()
    made = make(data, dest)
    target = made.path / "data" / "tape.db"
    conn = sqlite3.connect(target)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    manifest_path = made.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "tape.db":
            entry["size"], entry["sha256"] = snapshot.hash_file(target)
    manifest["stores"]["tape.db"]["version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    found = snapshot.verify(made.path, full=True)
    assert not found.passed and found.too_new is not None
    assert "written by a newer version of JimmyHQ" in str(found.too_new)


def _rewrite(made, change):
    manifest_path = made.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    change(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _restamp(made, rel, **extra):
    target = made.path / "data" / rel

    def change(manifest):
        for entry in manifest["files"]:
            if entry["path"] == rel:
                entry["size"], entry["sha256"] = snapshot.hash_file(target)
        for key, value in extra.items():
            manifest["stores"][rel][key] = value
    _rewrite(made, change)


def _too_new(made):
    conn = sqlite3.connect(made.path / "data" / "tape.db")
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    _restamp(made, "tape.db", version=99)


def _not_a_database(made):
    target = made.path / "data" / "tape.db"
    target.write_bytes(b"\x01" * target.stat().st_size)
    _restamp(made, "tape.db")


def _unreadable_database(made):
    target = made.path / "data" / "tape.db"
    target.unlink()
    target.mkdir()


FAILURES = {
    "no manifest": lambda made: (made.path / "manifest.json").unlink(),
    "newer layout": lambda made: _rewrite(made, lambda m: m.update(jimmyhq_backup=99)),
    "no file list": lambda made: _rewrite(made, lambda m: m.update(files=None)),
    "a path outside": lambda made: _rewrite(made, lambda m: m["files"].append(
        {"path": "../x", "size": 1, "sha256": "0"})),
    "missing": lambda made: (made.path / "data" / "memory" / "note.md").unlink(),
    "another size": lambda made: (made.path / "data" / "memory" / "note.md").write_text(
        "longer than it was\n", encoding="utf-8"),
    "changed": lambda made: (made.path / "data" / "memory" / "note.md").write_text(
        "A nate.\n", encoding="utf-8"),
    "extra": lambda made: (made.path / "data" / "extra.txt").write_text("x", encoding="utf-8"),
    "not a database": _not_a_database,
    "unreadable database": _unreadable_database,
    "another schema version": lambda made: _rewrite(
        made, lambda m: m["stores"]["tape.db"].update(version=7)),
    "too new": _too_new,
}


@pytest.mark.parametrize("full", [True, False], ids=["full", "quick"])
@pytest.mark.parametrize("failure", list(FAILURES))
def test_no_failure_of_the_restore_test_names_a_full_path(data, dest, tmp_path, failure, full):
    from jimmyhq.tape.store import Tape

    Tape(data / "tape.db").close()
    made = make(data, dest)
    FAILURES[failure](made)
    found = snapshot.verify(made.path, full=full)
    assert not found.passed or (failure == "changed" and not full)  # only a hash tells
    said = " ".join([*found.lines(), *found.short(), str(found.too_new or "")])
    for spelled in {str(tmp_path), str(tmp_path.resolve()), str(made.path)}:
        assert spelled not in said
    assert not re.search(r"(^|[\s(\"'])(/|[A-Za-z]:\\)", said), said


def test_a_destination_inside_the_data_folder_is_refused_with_a_sentence(data):
    inside = data / "memory" / "backups"
    inside.mkdir()
    with pytest.raises(BackupError, match="inside JimmyHQ's data folder"):
        make(data, inside)
    with pytest.raises(BackupError, match="inside JimmyHQ's data folder"):
        make(data, data)
    assert not any(p.name.startswith(".jimmyhq-backup") for p in inside.iterdir())


def test_a_missing_destination_is_a_plain_failure_without_its_path(data, tmp_path):
    missing = tmp_path / "unplugged-drive"
    with pytest.raises(BackupError) as caught:
        make(data, missing)
    assert "is not there" in str(caught.value) and str(tmp_path) not in str(caught.value)


def test_a_failed_check_leaves_the_backup_hidden_and_older_ones_as_they_were(
        data, dest, monkeypatch):
    first = make(data, dest)
    monkeypatch.setattr(snapshot, "verify", lambda *_a, **_k: snapshot.Check(
        passed=False, full=False, problems=[("tape.db", "It is damaged.")]))
    with pytest.raises(BackupError, match="did not pass its check"):
        make(data, dest, now=NOW + 1)
    assert [s.name for s in snapshot.snapshots(dest)] == [first.name]


def test_a_database_deleted_while_the_backup_runs_is_counted_gone_and_not_made_again(
        data, dest, monkeypatch):
    live_store(data / "hands.db").close()
    real_plan = snapshot.plan

    def plan_then_delete(*a, **kw):
        found = real_plan(*a, **kw)
        if (data / "hands.db").exists():
            (data / "hands.db").unlink()  # removed after it was planned
        return found

    monkeypatch.setattr(snapshot, "plan", plan_then_delete)
    made = make(data, dest)
    assert not (data / "hands.db").exists()  # the live data folder is as it was left
    assert not (made.path / "data" / "hands.db").exists()
    assert made.left_out["gone"] == 1


def _watch_disk(monkeypatch):
    """What reached the disk (by inode) before each rename."""
    log = []
    real_fsync, real_rename = os.fsync, os.rename

    def fsync(fd):
        log.append(("sync", os.fstat(fd).st_ino))
        return real_fsync(fd)

    def rename(a, b):
        log.append(("rename", os.path.basename(str(b))))
        return real_rename(a, b)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "rename", rename)
    return log


def _synced_before(log, rename_to):
    upto = log.index(("rename", rename_to))
    return {ino for what, ino in log[:upto] if what == "sync"}


def test_every_file_and_folder_of_a_backup_is_on_the_disk_before_it_is_put_in_place(
        data, dest, monkeypatch):
    live_store(data / "tape.db").close()
    log = _watch_disk(monkeypatch)
    made = make(data, dest)
    synced = _synced_before(log, made.name)
    for here, _dirs, names in os.walk(made.path):
        for name in names:
            assert os.stat(os.path.join(here, name)).st_ino in synced, name
        if sys.platform != "win32":
            assert os.stat(here).st_ino in synced, here
    if sys.platform != "win32":  # and the folder that holds it, before anything is pruned
        after = [ino for what, ino in log[log.index(("rename", made.name)):] if what == "sync"]
        assert os.stat(dest).st_ino in after
