"""An app's own data: one SQLite file per app, and nothing of it on the Tape.

    <data>/apps/<id>/data/app.db      tables ``kv`` and ``records``, ``secure_delete`` on

The app id is the whole namespace. It is checked against the id rule before it becomes
part of a path, the path is never built from anything else an app sends, and keys, table
names, ids and values only ever travel as SQL parameters. So no key or record, however it
is written, can name another app's data.

    data = appdata.open_data(core, "water", manifest.tables)
    data.set("2026-11-19", 3, limits)                 # JSON values by key
    rows = data.records(limits)                       # rows of the declared tables
    rows.insert("book", {"title": "Dune"}, now)       # -> Stored(id, data, created, updated)

This module is where records are *kept*; what a record may hold, and every way of reading
them (views, filters, totals, computed fields), is ``described.Described``, which is
handed ``data.records(limits)`` as its storage. A record's id is a whole number given
here, counted across all of the app's tables and never used twice, so a link still
points at the same record after its table is renamed. A table or field renamed with
``was`` keeps its rows: they are moved to the new name when the database is next opened.

Every refusal is an ``AppDataError`` with one of the bridge's codes and a sentence for
the app's author.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.apps.described import Stored
from jimmyhq.ids import now_ms

if TYPE_CHECKING:
    from jimmyhq.core import Core

DB_FILE = "app.db"
MAX_KEY = 200
MAX_KEYS = 5_000
MAX_RECORD_ID = 2**53  # the largest whole number a browser keeps exactly

_APP_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}\Z")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,             -- JSON
    size INTEGER NOT NULL,           -- bytes of key and value
    updated INTEGER NOT NULL         -- unix ms, UTC
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- across every table, never used twice
    tbl TEXT NOT NULL,
    data TEXT NOT NULL,              -- JSON object of the declared fields
    size INTEGER NOT NULL,
    created INTEGER NOT NULL,
    updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS records_tbl ON records (tbl, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
"""

# The platform's, not the app's: an app's tables are rows in ``records``.
STORE = storeversion.declare("app_data", _SCHEMA, files=(f"apps/*/data/{DB_FILE}",))


class AppDataError(Exception):
    """A storage request that was not carried out. ``code`` is a bridge error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Limits:
    """What one app may hold. The numbers come from the owner's settings."""

    storage_bytes: int
    value_bytes: int
    records: int


UNLIMITED = Limits(2**62, 2**62, 2**62)


# --- the rules, one function each -----------------------------------------------------------


def check_app_id(app_id: Any) -> str:
    """The id if it is shaped like an app id, so it is safe as one folder name."""
    if not isinstance(app_id, str) or not _APP_ID.match(app_id):
        raise AppDataError("not_found", "There is no app with that id. Nothing was done.")
    return app_id


def check_key(key: Any) -> str:
    """A storage key: 1 to 200 characters of ordinary text."""
    if not isinstance(key, str) or not key:
        raise AppDataError("invalid", "A storage key must be text of at least one "
                                      "character. Nothing was done. Pass {\"key\": \"name\"}.")
    if len(key) > MAX_KEY:
        raise AppDataError("invalid", f"A storage key can be at most {MAX_KEY} characters, "
                                      f"and this one is {len(key)}. Nothing was done. Use a "
                                      "shorter key.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in key) or not _storable(key):
        raise AppDataError("invalid", "A storage key cannot hold control characters. "
                                      "Nothing was done. Use letters, digits and "
                                      "punctuation.")
    return key


def encode(value: Any, what: str = "The value") -> str:
    """A value as the JSON text that is stored, or a refusal if it is not JSON."""
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        text.encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
        raise AppDataError("invalid", f"{what} is not something JSON can hold (a number "
                                      "that is not finite, half a character, or nesting "
                                      "too deep). Nothing was saved.") from None
    return text


def check_size(size: int, limits: Limits, what: str = "A stored value") -> None:
    if size > limits.value_bytes:
        raise AppDataError(
            "too_large",
            f"{what} can be at most {_kb(limits.value_bytes)} KB, and this is "
            f"{_kb(size)} KB. Nothing was saved. Store less in one piece, or split it.")


def _storable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in text


def _kb(size: int) -> str:
    return f"{size / 1024:.0f}" if size >= 10 * 1024 else f"{size / 1024:.1f}"


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f}".removesuffix(".0")


UPLOADS = "uploads"  # the folder in an app's data folder where pictures it was given go


def folder_bytes(folder: Path) -> int:
    """What the ordinary files directly in a folder weigh. Nothing for a folder that is
    not there or is a link, which JimmyHQ never follows."""
    total = 0
    try:
        if folder.is_symlink() or not folder.is_dir():
            return 0
        with os.scandir(folder) as found:
            for entry in found:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


# --- one app's database ---------------------------------------------------------------------


class AppData:
    """One app's ``app.db``. Safe to use from several threads."""

    def __init__(self, path: Path, tables: dict[str, Any] | None = None):
        self.path = path
        self.tables: dict[str, Any] = tables or {}
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        storeversion.prepare(self._db, STORE)
        self._renames()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @property
    def mark(self) -> str:
        return _mark(self.tables)

    def _renames(self) -> None:
        """Move rows kept under a table's or a field's earlier name (``was``) to the
        name the manifest uses now. Runs once for each new shape of the tables."""
        mark = self.mark
        with self._lock, self._tx():
            row = self._db.execute("SELECT value FROM meta WHERE key='tables'").fetchone()
            if row and row[0] == mark:
                return
            for table, spec in self.tables.items():
                for old in spec.get("was", ()):
                    if old not in self.tables:
                        self._db.execute("UPDATE records SET tbl=? WHERE tbl=?", (table, old))
                moved = {old: name for name, f in spec["fields"].items()
                         for old in f.get("was", ()) if old not in spec["fields"]}
                if not moved:
                    continue
                for record_id, text in self._db.execute(
                        "SELECT id, data FROM records WHERE tbl=?", (table,)).fetchall():
                    data = json.loads(text)
                    if not any(old in data for old in moved):
                        continue
                    for old, name in moved.items():
                        if old in data:
                            value = data.pop(old)
                            data.setdefault(name, value)
                    new = encode(data)
                    self._db.execute("UPDATE records SET data=?, size=? WHERE id=?",
                                     (new, len(new.encode("utf-8")), record_id))
            self._db.execute("INSERT INTO meta (key, value) VALUES ('tables', ?) "
                             "ON CONFLICT (key) DO UPDATE SET value=excluded.value", (mark,))

    def _tx(self) -> _Tx:
        return _Tx(self._db)

    def _settle(self) -> None:
        """After a delete: fold the write-ahead log back in, so what was deleted is not
        left readable in it."""
        try:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass  # a reader is in the way; the next delete settles it

    # --- how much is held -------------------------------------------------------------------

    def _bytes(self) -> int:
        a = self._db.execute("SELECT COALESCE(SUM(size), 0) FROM kv").fetchone()[0]
        b = self._db.execute("SELECT COALESCE(SUM(size), 0) FROM records").fetchone()[0]
        return a + b

    def usage(self) -> dict[str, int]:
        """What the app holds: bytes of keys, values and records, and how many of each."""
        with self._lock:
            return {
                "bytes": self._bytes(),
                "keys": self._db.execute("SELECT COUNT(*) FROM kv").fetchone()[0],
                "records": self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0],
            }

    def _room(self, limits: Limits, old: int, new: int) -> None:
        # Pictures the owner gave the app count towards the same storage.
        total = self._bytes() + folder_bytes(self.path.parent / UPLOADS) - old + new
        if new > old and total > limits.storage_bytes:
            raise AppDataError(
                "quota",
                f"This app may keep at most {_mb(limits.storage_bytes)} MB in all, and "
                "this would take it over. Nothing was saved. Delete something first, or "
                "ask the owner to raise \"Storage for each app (MB)\" under Settings, Apps.")

    # --- values by key ----------------------------------------------------------------------

    def get(self, key: str) -> Any:
        """The value stored under a key, or None."""
        key = check_key(key)
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return None if row is None else json.loads(row[0])

    def set(self, key: str, value: Any, limits: Limits = UNLIMITED) -> None:
        key = check_key(key)
        text = encode(value)
        size = len(key.encode("utf-8")) + len(text.encode("utf-8"))
        check_size(size, limits)
        with self._lock, self._tx():
            row = self._db.execute("SELECT size FROM kv WHERE key=?", (key,)).fetchone()
            self._room(limits, row[0] if row else 0, size)
            self._db.execute(
                "INSERT INTO kv (key, value, size, updated) VALUES (?,?,?,?) ON CONFLICT "
                "(key) DO UPDATE SET value=excluded.value, size=excluded.size, "
                "updated=excluded.updated", (key, text, size, now_ms()))

    def delete(self, key: str) -> bool:
        """Remove a key. True if it was there."""
        key = check_key(key)
        with self._lock:
            gone = self._db.execute("DELETE FROM kv WHERE key=?", (key,)).rowcount > 0
            if gone:
                self._settle()
        return gone

    def keys(self, prefix: Any = "", limit: Any = None) -> list[str]:
        """The keys that start with ``prefix``, in order."""
        if prefix is None:
            prefix = ""
        if not isinstance(prefix, str) or len(prefix) > MAX_KEY:
            raise AppDataError("invalid", "\"prefix\" must be text of at most "
                                          f"{MAX_KEY} characters. Nothing was read.")
        limit = _limit(limit, MAX_KEYS, MAX_KEYS)
        with self._lock:
            rows = self._db.execute(
                "SELECT key FROM kv WHERE substr(key, 1, length(?)) = ? ORDER BY key LIMIT ?",
                (prefix, prefix, limit)).fetchall()
        return [r[0] for r in rows]

    # --- records ----------------------------------------------------------------------------

    def records(self, limits: Limits = UNLIMITED) -> Records:
        """The app's records as ``described.Described`` wants its storage, held to these
        limits."""
        return Records(self, limits)


class Records:
    """One app's rows, by table: the storage under ``described.Described``.

    Nothing here knows what a record means. A table name and an id only ever travel as
    SQL parameters, a write is refused when it would take the app over the owner's
    limits, and a delete is settled so the bytes do not linger in the log.
    """

    def __init__(self, data: AppData, limits: Limits = UNLIMITED):
        self._data = data
        self._db = data._db  # noqa: SLF001 - the same file, the same lock
        self._lock = data._lock  # noqa: SLF001
        self.limits = limits

    @staticmethod
    def _id(record_id: Any) -> int | None:
        ok = isinstance(record_id, int) and not isinstance(record_id, bool)
        return record_id if ok and 0 < record_id <= MAX_RECORD_ID else None

    @staticmethod
    def _stored(row: tuple[Any, ...]) -> Stored:
        return Stored(row[0], json.loads(row[1]), row[2], row[3])

    def get(self, table: str, record_id: int) -> Stored | None:
        if self._id(record_id) is None or not isinstance(table, str):
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT id, data, created, updated FROM records WHERE tbl=? AND id=?",
                (table, record_id)).fetchone()
        return self._stored(row) if row else None

    def all(self, table: str) -> list[Stored]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, data, created, updated FROM records WHERE tbl=? ORDER BY id",
                (table,)).fetchall()
        return [self._stored(row) for row in rows]

    def count(self, table: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM records WHERE tbl=?",
                                    (table,)).fetchone()[0]

    def _weigh(self, data: dict[str, Any]) -> tuple[str, int]:
        text = encode(data, "The record")
        size = len(text.encode("utf-8"))
        check_size(size, self.limits, "A record")
        return text, size

    def insert(self, table: str, data: dict[str, Any], now: int) -> Stored:
        text, size = self._weigh(data)
        with self._lock, self._data._tx():  # noqa: SLF001
            count = self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if count + 1 > self.limits.records:
                raise AppDataError(
                    "quota",
                    f"This app may keep at most {self.limits.records} records, and it has "
                    f"{count}. Nothing was saved. Delete some first, or ask the owner to "
                    "raise \"Records for each app\" under Settings, Apps.")
            self._data._room(self.limits, 0, size)  # noqa: SLF001
            record_id = self._db.execute(
                "INSERT INTO records (tbl, data, size, created, updated) VALUES (?,?,?,?,?)",
                (table, text, size, now, now)).lastrowid
        return Stored(record_id, json.loads(text), now, now)

    def replace(self, table: str, record_id: int, data: dict[str, Any], now: int
                ) -> Stored | None:
        text, size = self._weigh(data)
        with self._lock, self._data._tx():  # noqa: SLF001
            row = self._db.execute(
                "SELECT size, created FROM records WHERE tbl=? AND id=?",
                (table, self._id(record_id))).fetchone()
            if row is None:
                return None
            self._data._room(self.limits, row[0], size)  # noqa: SLF001
            self._db.execute("UPDATE records SET data=?, size=?, updated=? WHERE id=?",
                             (text, size, now, record_id))
        return Stored(record_id, json.loads(text), row[1], now)

    def delete(self, table: str, record_id: int) -> bool:
        with self._lock:
            gone = self._db.execute("DELETE FROM records WHERE tbl=? AND id=?",
                                    (table, self._id(record_id))).rowcount > 0
            if gone:
                self._data._settle()  # noqa: SLF001
        return gone

    def rename_table(self, old: str, new: str) -> int:
        """Move every record of ``old`` to ``new``. Ids do not change, so links hold."""
        with self._lock, self._data._tx():  # noqa: SLF001
            return self._db.execute("UPDATE records SET tbl=? WHERE tbl=?",
                                    (new, old)).rowcount


def _limit(value: Any, default: int, most: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= most:
        raise AppDataError("invalid", f"\"limit\" must be a whole number from 1 to {most}. "
                                      "Nothing was read.")
    return value


def _mark(tables: dict[str, Any]) -> str:
    text = json.dumps(tables, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Tx:
    """One write transaction: all of it happens, or none."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind: Any, *_: Any) -> None:
        self._db.execute("COMMIT" if kind is None else "ROLLBACK")


# --- where each app's database lives --------------------------------------------------------


def data_dir(core: Core, app_id: str) -> Path:
    """``<data>/apps/<id>/data``. The id is checked first, so the answer is always one
    folder directly under ``<data>/apps``."""
    return Path(core.config.data) / "apps" / check_app_id(app_id) / "data"


class Stores:
    """This process's open app databases, one for each app."""

    def __init__(self, core: Core):
        self.core = core
        self._lock = threading.Lock()
        self._open: dict[str, AppData] = {}

    def open(self, app_id: str, tables: dict[str, Any] | None = None) -> AppData:
        folder = data_dir(self.core, app_id)
        tables = tables or {}
        with self._lock:
            have = self._open.get(app_id)
            if have is not None and have.mark == _mark(tables):
                return have
            if have is not None:
                have.close()  # the app was updated and its tables changed
            for step in (folder.parent, folder):
                if step.is_symlink():
                    raise AppDataError("unavailable", "This app's data folder is a link, "
                                                      "which JimmyHQ does not follow. "
                                                      "Nothing was done.")
            self._open[app_id] = AppData(folder / DB_FILE, tables)
            return self._open[app_id]

    def drop(self, app_id: str) -> None:
        """Close an app's database, so its files can be moved, backed up or deleted."""
        with self._lock:
            have = self._open.pop(check_app_id(app_id), None)
        if have is not None:
            have.close()

    def close(self) -> None:
        with self._lock:
            stores, self._open = list(self._open.values()), {}
        for store in stores:
            store.close()


def stores(core: Core) -> Stores:
    return core.service("apps.data", Stores)


def open_data(core: Core, app_id: str, tables: dict[str, Any] | None = None) -> AppData:
    """The app's own database, created on first use."""
    return stores(core).open(app_id, tables)
