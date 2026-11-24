"""The village event log: every tick, action, need change and feeling of every agent.

It is append-only and lives in its own database, ``village.db``, not on the Tape. A
single agent writes several events a beat, most of them only of interest to the body
itself; the Tape is for what the owner would call a decision or an action. The file's
tables and their version are declared in ``dbfile.py``.

Stored times are unix milliseconds in UTC. The world works in float seconds, so the
conversion happens here, at the edge, and nowhere else.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jimmyhq import storeversion
from jimmyhq.tape.store import connect
from jimmyhq.village.dbfile import FILE_NAME as FILE_NAME  # still imported from here
from jimmyhq.village.dbfile import STORE
from jimmyhq.village.world.events import Event

_PREFIX = "evt-"


def to_ms(ts: float) -> int:
    return round(ts * 1000)


def from_ms(ts_ms: int) -> float:
    return ts_ms / 1000


def _row_id(event_id: str) -> int | None:
    if not isinstance(event_id, str) or not event_id.startswith(_PREFIX):
        return None
    number = event_id[len(_PREFIX):]
    return int(number) if number.isascii() and number.isdigit() else None


def _event(row: sqlite3.Row) -> Event:
    return Event(id=f"{_PREFIX}{row['id']}", ts=from_ms(row["ts_ms"]), kind=row["kind"],
                 agent=row["agent"], data=json.loads(row["data"]))


class SqliteLog:
    """The durable event log. It has the same methods as the world's ``EventLog``."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = connect(self.path)
        with self._lock:
            storeversion.prepare(self._conn, STORE)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- write ----------------------------------------------------------------

    def append(self, kind: str, agent: str | None, ts: float, **data: Any) -> Event:
        """Add one event and return it, as it will read back."""
        ts_ms = to_ms(ts)
        text = json.dumps(data, default=str, ensure_ascii=False, allow_nan=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts_ms, agent, kind, data) VALUES (?, ?, ?, ?)",
                (ts_ms, agent, kind, text),
            )
            row_id = cur.lastrowid
        return Event(id=f"{_PREFIX}{row_id}", ts=from_ms(ts_ms), kind=kind, agent=agent,
                     data=json.loads(text))

    # --- read -----------------------------------------------------------------

    def get(self, event_id: str) -> Event | None:
        row_id = _row_id(event_id)
        if row_id is None:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id = ?", (row_id,)).fetchone()
        return _event(row) if row else None

    @staticmethod
    def _where(agent: str | None, kinds: Iterable[str] | None, since: float | None,
               until: float | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent is not None:
            clauses.append("agent = ?")
            params.append(agent)
        ks = list(dict.fromkeys(kinds)) if kinds else []
        if ks:
            clauses.append(f"kind IN ({', '.join('?' * len(ks))})")
            params.extend(ks)
        if since is not None:
            clauses.append("ts_ms >= ?")
            params.append(to_ms(since))
        if until is not None:
            clauses.append("ts_ms <= ?")
            params.append(to_ms(until))
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def query(self, agent: str | None = None, kinds: Iterable[str] | None = None,
              since: float | None = None, until: float | None = None,
              limit: int | None = None) -> list[Event]:
        """Matching events, oldest first. ``since`` and ``until`` are inclusive, in
        seconds. With ``limit``, the newest that many."""
        where, params = self._where(agent, kinds, since, until)
        sql = f"SELECT * FROM events{where} ORDER BY id"
        if limit is not None:
            sql = f"SELECT * FROM ({sql} DESC LIMIT ?) ORDER BY id"
            params.append(max(0, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_event(r) for r in rows]

    def last(self, agent: str | None = None,
             kinds: Iterable[str] | None = None) -> Event | None:
        """The newest matching event."""
        where, params = self._where(agent, kinds, None, None)
        sql = f"SELECT * FROM events{where} ORDER BY id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return _event(row) if row else None

    def count(self, agent: str | None = None, kinds: Iterable[str] | None = None,
              since: float | None = None, until: float | None = None) -> int:
        where, params = self._where(agent, kinds, since, until)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM events{where}", params).fetchone()
        return int(row[0])

    def counts_by_kind(self, agent: str | None = None, since: float | None = None,
                       until: float | None = None) -> dict[str, int]:
        """How many events of each kind, for a summary screen or a report."""
        where, params = self._where(agent, None, since, until)
        sql = f"SELECT kind, COUNT(*) FROM events{where} GROUP BY kind"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return {r[0]: int(r[1]) for r in rows}
