"""What the plug points remember between ticks, in ``apps.db`` beside the inbox: each
job's last run, how far each app has read the Tape, the crash loops already told about,
and what an app's program has emitted for its open pages.

Working state only. Nothing here is the record: what the owner must be able to see later
is printed on the Tape by whoever acts on it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.apps import dbfile, quotas

if TYPE_CHECKING:
    from jimmyhq.core import Core

EMITS_KEPT = 100  # for each app: its open pages read from the newest of these
EMITS_KEPT_MS = 10 * 60 * 1000



class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        storeversion.prepare(self._db, dbfile.STORE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- jobs

    def job(self, app_id: str, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM plug_jobs WHERE app_id=? AND name=?",
                                   (app_id, name)).fetchone()
        return None if row is None else dict(row)

    def jobs(self, app_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM plug_jobs WHERE app_id=? ORDER BY name",
                                    (app_id,)).fetchall()
        return [dict(r) for r in rows]

    def job_queued(self, app_id: str, name: str, delivery: str, now: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO plug_jobs (app_id, name, queued, delivery) VALUES (?,?,?,?) "
                "ON CONFLICT (app_id, name) DO UPDATE SET queued=excluded.queued, "
                "delivery=excluded.delivery", (app_id, name, now, delivery))

    def job_finished(self, app_id: str, name: str, ok: bool, error: str | None,
                     now: int) -> None:
        with self._lock:
            self._db.execute("UPDATE plug_jobs SET delivery=NULL, finished=?, ok=?, error=? "
                             "WHERE app_id=? AND name=?",
                             (now, int(ok), error, app_id, name))

    # --- how far an app has read the Tape

    def cursor(self, app_id: str, name: str) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT seq FROM plug_cursors WHERE app_id=? AND name=?",
                                   (app_id, name)).fetchone()
        return None if row is None else int(row["seq"])

    def set_cursor(self, app_id: str, name: str, seq: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO plug_cursors (app_id, name, seq) VALUES (?,?,?) "
                             "ON CONFLICT (app_id, name) DO UPDATE SET seq=excluded.seq",
                             (app_id, name, seq))

    # --- what was told once

    def tell_once(self, app_id: str, what: str, now: int) -> bool:
        """True the first time; False when this was told already."""
        with self._lock:
            done = self._db.execute("INSERT OR IGNORE INTO plug_told (app_id, what, at) "
                                    "VALUES (?,?,?)", (app_id, what, now))
            return done.rowcount == 1

    # --- what a program emitted for its pages

    def emit(self, app_id: str, name: str, data: Any, now: int) -> int:
        with self._lock:
            seq = self._db.execute("INSERT INTO plug_emits (app_id, name, data, at) VALUES "
                                   "(?,?,?,?)", (app_id, name, json.dumps(data), now)).lastrowid
            self._db.execute(
                "DELETE FROM plug_emits WHERE app_id=? AND (at < ? OR seq <= ?)",
                (app_id, now - EMITS_KEPT_MS, (seq or 0) - EMITS_KEPT))
            return int(seq or 0)

    def emitted(self, app_id: str, after: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT seq, name, data, at FROM plug_emits WHERE "
                                    "app_id=? AND seq>? ORDER BY seq LIMIT ?",
                                    (app_id, after, limit)).fetchall()
        return [{"seq": r["seq"], "name": r["name"], "data": json.loads(r["data"]),
                 "at": r["at"]} for r in rows]

    def last_emit(self, app_id: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(seq) AS seq FROM plug_emits WHERE app_id=?",
                                   (app_id,)).fetchone()
        return int(row["seq"] or 0)

    def forget(self, app_id: str) -> None:
        """An app was removed: its jobs and what it emitted go. Its Tape cursors stay, so
        an app installed again under the same id is not handed old decisions."""
        with self._lock:
            for table in ("plug_jobs", "plug_emits", "plug_told"):
                self._db.execute(f"DELETE FROM {table} WHERE app_id=?", (app_id,))  # noqa: S608


def state(core: Core) -> State:
    return core.service("apps.plugs", lambda c: State(quotas.state_db(c)))
