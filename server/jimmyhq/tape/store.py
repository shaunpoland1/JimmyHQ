"""The Tape: one append-only ledger, and the source of truth.

- One table in SQLite (WAL mode). Events are validated on the way in and
  upgraded on the way out. Nothing is updated or deleted, with one exception:
  ``Tape.erase``, which the owner asks for. It empties the payload of the
  events it is given and leaves everything else about them in place.
- Several processes may write (web, worker, apps). Each write is one short
  ``BEGIN IMMEDIATE`` transaction, so SQLite serialises them.
- Lineage is three columns (cause, root, subject) and a walk.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jimmyhq import storeversion
from jimmyhq.tape.registry import (
    Event,
    MissingUpcasterError,
    UnknownKindError,
    make_event,
    upcast,
)

_MAX_SEQ = 2**63 - 1  # SQLite integers are 64-bit


def _seq(value: int) -> int:
    return max(0, min(int(value), _MAX_SEQ))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tape (
  seq      INTEGER PRIMARY KEY AUTOINCREMENT,
  id       TEXT UNIQUE NOT NULL,
  ts       INTEGER NOT NULL,
  kind     TEXT NOT NULL,
  v        INTEGER NOT NULL,
  actor    TEXT NOT NULL,
  cause    TEXT,
  root     TEXT,
  subject  TEXT,
  payload  TEXT NOT NULL,
  erased   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tape_kind ON tape(kind, seq);
CREATE INDEX IF NOT EXISTS idx_tape_subject ON tape(subject, seq);
CREATE INDEX IF NOT EXISTS idx_tape_cause ON tape(cause);
CREATE INDEX IF NOT EXISTS idx_tape_root ON tape(root);
-- What an erase has still to finish. It is written in the erase's own
-- transaction, so a process that stops straight after still leaves the job
-- for the next one: ('settle', '') while the write-ahead log may hold an
-- earlier image of an erased page, and ('vault', ref) for each Vault file
-- the erased events were the last to name.
CREATE TABLE IF NOT EXISTS tape_pending (
  what   TEXT NOT NULL,
  ref    TEXT NOT NULL,
  since  INTEGER NOT NULL,
  PRIMARY KEY (what, ref)
);
"""



def _before_versions(conn: sqlite3.Connection) -> None:
    """A Tape from before versions existed: one from before erasure gains its column."""
    if "erased" not in {r[1] for r in conn.execute("PRAGMA table_info(tape)")}:
        try:
            conn.execute("ALTER TABLE tape ADD COLUMN erased INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc):  # another process added it first
                raise


STORE = storeversion.declare("tape", _SCHEMA, files=("tape.db",),
                             unversioned=_before_versions)

_COLUMNS = "seq, id, ts, kind, v, actor, cause, root, subject, payload, erased"

ERASERS = ("owner", "system")  # who may erase: the owner, or a job doing what the owner set up
ERASED_KIND = "tape.erased"
_TOMBSTONE = "{}"  # all that is left of an erased payload
_VAULT_REF = re.compile(r"sha256:[0-9a-f]{64}")
_CHUNK = 500  # ids per statement, under SQLite's limit on bound values
# A Vault file is not removed until it has lain untouched this long. Storing
# a file (``Vault.put``) and printing the event that names it are two steps;
# ``put`` touches the file, so one that a writer is about to name is left
# alone until the event is there to be seen.
ORPHAN_QUIET_MS = 60_000


class TapeConflict(RuntimeError):
    """Another writer changed the subject first. Nothing was written."""


class TapeRefused(PermissionError):
    """This actor may not erase. Nothing was changed."""


@dataclass(frozen=True)
class Erasure:
    """What an erase did."""

    events: int  # how many events lost their payload
    kinds: dict[str, int]  # the same, by kind
    receipt: Event | None  # the ``tape.erased`` event; None when nothing was erased
    # Vault references the erased payloads held that no event mentions any
    # more. They are queued durably; ``Tape.release_orphans`` removes the files.
    orphans: tuple[str, ...]
    # True when the write-ahead log was folded in and emptied afterwards, so no
    # earlier image of a page is left in it. False when a reader held it open:
    # that is remembered (``Tape.pending``) and ``Tape.settle`` finishes it.
    settled: bool


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection the way every JimmyHQ store does."""
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    # Switching a new database into WAL mode does not wait on the busy timeout,
    # so two processes opening a fresh data folder together must retry.
    deadline = time.monotonic() + 10
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class Tape:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = connect(self.path)
        with self._lock:
            # What an erase removes is overwritten with zeros in the file, not
            # only unlinked. It costs nothing on an append, which frees nothing.
            self._conn.execute("PRAGMA secure_delete=ON")
            storeversion.prepare(self._conn, STORE)

    # --- write ----------------------------------------------------------------

    def append(self, event: Event, *, expect_subject_seq: int | None = None) -> Event:
        """Append an already validated event. Returns it with its seq.

        With ``expect_subject_seq``, the event is written only if the newest
        event about its subject still has that seq (0 for "none yet"). The
        check and the write are one transaction, so of two processes acting on
        the same thing at the same moment exactly one wins and the other gets
        ``TapeConflict`` with nothing written.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._expect(event.subject, expect_subject_seq)
                seq = self._insert(event)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return event.model_copy(update={"seq": seq})

    def _expect(self, subject: str | None, expect_subject_seq: int | None) -> None:
        """Inside a write transaction: refuse if the subject has moved on."""
        if expect_subject_seq is None:
            return
        newest = self._conn.execute(
            "SELECT MAX(seq) AS m FROM tape WHERE subject = ?", (subject,)).fetchone()["m"]
        if int(newest or 0) != expect_subject_seq:
            raise TapeConflict(f"{subject} changed since seq {expect_subject_seq}")

    def _insert(self, event: Event) -> int:
        """Inside a write transaction: add one line. Returns its seq."""
        cur = self._conn.execute(
            "INSERT INTO tape (id, ts, kind, v, actor, cause, root, subject, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (event.id, event.ts, event.kind, event.v, event.actor, event.cause, event.root,
             event.subject, json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))))
        return int(cur.lastrowid or 0)

    def print(  # noqa: A003 - "print to the Tape" is the house verb
        self,
        kind: str,
        payload: dict,
        *,
        actor: str,
        cause: str | None = None,
        root: str | None = None,
        subject: str | None = None,
        expect_subject_seq: int | None = None,
    ) -> Event:
        """Build, validate and append. The everyday write path, and the only one."""
        if expect_subject_seq is not None and subject is None:
            raise ValueError("expect_subject_seq needs a subject")
        return self.append(
            make_event(kind, payload, actor=actor, cause=cause, root=root, subject=subject),
            expect_subject_seq=expect_subject_seq,
        )

    # --- erase ----------------------------------------------------------------

    def erase(
        self,
        event_ids: list[str] | tuple[str, ...],
        *,
        actor: str,
        reason: str,
        cause: str | None = None,
        subject: str | None = None,
        expect_subject_seq: int | None = None,
    ) -> Erasure:
        """Remove, for good, what these events say. The one exception to
        "nothing is rewritten", and only the owner (or the system, doing what
        the owner set up) may ask for it.

        Each event keeps its seq, id, time, kind, version, actor and lineage,
        so order and counts stay true; its payload becomes an empty tombstone
        and it reads back with ``erased`` true and ``readable`` false, which
        every lens skips. One ``tape.erased`` event is printed in the same
        transaction: how many events of which kinds, and ``reason`` (the name
        of the event kind that explains it, such as
        ``chat.conversation.deleted``). Never what they said. ``subject`` and
        ``cause`` are that event's own; ``expect_subject_seq`` works as in
        ``append``, so nothing is erased if the subject has moved on.

        The old bytes are overwritten with zeros in the database file
        (``secure_delete``), and afterwards the write-ahead log is folded in
        and emptied. Ids that are unknown or already erased are passed over,
        so erasing twice is harmless; with nothing to erase nothing is printed.
        A ``tape.erased`` event cannot itself be erased.

        Lenses that fold an erased kind rebuild themselves when they next
        read (see ``lens``). What this cannot reach: a copy of the data folder
        made earlier. Files in the Vault that the erased events were the last
        to name are queued, in the same transaction, for ``release_orphans``;
        so is the emptying of the log, in case this process stops before it,
        or a reader holds the log (``settled`` false). ``pending`` says what
        is still owed and ``finish`` does it.
        """
        if actor not in ERASERS:
            raise TapeRefused("Only the owner can erase from the Tape.")
        if expect_subject_seq is not None and subject is None:
            raise ValueError("expect_subject_seq needs a subject")
        ids = list(dict.fromkeys(event_ids))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._expect(subject, expect_subject_seq)
                rows: list[sqlite3.Row] = []
                for at in range(0, len(ids), _CHUNK):
                    chunk = ids[at:at + _CHUNK]
                    rows += self._conn.execute(
                        "SELECT seq, kind, payload FROM tape WHERE erased = 0 AND id IN"  # noqa: S608
                        f" ({','.join('?' * len(chunk))})", chunk).fetchall()
                if any(r["kind"] == ERASED_KIND for r in rows):
                    raise ValueError("The record of an erasure cannot itself be erased.")
                kinds: dict[str, int] = {}
                refs: set[str] = set()
                for row in rows:
                    kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
                    refs.update(_VAULT_REF.findall(row["payload"]))
                self._conn.executemany(
                    "UPDATE tape SET payload = ?, erased = 1 WHERE seq = ?",
                    [(_TOMBSTONE, r["seq"]) for r in rows])
                if refs:
                    # One pass over what is left: a file another event still
                    # names (the same words said elsewhere) is not an orphan.
                    for row in self._conn.execute(
                            "SELECT payload FROM tape WHERE instr(payload, 'sha256:') > 0"
                    ).fetchall():
                        refs.difference_update(_VAULT_REF.findall(row["payload"]))
                receipt = None
                if rows:
                    receipt = make_event(
                        ERASED_KIND,
                        {"reason": reason, "events": len(rows),
                         "kinds": dict(sorted(kinds.items()))},
                        actor=actor, cause=cause, subject=subject)
                    receipt = receipt.model_copy(update={"seq": self._insert(receipt)})
                    self._conn.execute(
                        "INSERT OR REPLACE INTO tape_pending (what, ref, since)"
                        " VALUES ('settle', '', ?)", (receipt.seq,))
                    self._conn.executemany(
                        "INSERT OR IGNORE INTO tape_pending (what, ref, since)"
                        " VALUES ('vault', ?, ?)", [(ref, receipt.ts) for ref in sorted(refs)])
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        settled = self.settle() if rows else True
        return Erasure(len(rows), kinds, receipt, tuple(sorted(refs)), settled)

    def settle(self) -> bool:
        """Fold the write-ahead log into the database and empty it, so no
        image of a page from before an erase is left in it. False when a
        reader in another process held the log the whole time (about two
        seconds). That it is still owed is kept in the database
        (``pending``), so the next erase, the worker's hourly job or calling
        this again finishes it, in this process or another."""
        for attempt in range(6):
            try:
                with self._lock:
                    owed = self._conn.execute(
                        "SELECT since FROM tape_pending WHERE what = 'settle'").fetchone()
                    usual = self._conn.execute("PRAGMA busy_timeout").fetchone()[0]
                    self._conn.execute("PRAGMA busy_timeout = 200")
                    try:
                        row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    finally:
                        self._conn.execute(f"PRAGMA busy_timeout = {int(usual)}")
            except sqlite3.OperationalError:
                row = (1,)
            if row is not None and row[0] == 0:
                if owed is not None:
                    # Paid, unless another erase has asked again since.
                    with self._lock:
                        self._conn.execute("DELETE FROM tape_pending WHERE what = 'settle'"
                                           " AND since = ?", (owed["since"],))
                return True
            time.sleep(0.05 * (attempt + 1))
        return False

    def pending(self) -> dict[str, Any]:
        """What erasing still owes: ``settle`` is true while an earlier image
        of an erased page may be in the write-ahead log, and ``vault`` counts
        the Vault files waiting to be removed."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT what, COUNT(*) AS n FROM tape_pending GROUP BY what").fetchall()
        counts = {r["what"]: r["n"] for r in rows}
        return {"settle": bool(counts.get("settle")), "vault": counts.get("vault", 0)}

    def release_orphans(self, vault: Any, *, quiet_ms: int | None = None) -> int:
        """Remove the Vault files queued by ``erase`` that are safe to remove.
        Returns how many are still waiting.

        All of it happens inside one write transaction on the Tape, which no
        ``print`` in any process can interleave with:

        - a file some event names again (the same long text was said
          elsewhere since) is no longer an orphan and is taken off the queue;
        - a file stored or put again within the last ``quiet_ms``
          (``ORPHAN_QUIET_MS``) waits: ``Vault.put`` touches a file it finds
          already there, so a writer that has stored it and is about to print
          the event naming it is not left pointing at nothing. It is looked
          at again next time (``Vault.discard`` makes the look and the delete
          one step);
        - anything else is deleted and taken off the queue. A file that
          cannot be deleted stays queued.
        """
        quiet_ms = ORPHAN_QUIET_MS if quiet_ms is None else quiet_ms
        with self._lock:
            if not self._conn.execute(
                    "SELECT 1 FROM tape_pending WHERE what = 'vault' LIMIT 1").fetchone():
                return 0
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                waiting = {r["ref"] for r in self._conn.execute(
                    "SELECT ref FROM tape_pending WHERE what = 'vault'")}
                named: set[str] = set()
                for row in self._conn.execute(
                        "SELECT payload FROM tape WHERE instr(payload, 'sha256:') > 0"
                ).fetchall():
                    named.update(waiting.intersection(_VAULT_REF.findall(row["payload"])))
                done = set(named)
                for ref in sorted(waiting - named):
                    try:
                        if vault.discard(ref, quiet_ms):
                            done.add(ref)
                    except OSError:
                        continue  # still there: tried again next time
                self._conn.executemany(
                    "DELETE FROM tape_pending WHERE what = 'vault' AND ref = ?",
                    [(ref,) for ref in done])
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return len(waiting - done)

    def finish(self, vault: Any) -> dict[str, Any]:
        """Do whatever erasing still owes and say what is left (``pending``).
        Cheap when nothing is owed. The worker calls this every hour."""
        owed = self.pending()
        if owed["settle"]:
            self.settle()
        if owed["vault"]:
            self.release_orphans(vault)
        return self.pending() if owed["settle"] or owed["vault"] else owed

    # --- read -----------------------------------------------------------------

    @staticmethod
    def _event(row: sqlite3.Row) -> Event:
        if row["erased"]:
            return Event(seq=row["seq"], id=row["id"], ts=row["ts"], kind=row["kind"],
                         v=row["v"], actor=row["actor"], cause=row["cause"], root=row["root"],
                         subject=row["subject"], payload={}, readable=False, erased=True)
        v, payload = row["v"], json.loads(row["payload"])
        readable = True
        try:
            v, payload = upcast(row["kind"], v, payload)
        except MissingUpcasterError:
            raise  # a bug in this code base; never pass it over quietly
        except (UnknownKindError, ValidationError, KeyError, TypeError, ValueError):
            # Written by an app that is not loaded here, by a newer release, or
            # before a payload model was tightened. Hand it back as stored and
            # marked: one unreadable event must not stop every read, every lens
            # and the live feed.
            v, payload, readable = row["v"], json.loads(row["payload"]), False
        return Event(
            seq=row["seq"],
            id=row["id"],
            ts=row["ts"],
            kind=row["kind"],
            v=v,
            actor=row["actor"],
            cause=row["cause"],
            root=row["root"],
            subject=row["subject"],
            payload=payload,
            readable=readable,
        )

    def _select(self, where: str, params: tuple, order: str, limit: int) -> list[Event]:
        sql = f"SELECT {_COLUMNS} FROM tape WHERE {where} ORDER BY seq {order} LIMIT ?"  # noqa: S608
        limit = max(1, min(int(limit), 100_000))
        with self._lock:
            rows = self._conn.execute(sql, (*params, limit)).fetchall()
        return [self._event(r) for r in rows]

    def after(
        self,
        seq: int = 0,
        *,
        limit: int = 500,
        kind: str | None = None,
        kinds: tuple[str, ...] | None = None,
        upto: int | None = None,
    ) -> list[Event]:
        """Events after ``seq``, oldest first. For folding and for the live feed.

        ``kinds`` narrows to several kinds; ``upto`` stops at a seq (inclusive).
        """
        where, params = "seq > ?", (_seq(seq),)
        wanted = (kind,) if kind else kinds
        if wanted is not None and not wanted:
            return []  # a lens that folds no kinds matches nothing
        if wanted is not None:
            where += f" AND kind IN ({','.join('?' * len(wanted))})"
            params = (*params, *wanted)
        if upto is not None:
            where, params = where + " AND seq <= ?", (*params, _seq(upto))
        return self._select(where, params, "ASC", limit)

    def latest(
        self, *, limit: int = 100, kind: str | None = None, before: int | None = None
    ) -> list[Event]:
        """The newest events, newest first. ``before`` pages backwards by seq."""
        where, params = "1=1", ()
        if kind:
            where, params = where + " AND kind = ?", (*params, kind)
        if before is not None:
            where, params = where + " AND seq < ?", (*params, _seq(before))
        return self._select(where, params, "DESC", limit)

    def get(self, event_id: str) -> Event | None:
        found = self._select("id = ?", (event_id,), "ASC", 1)
        return found[0] if found else None

    def by_subject(
        self,
        subject: str,
        *,
        limit: int = 500,
        before: int | None = None,
        after: int | None = None,
        newest_first: bool = False,
        kind: str | None = None,
    ) -> list[Event]:
        """Events about one subject, oldest first unless ``newest_first``.

        ``before`` and ``after`` are seqs (exclusive), so a long history can be
        read a page at a time instead of all at once.
        """
        where, params = "subject = ?", (subject,)
        if before is not None:
            where, params = where + " AND seq < ?", (*params, _seq(before))
        if after is not None:
            where, params = where + " AND seq > ?", (*params, _seq(after))
        if kind is not None:
            where, params = where + " AND kind = ?", (*params, kind)
        return self._select(where, params, "DESC" if newest_first else "ASC", limit)

    def subject_last_seq(self, subject: str) -> int:
        """The seq of the newest event about a subject, or 0. See ``append``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(seq) AS m FROM tape WHERE subject = ?", (subject,)
            ).fetchone()
        return int(row["m"] or 0)

    def by_root(self, root: str, *, limit: int = 1000) -> list[Event]:
        return self._select("root = ?", (root,), "ASC", limit)

    def caused_by(self, event_id: str, *, limit: int = 200) -> list[Event]:
        return self._select("cause = ?", (event_id,), "ASC", limit)

    def lineage(self, event_id: str, *, max_depth: int = 50) -> list[Event]:
        """Ancestors through cause links, nearest first."""
        out: list[Event] = []
        seen = {event_id}
        event = self.get(event_id)
        while event is not None and event.cause and len(out) < max_depth:
            if event.cause in seen:
                break  # a cause cycle is corrupt data; stop rather than spin
            parent = self.get(event.cause)
            if parent is None:
                break
            out.append(parent)
            seen.add(parent.id)
            event = parent
        return out

    def last_seq(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT MAX(seq) AS m FROM tape").fetchone()
        return int(row["m"] or 0)

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM tape").fetchone()["n"]
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM tape GROUP BY kind ORDER BY n DESC"
            ).fetchall()
        size = sum(
            p.stat().st_size
            for p in (self.path, Path(f"{self.path}-wal"))
            if p.exists()
        )
        return {"events": total, "bytes": size, "by_kind": {r["kind"]: r["n"] for r in rows}}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
