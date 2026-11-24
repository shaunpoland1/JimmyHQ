"""Packages' own records, in the app's private database (``packages.db`` in its data folder).

Tracking numbers, labels, statuses and events live here and nowhere else: never on the
owner's record, never in a Bench card. The database is a private one: a deleted package
is overwritten in the file, not only unlinked. Every time is unix milliseconds, UTC.

A package's ``status`` is one of ``packages.status.STATUSES``. ``status_by`` says who set
it (``tracker`` or ``hand``). ``registered_with`` is the tracker that registered the number
(``registered_ms`` when); ``charged_ms`` is when registering used a place of quota, set at
most once for a number. ``refused`` is why registering was refused (kept so a scheduled
check does not ask again; Refresh now does); ``note`` is the last thing worth saying about
it. ``tell_status`` is a change waiting to be told, ``card_id`` the Bench card for it.
``archived_ms`` is when it stopped being checked.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS package (
  id INTEGER PRIMARY KEY, label TEXT NOT NULL, number TEXT NOT NULL,
  carrier TEXT NOT NULL DEFAULT '', carrier_found TEXT NOT NULL DEFAULT '',
  carrier_guessed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'waiting', status_by TEXT NOT NULL DEFAULT 'tracker',
  event TEXT NOT NULL DEFAULT '', event_ms INTEGER, checked_ms INTEGER,
  registered_with TEXT NOT NULL DEFAULT '', registered_ms INTEGER, charged_ms INTEGER,
  refused TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  delivered_ms INTEGER, archived_ms INTEGER, tell_status TEXT NOT NULL DEFAULT '',
  card_id TEXT, created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS carrier (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, created INTEGER NOT NULL);
"""


class Store:
    def __init__(self, private: Any, clock: Any):
        self.private = private
        self.conn: sqlite3.Connection = private.conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self.now = clock

    def _migrate(self) -> None:
        """Bring a database made by an older Packages up to ``SCHEMA_VERSION``: each step
        takes it from the version before. Never edit a step that has shipped."""
        have = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if have > SCHEMA_VERSION:
            raise RuntimeError("This database was made by a newer Packages. Update Packages "
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

    def insert(self, table: str, values: dict[str, Any]) -> int:
        keys = list(values)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",  # noqa: S608
            [values[k] for k in keys])
        self.conn.commit()
        return int(cur.lastrowid)

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, **values: Any) -> None:
        for key, value in values.items():
            self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) "
                              "DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        self.conn.commit()

    # --- packages -------------------------------------------------------------------------

    def package(self, package_id: int | None) -> dict[str, Any] | None:
        if package_id is None:
            return None
        return self.one("SELECT * FROM package WHERE id=?", (package_id,))

    def packages(self, limit: int = 5000) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM package ORDER BY id LIMIT ?", (limit,))

    def by_number(self, number: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM package WHERE upper(number)=upper(?) LIMIT 1", (number,))

    def set(self, package_id: int, **values: Any) -> dict[str, Any]:
        """Change one package's columns, stamping the time."""
        values["updated"] = self.now()
        sets = ", ".join(f"{k}=?" for k in values)
        self.conn.execute(f"UPDATE package SET {sets} WHERE id=?",  # noqa: S608
                          [*values.values(), package_id])
        self.conn.commit()
        return self.package(package_id) or {}

    def remove(self, package_id: int) -> None:
        """Delete a package for good: its row is overwritten in the file."""
        self.private.purge("package", "id=?", (package_id,))

    # --- carriers the owner saved ----------------------------------------------------------

    def carriers(self) -> list[dict[str, Any]]:
        return self.all("SELECT code, name FROM carrier ORDER BY name COLLATE NOCASE, code")

    def save_carrier(self, code: str, name: str) -> None:
        self.conn.execute("INSERT INTO carrier (code, name, created) VALUES (?, ?, ?) ON "
                          "CONFLICT(code) DO UPDATE SET name=excluded.name",
                          (code, name, self.now()))
        self.conn.commit()

    def forget_carrier(self, code: str) -> bool:
        return self.private.purge("carrier", "code=?", (code,)) > 0

    def carrier_name(self, code: str) -> str:
        row = self.one("SELECT name FROM carrier WHERE code=?", (code,))
        return row["name"] if row else ""
