"""Vital signs: heartbeats and job runs.

These are deliberately not on the Tape. The Tape records decisions and work;
"still alive" ticks would bury them.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from jimmyhq.ids import now_ms
from jimmyhq.tape.store import connect

ALIVE_WITHIN_MS = 30_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS heartbeat (
  name TEXT PRIMARY KEY, pid INTEGER NOT NULL, last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
  name TEXT PRIMARY KEY, last_run INTEGER NOT NULL, ok INTEGER NOT NULL, error TEXT
);
"""


class Metrics:
    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._conn = connect(path)
        self._conn.executescript(_SCHEMA)

    def beat(self, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO heartbeat (name, pid, last_seen) VALUES (?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET pid = excluded.pid,"
                " last_seen = excluded.last_seen",
                (name, os.getpid(), now_ms()),
            )

    def forget(self, name: str) -> None:
        """Stop listing something that will not beat again, such as a removed node."""
        with self._lock:
            self._conn.execute("DELETE FROM heartbeat WHERE name = ?", (name,))

    def last_seen(self, name: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seen FROM heartbeat WHERE name = ?", (name,)).fetchone()
        return row["last_seen"] if row else None

    def processes(self, expected: tuple[str, ...]) -> list[dict]:
        with self._lock:
            rows = {
                r["name"]: r["last_seen"]
                for r in self._conn.execute("SELECT name, last_seen FROM heartbeat")
            }
        now = now_ms()
        names = list(expected) + sorted(n for n in rows if n not in expected)
        return [
            {
                "name": n,
                "ok": n in rows and now - rows[n] <= ALIVE_WITHIN_MS,
                "last_seen_ms": rows.get(n),
            }
            for n in names
        ]

    def job_last_run(self, name: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_run FROM job_runs WHERE name = ?", (name,)
            ).fetchone()
        return row["last_run"] if row else None

    def job_ran(self, name: str, *, ok: bool, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_runs (name, last_run, ok, error) VALUES (?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET last_run = excluded.last_run,"
                " ok = excluded.ok, error = excluded.error",
                (name, now_ms(), int(ok), error),
            )

    def jobs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, last_run, ok, error FROM job_runs ORDER BY name"
            ).fetchall()
        return [dict(r) | {"ok": bool(r["ok"])} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
