"""Schema versions: a store that cannot be rebuilt knows its version, an older file is
brought up to date step by step, and a file from a newer JimmyHQ is refused untouched."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from jimmyhq import storeversion
from jimmyhq.storeversion import Store, StoreTooNew
from jimmyhq.tape.store import connect

FIRST = """
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL);
"""


def toy(*steps, unversioned=None) -> Store:
    # Not declared: a toy store must not join the real ones' list.
    return Store("toy", FIRST, ("toy.db",), tuple(steps), unversioned)


def columns(conn, table="notes"):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_a_new_file_gets_the_schema_every_step_and_the_latest_version(tmp_path):
    conn = connect(tmp_path / "toy.db")
    store = toy("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
                lambda c: c.execute("CREATE TABLE tags (note INTEGER, tag TEXT)"))
    assert storeversion.prepare(conn, store) == 0
    assert storeversion.version_of(conn) == store.latest == 3
    assert columns(conn) == ["id", "text", "pinned"] and columns(conn, "tags") == ["note", "tag"]
    conn.close()


def test_a_file_from_before_versions_keeps_its_rows_gains_todays_shape_and_is_stamped(tmp_path):
    conn = connect(tmp_path / "toy.db")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")  # the shape before `text`
    conn.execute("INSERT INTO notes (id) VALUES (7)")

    def by_hand(c):
        if "text" not in columns(c):
            c.execute("ALTER TABLE notes ADD COLUMN text TEXT NOT NULL DEFAULT ''")

    store = toy(unversioned=by_hand)
    assert storeversion.prepare(conn, store) == 0
    assert storeversion.version_of(conn) == 1 and columns(conn) == ["id", "text"]
    assert [tuple(r) for r in conn.execute("SELECT id, text FROM notes")] == [(7, "")]
    conn.close()


def test_the_missing_steps_run_in_order_and_each_only_once(tmp_path):
    ran = []
    conn = connect(tmp_path / "toy.db")
    storeversion.prepare(conn, toy(lambda c: ran.append(2)))  # a file at version 2
    assert ran == [2]
    later = toy(lambda c: ran.append(2), lambda c: ran.append(3), lambda c: ran.append(4))
    assert storeversion.prepare(conn, later) == 2
    assert storeversion.prepare(conn, later) == 4  # already there: nothing runs again
    assert ran == [2, 3, 4] and storeversion.version_of(conn) == 4
    conn.close()


def test_a_step_that_fails_leaves_the_last_good_version_and_the_rows_as_they_were(tmp_path):
    conn = connect(tmp_path / "toy.db")
    storeversion.prepare(conn, toy())
    conn.execute("INSERT INTO notes (id, text) VALUES (1, 'kept')")

    def half_done(c):
        c.execute("ALTER TABLE notes ADD COLUMN half INTEGER")
        c.execute("UPDATE notes SET text = 'changed'")
        raise RuntimeError("the step broke half way")

    store = toy("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0", half_done)
    with pytest.raises(RuntimeError, match="half way"):
        storeversion.prepare(conn, store)
    assert storeversion.version_of(conn) == 2 and not conn.in_transaction
    assert columns(conn) == ["id", "text", "pinned"]  # the good step stays, the broken one is gone
    rows = [tuple(r) for r in conn.execute("SELECT id, text, pinned FROM notes")]
    assert rows == [(1, "kept", 0)]
    conn.close()


def test_a_file_from_a_newer_jimmyhq_is_refused_with_what_to_do_and_left_byte_for_byte(tmp_path):
    path = tmp_path / "toy.db"
    conn = connect(path)
    storeversion.prepare(conn, toy())
    conn.execute("INSERT INTO notes (id, text) VALUES (1, 'from the future')")
    conn.execute("PRAGMA user_version = 5")
    conn.close()
    before = path.read_bytes()
    conn = connect(path)
    with pytest.raises(StoreTooNew) as refused:
        storeversion.prepare(conn, toy("ALTER TABLE notes ADD COLUMN pinned INTEGER"))
    conn.close()
    assert path.read_bytes() == before
    message = str(refused.value)
    assert str(path) in message and "newer version of JimmyHQ" in message
    assert "version 5" in message and "up to version 2" in message
    assert "Nothing was changed" in message and "Update JimmyHQ" in message
    assert "backup" in message


def test_two_connections_preparing_one_fresh_file_at_once_both_succeed(tmp_path):
    store = toy("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    start, errors = threading.Barrier(4), []

    def open_it():
        conn = connect(tmp_path / "toy.db")
        try:
            start.wait()
            storeversion.prepare(conn, store)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=open_it) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    conn = connect(tmp_path / "toy.db")
    assert storeversion.version_of(conn) == 2 and columns(conn) == ["id", "text", "pinned"]
    conn.close()


def test_a_semicolon_inside_a_comment_or_a_string_does_not_end_a_statement():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    storeversion.run_script(conn, """
    -- a comment; with a semicolon, and 'quotes'
    CREATE TABLE a (x TEXT DEFAULT 'one;two');
    CREATE TABLE b (y INTEGER)""")
    assert conn.in_transaction  # executescript would have committed here
    conn.execute("INSERT INTO a DEFAULT VALUES")
    assert conn.execute("SELECT x FROM a").fetchone() == ("one;two",)
    conn.execute("ROLLBACK")


def test_a_store_name_is_declared_only_once():
    with pytest.raises(ValueError, match="already declared"):
        storeversion.declare("tape", FIRST, files=("other.db",))


def test_preparing_inside_an_open_transaction_is_refused(tmp_path):
    conn = connect(tmp_path / "toy.db")
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError, match="none open"):
        storeversion.prepare(conn, toy())
    conn.execute("ROLLBACK")
    conn.close()
