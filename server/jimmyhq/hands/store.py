"""``hands.db``: what the hands module keeps off the Tape.

- ``runs`` is the exactly-once ledger. A row is written ``started`` before a provider is
  called and settled after. A key can be used once, ever. A row still ``started`` long
  after its call should have ended means the process died in between: it becomes
  ``unknown`` and is never tried again.
- ``pending`` holds the exact arguments of a request that waits for the owner. They can
  hold a message body, so they are here (a real delete) and not on the Tape.
- ``kept`` holds shaped copies of readings for the repeat window.
- ``account_refs`` holds each account's reference at its provider, which may be an
  address.
- ``actions``, ``health`` and ``connects`` are caches and work in progress.
- ``account_addresses`` holds an agent inbox's own address, for the owner's screen.
- ``mail_sent_to`` holds a digest (never the address) of each address an agent has
  sent mail to from its inbox; ``mail_cursors`` where the inbox was last read to, and
  ``mail_cards`` when a card about failed mail was last filed.

The web and worker processes both open this file; SQLite settles who writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from jimmyhq import storeversion
from jimmyhq.tape.store import connect

FILE_NAME = "hands.db"
STARTED, DONE, FAILED, UNKNOWN = "started", "done", "failed", "unknown"
PENDING, PERFORMED, DECLINED = "pending", "performed", "declined"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, agent TEXT NOT NULL, account TEXT NOT NULL,
  provider_id TEXT NOT NULL, action TEXT NOT NULL, tool TEXT NOT NULL, effect TEXT NOT NULL,
  level TEXT NOT NULL, status TEXT NOT NULL, started_ms INTEGER NOT NULL, done_ms INTEGER,
  ref TEXT, error TEXT, error_kind TEXT, card_id TEXT, unknown_card TEXT,
  receipt TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS runs_agent ON runs (agent, account, started_ms);
CREATE INDEX IF NOT EXISTS runs_provider ON runs (provider_id, started_ms);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status, started_ms);
CREATE TABLE IF NOT EXISTS pending (
  fingerprint TEXT PRIMARY KEY, request_id TEXT NOT NULL, agent TEXT NOT NULL,
  account TEXT NOT NULL, action TEXT NOT NULL, tool TEXT NOT NULL, effect TEXT NOT NULL,
  version TEXT NOT NULL, rule_hash TEXT NOT NULL, arguments TEXT, why TEXT NOT NULL,
  created_ms INTEGER NOT NULL, status TEXT NOT NULL, card_id TEXT, event_id TEXT
);
CREATE INDEX IF NOT EXISTS pending_agent ON pending (agent, created_ms);
CREATE TABLE IF NOT EXISTS kept (
  name TEXT PRIMARY KEY, agent TEXT NOT NULL, account TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_refs (
  account TEXT PRIMARY KEY, provider_id TEXT NOT NULL, ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connects (
  account TEXT PRIMARY KEY, provider_id TEXT NOT NULL, created_ms INTEGER NOT NULL,
  details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
  account TEXT PRIMARY KEY, fetched_ms INTEGER NOT NULL, actions TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health (
  provider_id TEXT PRIMARY KEY, ok INTEGER NOT NULL, checked_ms INTEGER NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cards (
  card_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, what TEXT NOT NULL, agent TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cards_run ON cards (run_id);
CREATE TABLE IF NOT EXISTS account_addresses (
  account TEXT PRIMARY KEY, address TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_sent_to (
  account TEXT NOT NULL, agent TEXT NOT NULL, digest TEXT NOT NULL, first_ms INTEGER NOT NULL,
  PRIMARY KEY (account, agent, digest)
);
CREATE TABLE IF NOT EXISTS mail_cursors (account TEXT PRIMARY KEY, cursor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail_cards (
  card_id TEXT PRIMARY KEY, account TEXT NOT NULL, ts_ms INTEGER NOT NULL
);
"""

STORE = storeversion.declare("hands", _SCHEMA, files=(FILE_NAME,))


class KeyUsed(Exception):  # noqa: N818 - an answer, not a fault
    """This run key was used before. ``run`` is the row that holds it."""

    def __init__(self, run: dict[str, Any]):
        super().__init__(run["key"])
        self.run = run


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for field in ("receipt", "details"):
        if field in out:
            out[field] = json.loads(out[field])
    if out.get("arguments") is not None and "fingerprint" in out:
        out["arguments"] = json.loads(out["arguments"])
    return out


class HandsStore:
    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._conn = connect(path)
        with self._lock:
            storeversion.prepare(self._conn, STORE)

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [_row(r) for r in self._conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._conn.execute(sql, args).fetchone())

    def _do(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, args).rowcount

    # --- the run ledger -------------------------------------------------------

    def start_run(self, *, run_id: str, key: str, agent: str, account: str, provider_id: str,
                  action: str, tool: str, effect: str, level: str, now: int,
                  receipt: dict[str, Any], card_id: str | None = None) -> None:
        """Record that a run is about to leave. Raises ``KeyUsed`` if this key ever
        started before, whatever became of it."""
        try:
            self._do(
                "INSERT INTO runs (id, key, agent, account, provider_id, action, tool, effect,"
                " level, status, started_ms, card_id, receipt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, key, agent, account, provider_id, action, tool, effect, level, STARTED,
                 now, card_id, json.dumps(receipt)))
        except sqlite3.IntegrityError:
            raise KeyUsed(self.run_by_key(key)) from None

    def settle_run(self, run_id: str, status: str, *, now: int, ref: str | None = None,
                   error: str | None = None, error_kind: str | None = None) -> bool:
        """Close a started run. False if it was already closed (by a sweep, say): the
        first answer stands."""
        return self._do(
            "UPDATE runs SET status = ?, done_ms = ?, ref = ?, error = ?, error_kind = ?"
            " WHERE id = ? AND status = ?",
            (status, now, ref, error, error_kind, run_id, STARTED)) == 1

    def run(self, run_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM runs WHERE id = ?", (run_id,))

    def run_by_key(self, key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM runs WHERE key = ?", (key,))

    def runs(self, *, agent: str | None = None, status: str | None = None,
             limit: int = 100) -> list[dict[str, Any]]:
        where, args = [], []
        for column, value in (("agent", agent), ("status", status)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "")
        return self._all(sql + " ORDER BY started_ms DESC, id DESC LIMIT ?", (*args, limit))

    def stale_runs(self, before_ms: int) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM runs WHERE status = ? AND started_ms < ?"
                         " ORDER BY started_ms", (STARTED, before_ms))

    def unknown_runs(self, *, carded: bool | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs WHERE status = ?"
        if carded is not None:
            sql += " AND unknown_card IS " + ("NOT NULL" if carded else "NULL")
        return self._all(sql + " ORDER BY started_ms", (UNKNOWN,))

    def mark_carded(self, run_id: str, card_id: str) -> None:
        self._do("UPDATE runs SET unknown_card = ? WHERE id = ?", (card_id, run_id))

    def claim_unknown(self, run_id: str, mark: str) -> bool:
        """Take the job of filing the card about an unknown run. True for one caller
        only, across processes; the card's id replaces ``mark`` once it is filed."""
        return self._do("UPDATE runs SET unknown_card = ? WHERE id = ? AND status = ?"
                        " AND unknown_card IS NULL", (mark, run_id, UNKNOWN)) == 1

    def release_unknown(self, run_id: str, mark: str) -> None:
        """Give a claim back when the card could not be filed."""
        self._do("UPDATE runs SET unknown_card = NULL WHERE id = ? AND unknown_card = ?",
                 (run_id, mark))

    # --- cards filed about a run, and the executor's place on the Tape ------------

    def note_card(self, card_id: str, run_id: str, what: str, agent: str) -> None:
        self._do("INSERT OR IGNORE INTO cards (card_id, run_id, what, agent) VALUES (?,?,?,?)",
                 (card_id, run_id, what, agent))

    def card(self, card_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM cards WHERE card_id = ?", (card_id,))

    def cards_of(self, run_ids: list[str], what: str) -> dict[str, str]:
        """``run id -> card id`` for the cards of one sort filed about these runs."""
        if not run_ids:
            return {}
        marks = ",".join("?" * len(run_ids))
        rows = self._all(f"SELECT run_id, card_id FROM cards WHERE what = ?"  # noqa: S608
                         f" AND run_id IN ({marks})", (what, *run_ids))
        return {r["run_id"]: r["card_id"] for r in rows}

    def cursor(self, name: str) -> int:
        row = self._one("SELECT seq FROM cursors WHERE name = ?", (name,))
        return int(row["seq"]) if row else 0

    def set_cursor(self, name: str, seq: int) -> None:
        """Move a cursor forward. It never moves back."""
        self._do("INSERT INTO cursors (name, seq) VALUES (?, ?) ON CONFLICT(name) DO UPDATE"
                 " SET seq = excluded.seq WHERE excluded.seq > cursors.seq", (name, int(seq)))

    def rewind_cursor(self, name: str, seq: int) -> None:
        """Put a cursor back to ``seq``. Only for a cursor past the end of the Tape, which
        happens when the Tape was put back from a backup made before this file."""
        self._do("UPDATE cursors SET seq = ? WHERE name = ? AND seq > ?",
                 (int(seq), name, int(seq)))

    def count_runs(self, *, since_ms: int, agent: str | None = None, account: str | None = None,
                   provider_id: str | None = None, action: str | None = None) -> int:
        where, args = ["started_ms >= ?"], [since_ms]
        for column, value in (("agent", agent), ("account", account),
                              ("provider_id", provider_id), ("action", action)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        row = self._one("SELECT COUNT(*) AS n FROM runs WHERE " + " AND ".join(where),
                        tuple(args))
        return int(row["n"])

    # --- requests that wait for the owner ---------------------------------------

    def add_pending(self, *, fingerprint: str, request_id: str, agent: str, account: str,
                    action: str, tool: str, effect: str, version: str, rule_hash: str,
                    arguments: dict[str, Any], why: str, now: int) -> None:
        self._do(
            "INSERT INTO pending (fingerprint, request_id, agent, account, action, tool, effect,"
            " version, rule_hash, arguments, why, created_ms, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fingerprint, request_id, agent, account, action, tool, effect, version, rule_hash,
             json.dumps(arguments, sort_keys=True, ensure_ascii=False), why, now, PENDING))

    def pending(self, fingerprint: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM pending WHERE fingerprint = ?", (fingerprint,))

    def pending_by_card(self, card_id: str) -> dict[str, Any] | None:
        """The request a Bench card shows, whatever became of it."""
        return self._one("SELECT * FROM pending WHERE card_id = ?", (card_id,))

    def pending_list(self, *, agent: str | None = None) -> list[dict[str, Any]]:
        if agent is None:
            return self._all("SELECT * FROM pending WHERE status = ? ORDER BY created_ms",
                             (PENDING,))
        return self._all("SELECT * FROM pending WHERE status = ? AND agent = ?"
                         " ORDER BY created_ms", (PENDING, agent))

    def note_pending(self, fingerprint: str, *, card_id: str | None = None,
                     event_id: str | None = None) -> None:
        self._do("UPDATE pending SET card_id = COALESCE(?, card_id),"
                 " event_id = COALESCE(?, event_id) WHERE fingerprint = ?",
                 (card_id, event_id, fingerprint))

    def close_pending(self, fingerprint: str, status: str) -> bool:
        """Close a request and erase its arguments. False if it was not waiting."""
        return self._do("UPDATE pending SET status = ?, arguments = NULL"
                        " WHERE fingerprint = ? AND status = ?",
                        (status, fingerprint, PENDING)) == 1

    def count_pending(self, agent: str, since_ms: int) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM pending WHERE agent = ? AND created_ms >= ?",
                        (agent, since_ms))
        return int(row["n"])

    # --- kept readings ----------------------------------------------------------

    def keep(self, name: str, agent: str, account: str, body: str, now: int,
             older_than_ms: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM kept WHERE ts_ms < ?", (older_than_ms,))
            self._conn.execute(
                "INSERT INTO kept (name, agent, account, ts_ms, body) VALUES (?,?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET ts_ms = excluded.ts_ms, body = excluded.body",
                (name, agent, account, now, body))

    def kept(self, name: str, since_ms: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM kept WHERE name = ? AND ts_ms >= ?", (name, since_ms))

    # --- accounts ---------------------------------------------------------------

    def set_ref(self, account: str, provider_id: str, ref: str) -> None:
        self._do("INSERT INTO account_refs (account, provider_id, ref) VALUES (?,?,?)"
                 " ON CONFLICT(account) DO UPDATE SET provider_id = excluded.provider_id,"
                 " ref = excluded.ref", (account, provider_id, ref))

    def ref(self, account: str) -> str | None:
        row = self._one("SELECT ref FROM account_refs WHERE account = ?", (account,))
        return row["ref"] if row else None

    def refs(self, provider_id: str) -> set[str]:
        return {r["ref"] for r in self._all(
            "SELECT ref FROM account_refs WHERE provider_id = ?", (provider_id,))}

    def set_connect(self, account: str, provider_id: str, details: dict[str, Any],
                    now: int) -> None:
        self._do("INSERT INTO connects (account, provider_id, created_ms, details)"
                 " VALUES (?,?,?,?) ON CONFLICT(account) DO UPDATE SET"
                 " provider_id = excluded.provider_id, created_ms = excluded.created_ms,"
                 " details = excluded.details", (account, provider_id, now, json.dumps(details)))

    def connect_of(self, account: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM connects WHERE account = ?", (account,))

    def connects(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM connects ORDER BY created_ms")

    def drop_connect(self, account: str) -> None:
        self._do("DELETE FROM connects WHERE account = ?", (account,))

    def forget_account(self, account: str) -> None:
        """Everything private about an account, gone for good. The ledger keeps its
        rows: they hold no values."""
        with self._lock:
            for table in ("account_refs", "connects", "actions", "kept", "pending",
                          "account_addresses", "mail_sent_to", "mail_cursors"):
                self._conn.execute(f"DELETE FROM {table} WHERE account = ?", (account,))  # noqa: S608

    def set_address(self, account: str, address: str | None) -> None:
        if address:
            self._do("INSERT INTO account_addresses (account, address) VALUES (?,?)"
                     " ON CONFLICT(account) DO UPDATE SET address = excluded.address",
                     (account, address))

    def address(self, account: str) -> str | None:
        row = self._one("SELECT address FROM account_addresses WHERE account = ?", (account,))
        return row["address"] if row else None

    # --- mail ---------------------------------------------------------------------

    def learn_sent(self, account: str, agent: str, digests: list[str], now: int) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO mail_sent_to (account, agent, digest, first_ms)"
                " VALUES (?,?,?,?)", [(account, agent, d, now) for d in digests])

    def sent_before(self, account: str, agent: str, digest: str) -> bool:
        return self._one("SELECT 1 AS x FROM mail_sent_to WHERE account = ? AND agent = ?"
                         " AND digest = ?", (account, agent, digest)) is not None

    def mail_cursor(self, account: str) -> str | None:
        row = self._one("SELECT cursor FROM mail_cursors WHERE account = ?", (account,))
        return row["cursor"] if row else None

    def set_mail_cursor(self, account: str, cursor: str | None) -> None:
        if cursor is not None:
            self._do("INSERT INTO mail_cursors (account, cursor) VALUES (?,?)"
                     " ON CONFLICT(account) DO UPDATE SET cursor = excluded.cursor",
                     (account, cursor))

    def note_mail_card(self, card_id: str, account: str, now: int) -> None:
        self._do("INSERT OR IGNORE INTO mail_cards (card_id, account, ts_ms) VALUES (?,?,?)",
                 (card_id, account, now))

    def mail_cards_since(self, since_ms: int) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM mail_cards WHERE ts_ms >= ?", (since_ms,))
        return int(row["n"]) if row else 0

    # --- caches -----------------------------------------------------------------

    def set_actions(self, account: str, actions: list[dict[str, Any]], now: int) -> None:
        self._do("INSERT INTO actions (account, fetched_ms, actions) VALUES (?,?,?)"
                 " ON CONFLICT(account) DO UPDATE SET fetched_ms = excluded.fetched_ms,"
                 " actions = excluded.actions", (account, now, json.dumps(actions)))

    def actions(self, account: str) -> tuple[list[dict[str, Any]], int] | None:
        with self._lock:
            row = self._conn.execute("SELECT fetched_ms, actions FROM actions WHERE account = ?",
                                     (account,)).fetchone()
        return (json.loads(row["actions"]), row["fetched_ms"]) if row else None

    def set_health(self, provider_id: str, ok: bool, now: int, error: str | None = None) -> None:
        self._do("INSERT INTO health (provider_id, ok, checked_ms, error) VALUES (?,?,?,?)"
                 " ON CONFLICT(provider_id) DO UPDATE SET ok = excluded.ok,"
                 " checked_ms = excluded.checked_ms, error = excluded.error",
                 (provider_id, int(ok), now, error))

    def health(self) -> dict[str, dict[str, Any]]:
        return {r["provider_id"]: {"ok": bool(r["ok"]), "checked_ms": r["checked_ms"],
                                   "error": r["error"]}
                for r in self._all("SELECT * FROM health")}

    def forget_provider(self, provider_id: str) -> None:
        self._do("DELETE FROM health WHERE provider_id = ?", (provider_id,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
