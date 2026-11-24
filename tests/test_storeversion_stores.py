"""Every store that cannot be rebuilt opens through ``storeversion``: a new file is
stamped, a file from the first release opens with its rows, and a file from a newer
JimmyHQ stops start-up with a sentence and is not touched."""

from __future__ import annotations

import importlib
import re
import socket
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jimmyhq import storeversion
from jimmyhq.tape.store import connect

ROOT = Path(__file__).resolve().parents[1]

for _module in storeversion.DECLARED_IN:  # every store declared, whatever ran first
    importlib.import_module(_module)


# --- how each store is opened, by its file ------------------------------------------------


def _tape(path):
    from jimmyhq.tape import Tape

    Tape(path).close()


def _auth(path):
    from jimmyhq.auth import Auth

    Auth(path).close()


def _offrecord(path):
    from jimmyhq.chat.offrecord import OffRecord

    off = OffRecord(SimpleNamespace(config=SimpleNamespace(data=path.parent),
                                    tape=SimpleNamespace(last_seq=lambda: 0)))
    with off._write():
        pass
    return off


def _memory_store(path):
    from jimmyhq.memory.store import Store

    Store(path).close()


def _village_log(path):
    from jimmyhq.village.log import SqliteLog

    SqliteLog(path).close()


def _village_commands(path):
    from jimmyhq.village import roster

    roster._Db(path).close()


def _village_cards(path):
    from jimmyhq.village.comms import benchlink

    v = SimpleNamespace(log=SimpleNamespace(path=path))
    benchlink.db(v)
    benchlink.close(v)


def _hands(path):
    from jimmyhq.hands.store import HandsStore

    HandsStore(path).close()


def _app_data(path):
    from jimmyhq.apps.appdata import AppData

    AppData(path).close()


def _apps_inbox(path):
    from jimmyhq.apps.inbox import Inbox

    Inbox(path).close()


def _apps_kinds(path):
    from jimmyhq.apps.schemas import KindStore

    KindStore(path).close()


def _apps_mounts(path):
    from jimmyhq.apps.files import Mounts

    Mounts(path).close()


def _apps_quotas(path):
    from jimmyhq.apps.quotas import Counters

    Counters(path).close()


def _apps_plugs(path):
    from jimmyhq.apps.plugs.state import State

    State(path).close()


def _apps_updates(path):
    from jimmyhq.apps.update import Found

    Found(path).close()


def _apps_counts(path):
    from jimmyhq.apps.counts import AppCounts

    AppCounts(path).close()


# (file the docs name, the store's declared name, where it lives in the data folder, opener)
OPENERS = [
    ("tape.db", "tape", "tape.db", _tape),
    ("auth.db", "auth", "auth.db", _auth),
    ("chat_offrecord.db", "chat_offrecord", "chat_offrecord.db", _offrecord),
    ("memory_store.db", "memory_store", "memory_store.db", _memory_store),
    ("village.db", "village", "village.db", _village_log),
    ("village.db", "village", "village.db", _village_commands),
    ("village.db", "village", "village.db", _village_cards),
    ("hands.db", "hands", "hands.db", _hands),
    ("app.db", "app_data", "apps/water/data/app.db", _app_data),
    ("apps.db", "apps", "apps/apps.db", _apps_inbox),
    ("apps.db", "apps", "apps/apps.db", _apps_kinds),
    ("apps.db", "apps", "apps/apps.db", _apps_mounts),
    ("apps.db", "apps", "apps/apps.db", _apps_quotas),
    ("apps.db", "apps", "apps/apps.db", _apps_plugs),
    ("apps.db", "apps", "apps/apps.db", _apps_updates),
    ("apps.db", "apps", "apps/apps.db", _apps_counts),
]
IDS = [f"{name}-{opener.__name__.strip('_')}" for _, name, _, opener in OPENERS]


def _no_rows_in_the_docs() -> set[str]:
    """Each ``.db`` named in a row of the Stores table whose Rebuildable says No."""
    text = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    names = set()
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[2].startswith("No"):
            names |= set(re.findall(r"`([^`]*\.db)`", cells[0] + " " + cells[1]))
    return {Path(n).name for n in names}


def test_every_store_the_docs_call_not_rebuildable_is_declared_and_opened_with_a_version():
    in_docs = _no_rows_in_the_docs()
    assert {"tape.db", "app.db", "apps.db"} <= in_docs  # the table was read
    declared = {Path(f).name for s in storeversion.STORES.values() for f in s.files}
    assert in_docs == declared, "a store that cannot be rebuilt needs storeversion.declare"
    assert in_docs == {doc for doc, *_ in OPENERS}, "and a line in OPENERS in this test"
    for _, name, where, _ in OPENERS:
        assert any(Path(where).match(f) for f in storeversion.STORES[name].files)


@pytest.mark.parametrize(("doc", "name", "where", "opener"), OPENERS, ids=IDS)
def test_a_new_store_file_is_stamped_with_its_latest_version(tmp_path, doc, name, where, opener):
    path = tmp_path / where
    path.parent.mkdir(parents=True, exist_ok=True)
    opener(path)
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == storeversion.STORES[name].latest
        if name == "apps":  # whichever part opens the file first makes every table
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            assert {"inbox", "heartbeats", "plugs", "kind_schemas", "mounts", "quota_counts",
                    "plug_jobs", "plug_cursors", "plug_told", "plug_emits",
                    "app_updates", "app_counts"} <= tables
        if name == "village":  # whichever part opens the file first makes every table
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            assert {"events", "commands", "cursors", "cards", "task_refs"} <= tables
    finally:
        conn.close()


@pytest.mark.parametrize(("doc", "name", "where", "opener"), OPENERS, ids=IDS)
def test_a_store_file_from_a_newer_jimmyhq_is_refused_and_left_byte_for_byte(
        tmp_path, doc, name, where, opener):
    path = tmp_path / where
    path.parent.mkdir(parents=True, exist_ok=True)
    opener(path)
    conn = connect(path)
    conn.execute(f"PRAGMA user_version = {storeversion.STORES[name].latest + 1}")
    conn.close()
    before = path.read_bytes()
    with pytest.raises(storeversion.StoreTooNew, match="Nothing was changed"):
        opener(path)
    assert path.read_bytes() == before


# --- files made by the first release, before versions existed -------------------------------


def test_a_tape_from_before_versions_and_erasure_keeps_its_events_and_is_stamped(tmp_path):
    from jimmyhq.tape import Tape

    path = tmp_path / "tape.db"
    conn = connect(path)
    conn.execute("""CREATE TABLE tape (
      seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, ts INTEGER NOT NULL,
      kind TEXT NOT NULL, v INTEGER NOT NULL, actor TEXT NOT NULL, cause TEXT, root TEXT,
      subject TEXT, payload TEXT NOT NULL)""")
    conn.execute("INSERT INTO tape (id, ts, kind, v, actor, payload) VALUES "
                 "('e1', 1800000000000, 'system.opened', 1, 'system', '{\"version\":\"0\"}')")
    conn.close()
    tape = Tape(path)
    try:
        assert tape.last_seq() == 1 and tape.get("e1").payload == {"version": "0"}
    finally:
        tape.close()
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == 1
        assert conn.execute("SELECT erased FROM tape").fetchall() == [(0,)]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "tape_pending" in tables
    finally:
        conn.close()


def test_an_off_record_file_from_before_versions_keeps_its_rows_and_gains_three_columns(
        tmp_path):
    path = tmp_path / "chat_offrecord.db"
    conn = connect(path)
    conn.execute("""CREATE TABLE conversations (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, title_source TEXT NOT NULL,
      target TEXT NOT NULL, effort TEXT, local_only INTEGER NOT NULL,
      created INTEGER NOT NULL, updated INTEGER NOT NULL,
      archived INTEGER NOT NULL DEFAULT 0, recorded INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE messages (
      seq INTEGER PRIMARY KEY, tape_seq INTEGER NOT NULL, id TEXT UNIQUE NOT NULL,
      conversation_id TEXT NOT NULL, role TEXT NOT NULL, created INTEGER NOT NULL,
      text TEXT NOT NULL, reply_to TEXT, facts TEXT)""")
    conn.execute("INSERT INTO conversations VALUES ('c1', 'Plans', 'owner', '{}', NULL, 1,"
                 " 1800000000000, 1800000000000, 1, 0)")
    conn.execute("INSERT INTO messages VALUES (1, 0, 'm1', 'c1', 'user', 1800000000000,"
                 " 'hello', NULL, NULL)")
    conn.close()
    from jimmyhq.chat.offrecord import OffRecord

    off = OffRecord(SimpleNamespace(config=SimpleNamespace(data=tmp_path),
                                    tape=SimpleNamespace(last_seq=lambda: 0)))
    conv, messages, _ = off.story("c1")
    assert conv["title"] == "Plans" and conv["use_memory"] is True and conv["archived"]
    assert conv["archived_at"] is not None  # its clock starts when it is brought up to date
    assert [m["text"] for m in messages] == ["hello"]
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == 1
        names = [r[1] for r in conn.execute("PRAGMA table_info(conversations)")]
        assert names[-3:] == ["use_memory", "archived_at", "delete_marked_at"]
    finally:
        conn.close()


# The stores whose shape before versions differed from their first schema, each with a
# test above that builds a file in that true older shape. A store that gains an
# ``unversioned`` step needs one too.
OLDER_SHAPE_TESTED = {
    "tape": test_a_tape_from_before_versions_and_erasure_keeps_its_events_and_is_stamped,
    "chat_offrecord":
        test_an_off_record_file_from_before_versions_keeps_its_rows_and_gains_three_columns,
}


def test_every_store_whose_shape_before_versions_differed_is_tested_from_that_shape():
    changed = {name for name, store in storeversion.STORES.items()
               if store.unversioned is not None}
    assert changed == set(OLDER_SHAPE_TESTED)


@pytest.mark.parametrize(("doc", "name", "where", "opener"),
                         [o for o in OPENERS if o[1] not in OLDER_SHAPE_TESTED],
                         ids=[i for i, o in zip(IDS, OPENERS, strict=True)
                              if o[1] not in OLDER_SHAPE_TESTED])
def test_an_unstamped_file_already_in_a_stores_first_schema_keeps_its_rows_and_is_stamped(
        tmp_path, doc, name, where, opener):
    """What this proves: a file holding a store's first schema but no version (version 0)
    is stamped, every step runs, and rows already there are kept. It stands for a file
    from before versions only for the stores whose shape had not changed then (on the
    main branch, none but the two in ``OLDER_SHAPE_TESTED`` had), and the check above
    keeps it that way."""
    path = tmp_path / where
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    storeversion.run_script(conn, storeversion.STORES[name].schema)  # as the release made it
    conn.execute("CREATE TABLE kept_by_hand (x)")  # stands in for the rows it held
    conn.execute("INSERT INTO kept_by_hand VALUES ('still here')")
    assert storeversion.version_of(conn) == 0
    conn.close()
    opener(path)
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == storeversion.STORES[name].latest
        assert conn.execute("SELECT x FROM kept_by_hand").fetchall() == [("still here",)]
    finally:
        conn.close()


# --- start-up --------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_starting_on_data_from_a_newer_jimmyhq_ends_with_the_sentence_and_changes_nothing(
        tmp_path, monkeypatch, capsys):
    from jimmyhq import cli, supervisor

    hands = tmp_path / "hands.db"  # a store nothing opens until a child needs it
    _hands(hands)
    conn = connect(hands)
    conn.execute(f"PRAGMA user_version = {storeversion.STORES['hands'].latest + 1}")
    conn.close()
    before = hands.read_bytes()
    monkeypatch.setenv("JIMMYHQ_DATA", str(tmp_path))
    monkeypatch.setenv("JIMMYHQ_HOST", "127.0.0.1")
    monkeypatch.setenv("JIMMYHQ_PORT", str(_free_port()))
    monkeypatch.setattr(cli, "_logging", lambda *_: None)
    monkeypatch.setattr(supervisor, "run", lambda *_, **__: pytest.fail("a child was started"))
    assert cli.main(["run"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("JimmyHQ cannot start: ") and "Traceback" not in err
    assert str(hands) in err and "newer version of JimmyHQ" in err and "Update JimmyHQ" in err
    assert hands.read_bytes() == before
    assert not (tmp_path / "tape.db").exists()  # stopped before any store was opened
