"""Small shared facts that are not worth an event: health and model lists.

Whether a provider answered a minute ago, and which models it listed, are
vital signs and caches, not decisions, so they stay off the Tape. They live in
a SQLite file in the data folder so the web and worker processes agree.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from jimmyhq.tape.store import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS health (
  provider_id TEXT PRIMARY KEY, ok INTEGER NOT NULL, checked_ms INTEGER NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS model_lists (
  provider_id TEXT PRIMARY KEY, fetched_ms INTEGER NOT NULL, models TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._conn = connect(path)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def set_health(self, provider_id: str, ok: bool, checked_ms: int,
                   error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO health (provider_id, ok, checked_ms, error) VALUES (?,?,?,?)"
                " ON CONFLICT(provider_id) DO UPDATE SET ok = excluded.ok,"
                " checked_ms = excluded.checked_ms, error = excluded.error",
                (provider_id, int(ok), checked_ms, error),
            )

    def health(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT provider_id, ok, checked_ms, error FROM health").fetchall()
        return {r["provider_id"]: {"ok": bool(r["ok"]), "checked_ms": r["checked_ms"],
                                   "error": r["error"]} for r in rows}

    def set_models(self, provider_id: str, models: list[str], fetched_ms: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO model_lists (provider_id, fetched_ms, models) VALUES (?,?,?)"
                " ON CONFLICT(provider_id) DO UPDATE SET fetched_ms = excluded.fetched_ms,"
                " models = excluded.models",
                (provider_id, fetched_ms, json.dumps(models)),
            )

    def models(self, provider_id: str) -> tuple[list[str], int] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched_ms, models FROM model_lists WHERE provider_id = ?",
                (provider_id,)).fetchone()
        return (json.loads(row["models"]), row["fetched_ms"]) if row else None

    def forget(self, provider_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM health WHERE provider_id = ?", (provider_id,))
            self._conn.execute("DELETE FROM model_lists WHERE provider_id = ?", (provider_id,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
