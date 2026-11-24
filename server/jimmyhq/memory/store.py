"""What memory keeps that cannot be worked out again: ``memory_store.db``.

``memory.db`` is an index. It can be thrown away and rebuilt from the Library's
files and the Tape, and people are told so. This file is the opposite: it holds
the text of earlier versions of notes, what mining proposed and what the owner
said no to, and how far each source has been read. None of that can be rebuilt,
and all of it is private, so it lives apart from the index and **really
deletes**: ``secure_delete`` overwrites removed rows with zeros, and
``scrub`` empties the write-ahead log so no earlier page image is left in it.

Version bodies are stored once per content hash. ``versions`` links each saved
version of a note (the id of its ``memory.note.saved`` event) to a body, and a
body goes when the last version that points at it goes.

Both processes open this file. Every change is one ``BEGIN IMMEDIATE``
transaction, so SQLite serialises them; nothing here is cached in memory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from jimmyhq import storeversion
from jimmyhq.tape.store import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bodies (
  hash TEXT PRIMARY KEY, data BLOB NOT NULL, size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
  version TEXT PRIMARY KEY, path TEXT NOT NULL, hash TEXT NOT NULL, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_path ON versions(path, ts);
CREATE INDEX IF NOT EXISTS idx_versions_hash ON versions(hash);
CREATE TABLE IF NOT EXISTS cursors (
  source TEXT PRIMARY KEY, cursor TEXT, read INTEGER NOT NULL DEFAULT 0,
  last_read_ms INTEGER, fails INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, last_error_ms INTEGER, waiting INTEGER, waiting_more INTEGER NOT NULL DEFAULT 0,
  waiting_ms INTEGER
);
CREATE TABLE IF NOT EXISTS tallies (
  source TEXT NOT NULL, span TEXT NOT NULL, name TEXT NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (source, span, name)
);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, statement TEXT NOT NULL, norm TEXT NOT NULL,
  kind TEXT NOT NULL, confidence REAL NOT NULL, evidence TEXT NOT NULL, quotes TEXT NOT NULL,
  proposed_ms INTEGER NOT NULL, state TEXT NOT NULL, state_ms INTEGER NOT NULL,
  route TEXT NOT NULL, auto INTEGER NOT NULL DEFAULT 0, reason TEXT,
  fact_id TEXT, card_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_state ON candidates(state, proposed_ms);
CREATE INDEX IF NOT EXISTS idx_candidates_norm ON candidates(norm);
CREATE INDEX IF NOT EXISTS idx_candidates_fact ON candidates(fact_id);
CREATE TABLE IF NOT EXISTS declined (
  id TEXT PRIMARY KEY, norm_hash TEXT NOT NULL, statement TEXT NOT NULL, reason TEXT,
  at_ms INTEGER NOT NULL, fact_id TEXT, candidate_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_declined_hash ON declined(norm_hash);
CREATE INDEX IF NOT EXISTS idx_declined_fact ON declined(fact_id);
CREATE TABLE IF NOT EXISTS activity (
  id TEXT PRIMARY KEY, ts INTEGER NOT NULL, type TEXT NOT NULL, actor TEXT NOT NULL,
  fact_id TEXT, note TEXT, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_fact ON activity(fact_id);
"""

STORE = storeversion.declare("memory_store", _SCHEMA, files=("memory_store.db",))

ACTIVITY_CAP = 20_000  # rows kept in the timeline; the Tape keeps the full record


def secure(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Make every delete on this connection overwrite what it removes."""
    conn.execute("PRAGMA secure_delete=ON")
    return conn


def scrub(conn: sqlite3.Connection, lock: Any) -> bool:
    """Move everything out of the write-ahead log and empty it, so no page
    image from before a delete is left on disk. False when another reader held
    the log the whole time; the caller notes that and tries again later.

    It gives up after about two seconds. Each try waits only briefly for a
    reader, not the connection's usual busy time: this runs while the Library
    is held, and a delete must not hang for minutes behind a long read."""
    for attempt in range(6):
        try:
            with lock:
                usual = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                conn.execute("PRAGMA busy_timeout = 200")
                try:
                    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                finally:
                    conn.execute(f"PRAGMA busy_timeout = {int(usual)}")
        except sqlite3.OperationalError:
            row = (1,)
        if row is not None and row[0] == 0:
            return True
        time.sleep(0.05 * (attempt + 1))
    return False


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._db = secure(connect(self.path))
        with self._lock:
            storeversion.prepare(self._db, STORE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- plumbing -------------------------------------------------------------

    @contextlib.contextmanager
    def tx(self):
        """One change, all or nothing, serialised across threads and processes."""
        with self._lock:
            if self._db.in_transaction:  # nested: part of the change already open
                yield self._db
                return
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def all(self, sql: str, params: tuple | list = ()) -> list[Any]:
        with self._lock:
            return self._db.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> Any:
        with self._lock:
            return self._db.execute(sql, tuple(params)).fetchone()

    def meta(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO meta (key, value) VALUES (?, ?)"
                       " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (key, json.dumps(value)))

    def scrub(self) -> bool:
        return scrub(self._db, self._lock)

    # --- versions of notes ----------------------------------------------------

    def keep(self, version: str, path: str, data: bytes, ts: int) -> str:
        """Store one saved version of a note. Returns the body's hash."""
        digest = sha(data)
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO bodies (hash, data, size) VALUES (?,?,?)",
                       (digest, data, len(data)))
            db.execute("INSERT OR REPLACE INTO versions (version, path, hash, ts)"
                       " VALUES (?,?,?,?)", (version, path, digest, ts))
        return digest

    def body(self, version: str) -> bytes | None:
        row = self.one("SELECT b.data FROM versions v JOIN bodies b ON b.hash = v.hash"
                       " WHERE v.version = ?", (version,))
        return bytes(row["data"]) if row else None

    def stored(self, versions: list[str]) -> set[str]:
        """Which of these versions still have their text."""
        out: set[str] = set()
        for start in range(0, len(versions), 500):
            part = versions[start:start + 500]
            marks = ",".join("?" * len(part))
            out.update(r["version"] for r in self.all(
                f"SELECT version FROM versions WHERE version IN ({marks})", part))  # noqa: S608
        return out

    def _drop_orphans(self, db: sqlite3.Connection) -> int:
        cur = db.execute("DELETE FROM bodies WHERE hash NOT IN (SELECT hash FROM versions)")
        return cur.rowcount

    def purge(self, versions: list[str], paths: list[str] = ()) -> dict[str, int]:
        """Remove these versions, and every version recorded under ``paths``.
        A body goes with the last version that pointed at it."""
        gone = 0
        with self.tx() as db:
            for start in range(0, len(versions), 500):
                part = versions[start:start + 500]
                marks = ",".join("?" * len(part))
                gone += db.execute(
                    f"DELETE FROM versions WHERE version IN ({marks})", part).rowcount  # noqa: S608
            for path in paths:
                gone += db.execute("DELETE FROM versions WHERE path = ?", (path,)).rowcount
            bodies = self._drop_orphans(db)
        return {"versions": gone, "bodies": bodies}

    def purge_containing(self, needles: list[bytes]) -> dict[str, Any]:
        """Remove every version of any note whose text contains one of
        ``needles``. Returns the counts and the notes that lost versions."""
        with self.tx() as db:
            hashes: set[str] = set()
            for needle in needles:
                if needle:
                    hashes.update(r["hash"] for r in db.execute(
                        "SELECT hash FROM bodies WHERE instr(data, ?) > 0", (needle,)))
            paths: set[str] = set()
            gone = 0
            for digest in hashes:
                paths.update(r["path"] for r in db.execute(
                    "SELECT DISTINCT path FROM versions WHERE hash = ?", (digest,)))
                gone += db.execute("DELETE FROM versions WHERE hash = ?", (digest,)).rowcount
            bodies = self._drop_orphans(db)
        return {"versions": gone, "bodies": bodies, "notes": sorted(paths)}

    def prune(self, keep_versions: int, older_than_ms: int | None) -> dict[str, int]:
        """Retention: at most ``keep_versions`` per note (0 for no limit) and
        nothing saved before ``older_than_ms`` (None for no limit)."""
        gone = 0
        with self.tx() as db:
            if older_than_ms is not None:
                gone += db.execute("DELETE FROM versions WHERE ts < ?",
                                   (older_than_ms,)).rowcount
            if keep_versions > 0:
                crowded = db.execute(
                    "SELECT path FROM versions GROUP BY path HAVING COUNT(*) > ?",
                    (keep_versions,)).fetchall()
                for row in crowded:
                    gone += db.execute(
                        "DELETE FROM versions WHERE path = ? AND version NOT IN ("
                        " SELECT version FROM versions WHERE path = ?"
                        " ORDER BY ts DESC, version DESC LIMIT ?)",
                        (row["path"], row["path"], keep_versions)).rowcount
            bodies = self._drop_orphans(db)
        return {"versions": gone, "bodies": bodies}

    # --- the timeline ---------------------------------------------------------

    def log(self, rows: list[tuple[str, int, str, str, str | None, str | None, dict]]) -> None:
        """Add timeline rows: (id, ts, type, actor, fact_id, note, detail).
        Never a statement, a note's text or anything read from a source."""
        if not rows:
            return
        with self.tx() as db:
            db.executemany(
                "INSERT OR IGNORE INTO activity (id, ts, type, actor, fact_id, note, detail)"
                " VALUES (?,?,?,?,?,?,?)",
                [(*row[:6], json.dumps(row[6])) for row in rows])
            db.execute("DELETE FROM activity WHERE id NOT IN"
                       " (SELECT id FROM activity ORDER BY id DESC LIMIT ?)", (ACTIVITY_CAP,))
