"""Imagery's own records, in the app's private database (``imagery.db`` in its data folder).

Descriptions, pictures' details, runs and the day's count live here and nowhere else: never
on the owner's record. The picture files are in the app's ``uploads`` folder. The database
is one that deletes for good: a deleted picture's row is overwritten in the file, not only
unlinked. Every time is unix milliseconds, UTC.

``picture.kind`` is ``made`` (an image service drew it, or Practice did) or ``given`` (the
owner gave it as a picture to start from). ``run.state`` is ``queued``, ``working``,
``done`` or ``failed``. An ``attempt`` is one request sent (or about to be sent) to an
outside service: the daily limit counts them.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS picture (
  id INTEGER PRIMARY KEY, ref TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'made',
  prompt TEXT NOT NULL DEFAULT '', style TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  aspect TEXT NOT NULL DEFAULT '', mime TEXT NOT NULL, bytes INTEGER NOT NULL,
  width INTEGER, height INTEGER, starred INTEGER NOT NULL DEFAULT 0,
  said TEXT NOT NULL DEFAULT '', run_id INTEGER, created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS picture_created ON picture (created DESC, id DESC);
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY, prompt TEXT NOT NULL, style TEXT NOT NULL DEFAULT '',
  full_prompt TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
  aspect TEXT NOT NULL, want INTEGER NOT NULL, made INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'queued', said TEXT NOT NULL DEFAULT '', problem TEXT NOT NULL
  DEFAULT '', reference_id INTEGER, seen INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS attempt (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, provider TEXT NOT NULL, run_id INTEGER);
CREATE INDEX IF NOT EXISTS attempt_ts ON attempt (ts);
"""


class Store:
    def __init__(self, private: Any, clock: Any):
        self.private = private
        self.conn: sqlite3.Connection = private.conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self.now = clock

    def _migrate(self) -> None:
        """Bring a database made by an older Imagery up to ``SCHEMA_VERSION``: each step
        takes it from the version before. Never edit a step that has shipped."""
        have = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if have > SCHEMA_VERSION:
            raise RuntimeError("This database was made by a newer Imagery. Update Imagery "
                               "instead of opening it with an older one.")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self.private.close()

    # --- plain helpers --------------------------------------------------------------------

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def number(self, sql: str, params: Iterable[Any] = ()) -> int:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return int(row[0] or 0) if row else 0

    def insert(self, table: str, values: dict[str, Any]) -> int:
        keys = list(values)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",  # noqa: S608
            [values[k] for k in keys])
        self.conn.commit()
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, **values: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",  # noqa: S608
                          [*values.values(), row_id])
        self.conn.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, **values: Any) -> None:
        for key, value in values.items():
            self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) "
                              "DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        self.conn.commit()

    # --- pictures -------------------------------------------------------------------------

    def picture(self, picture_id: int | None) -> dict[str, Any] | None:
        if picture_id is None:
            return None
        return self.one("SELECT * FROM picture WHERE id=?", (picture_id,))

    def forget_picture(self, picture_id: int) -> None:
        """Delete a picture's row for good: it is overwritten in the file."""
        self.private.purge("picture", "id=?", (picture_id,))

    def forget_run(self, run_id: int) -> None:
        self.private.purge("run", "id=?", (run_id,))

    def forget_attempt(self, attempt_id: int) -> None:
        self.private.purge("attempt", "id=?", (attempt_id,))

    def forget_attempts_before(self, ms: int) -> None:
        self.private.purge("attempt", "ts<?", (ms,))

    def run(self, run_id: int | None) -> dict[str, Any] | None:
        if run_id is None:
            return None
        return self.one("SELECT * FROM run WHERE id=?", (run_id,))
