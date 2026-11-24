"""The part of a conversation that is off the record: a store of its own,
with a real delete.

Nothing here writes to the Tape, the Vault or a lens. Messages, replies and
any title given while off the record are rows in one SQLite file in the data
folder, and deleting them removes the rows. The only trace an off-the-record
exchange leaves anywhere else is the Brain's usual receipt of the model call,
which carries counts and never words (see docs/chat.md).

A conversation has a row here once anything of it is off the record. For one
that has never been on the record this row is all there is: it also holds
what answers and whether it is archived. For one the Tape knows as well, the
Tape decides those, and this row holds only the title given off the record.

Each message remembers the Tape's position when it was stored, so the two
stores can be read back as one thread in the order things happened.

The file is shared by every process on the data folder. A connection is opened
for one read or one short ``BEGIN IMMEDIATE`` write and closed again, so
SQLite serialises writers and nothing is held between calls. The file is only
created when something is first said off the record.

This is the other side of ``backends.Backend``; ``onrecord`` is the Tape's.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from jimmyhq import storeversion
from jimmyhq.chat import store
from jimmyhq.chat.store import ChatError
from jimmyhq.core import Core
from jimmyhq.ids import now_ms
from jimmyhq.tape.store import connect

FILE = "chat_offrecord.db"
MAX_MESSAGES = 5000  # one conversation stays one bounded read

GONE = ("This conversation was deleted while the reply was being written, so the reply was "
        "not kept. Reload the list.")
NO_TITLE = {"title": "", "title_source": "none"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  title_source TEXT NOT NULL,
  target       TEXT NOT NULL,
  effort       TEXT,
  local_only   INTEGER NOT NULL,
  created      INTEGER NOT NULL,
  updated      INTEGER NOT NULL,
  archived     INTEGER NOT NULL DEFAULT 0,
  recorded     INTEGER NOT NULL DEFAULT 0,
  use_memory   INTEGER NOT NULL DEFAULT 1,
  archived_at  INTEGER,
  delete_marked_at INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
  seq             INTEGER PRIMARY KEY,
  tape_seq        INTEGER NOT NULL,
  id              TEXT UNIQUE NOT NULL,
  conversation_id TEXT NOT NULL,
  role            TEXT NOT NULL,
  created         INTEGER NOT NULL,
  text            TEXT NOT NULL,
  reply_to        TEXT,
  facts           TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, seq);
"""


def _before_versions(conn: sqlite3.Connection) -> None:
    """A file from before versions existed gains the columns added since it was made."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    # From before the memory switch.
    if "use_memory" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN use_memory"
                     " INTEGER NOT NULL DEFAULT 1")
    # From before archived conversations could be deleted after a time: when each was
    # archived was never kept, so the clock for those starts now, never earlier.
    if "archived_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN archived_at INTEGER")
        conn.execute("UPDATE conversations SET archived_at = ? WHERE archived = 1",
                     (now_ms(),))
    if "delete_marked_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN delete_marked_at INTEGER")


STORE = storeversion.declare("chat_offrecord", _SCHEMA, files=(FILE,),
                             unversioned=_before_versions)

_CONV = ("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) "
         "AS messages FROM conversations c")
_FACTS = tuple(name for name in store.REPLY_FIELDS if name != "reply_to")


def _conv(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "title": row["title"], "title_source": row["title_source"],
        "target": json.loads(row["target"]), "effort": row["effort"],
        "local_only": bool(row["local_only"]),
        # A file made before the memory switch existed has no such column: on.
        "use_memory": bool(row["use_memory"]) if "use_memory" in row.keys() else True,
        "created": row["created"],
        "updated": row["updated"], "messages": row["messages"],
        "archived": bool(row["archived"]),
        # When it was archived, while it is. A file from before this was kept
        # says nothing until its next write, which starts the clock then.
        "archived_at": (row["archived_at"] if "archived_at" in row.keys() else None)
        if row["archived"] else None,
        # When the job that deletes old archived conversations marked it.
        "delete_marked_at": (row["delete_marked_at"] if "delete_marked_at" in row.keys()
                             else None) if row["archived"] else None,
        # True once the Tape knows this conversation too.
        "recorded": bool(row["recorded"]),
    }


def _message(row: sqlite3.Row) -> dict[str, Any]:
    message = {"id": row["id"], "role": row["role"], "created": row["created"],
               "chars": len(row["text"]), "text": row["text"], "text_ref": None,
               "event": None, "seq": row["seq"], "on_record": False,
               # After everything the Tape held when this was stored.
               "order": (row["tape_seq"], 1, row["seq"])}
    if row["role"] == "assistant":
        # A reply kept before memory, or web lookup, existed says nothing of it.
        message |= {"reply_to": row["reply_to"], "memory": None, "web": None,
                    **json.loads(row["facts"])}
    return message


class OffRecord:
    on_record = False

    def __init__(self, core: Core):
        self.path = Path(core.config.data) / FILE
        self._position = core.tape.last_seq  # read only: where the Tape has got to

    # --- the file ----------------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        fresh = not self.path.exists()
        conn = connect(self.path)
        try:
            # What is deleted is overwritten in the file, not only unlinked.
            conn.execute("PRAGMA secure_delete=ON")
            if fresh:
                with contextlib.suppress(OSError):
                    os.chmod(self.path, 0o600)
        except BaseException:
            conn.close()
            raise
        return conn

    @contextlib.contextmanager
    def _read(self) -> Iterator[sqlite3.Connection | None]:
        """A connection for one read, or None while the file has never been
        needed. A file with no tables yet (another process is just creating
        it) reads as empty."""
        if not self.path.exists():
            yield None
            return
        conn = self._open()
        try:
            made = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages'").fetchone()
            if made:  # an older file is brought up to date, a newer one refused
                storeversion.prepare(conn, STORE)
            yield conn if made else None
        finally:
            conn.close()

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One short transaction. Of two processes writing at once, one waits."""
        conn = self._open()
        try:
            # Made, or brought up to date, in transactions of its own, so two
            # processes starting on a fresh data folder cannot trip over each other.
            storeversion.prepare(conn, STORE)
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    # --- reading -----------------------------------------------------------------

    def entries(self) -> list[dict[str, Any]]:
        with self._read() as conn:
            return [] if conn is None else [_conv(row) for row in conn.execute(_CONV)]

    def find(self, conv_id: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn and conn.execute(f"{_CONV} WHERE c.id = ?", (conv_id,)).fetchone()
        return _conv(row) if row else None

    def story(self, conv_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], bool] | None:
        with self._read() as conn:
            row = conn and conn.execute(f"{_CONV} WHERE c.id = ?", (conv_id,)).fetchone()
            if not row:
                return None
            rows = conn.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq",
                                (conv_id,)).fetchall()
        return _conv(row), [_message(r) for r in rows], len(rows) >= MAX_MESSAGES

    def newest(self, conv_id: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
        with self._read() as conn:
            rows = [] if conn is None else conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq DESC LIMIT ?",
                (conv_id, limit + 1)).fetchall()
        return [_message(r) for r in rows[:limit]][::-1], len(rows) > limit

    # --- writing -----------------------------------------------------------------

    def create(self, conv_id: str, fields: dict[str, Any], *, actor: str) -> dict[str, Any]:
        with self._write() as conn:
            self._insert(conn, conv_id, fields, recorded=False)
            return _conv(conn.execute(f"{_CONV} WHERE c.id = ?", (conv_id,)).fetchone())

    def _insert(self, conn: sqlite3.Connection, conv_id: str, fields: dict[str, Any], *,
                recorded: bool) -> None:
        now = now_ms()
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, title, title_source, target, effort,"
            " local_only, use_memory, created, updated, recorded) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (conv_id, fields["title"], fields["title_source"], json.dumps(fields["target"]),
             fields["effort"], int(fields["local_only"]), int(fields.get("use_memory", True)),
             now, now, int(recorded)))

    def _ensure(self, conn: sqlite3.Connection, conv: dict[str, Any]) -> None:
        """Make sure the conversation has its row here. One the Tape knows
        gets a row the first time something of it goes off the record; one
        the Tape does not know must still have the row it was started with."""
        if conv["recorded"]:
            self._insert(conn, conv["id"], conv | NO_TITLE, recorded=True)
        elif not conn.execute("SELECT 1 FROM conversations WHERE id = ?",
                              (conv["id"],)).fetchone():
            raise ChatError(409, GONE)

    def _set(self, conn: sqlite3.Connection, conv_id: str, values: dict[str, Any]) -> None:
        names = ", ".join(f"{name} = ?" for name in values)
        conn.execute(f"UPDATE conversations SET {names} WHERE id = ?",  # noqa: S608
                     (*values.values(), conv_id))

    def change(self, conv: dict[str, Any], diff: dict[str, Any], *, actor: str) -> None:
        values = {name: diff[name] for name in ("title", "title_source", "effort") if name in diff}
        if "target" in diff:
            values["target"] = json.dumps(diff["target"])
        for name in ("local_only", "use_memory"):
            if name in diff:
                values[name] = int(diff[name])
        with self._write() as conn:
            self._ensure(conn, conv)
            self._set(conn, conv["id"], values)

    def set_archived(self, conv: dict[str, Any], archived: bool, *, actor: str) -> None:
        with self._write() as conn:
            self._ensure(conn, conv)
            # Archiving starts a new clock and restoring stops it: either
            # way a mark for deletion made under the old one no longer holds.
            self._set(conn, conv["id"], {"archived": int(archived),
                                         "archived_at": now_ms() if archived else None,
                                         "delete_marked_at": None})

    def mark_for_deletion(self, conv_id: str, at: int | None) -> None:
        """Note when a conversation the Tape does not know was marked for
        deletion, or (None) take the mark away. Only while it is archived."""
        with self._write() as conn:
            conn.execute("UPDATE conversations SET delete_marked_at = ? WHERE id = ?"
                         " AND archived = 1", (at, conv_id))

    def mark_recorded(self, conv_id: str) -> None:
        """The Tape now knows this conversation: it decides what answers and
        whether it is archived from here on."""
        with self._write() as conn:
            self._set(conn, conv_id, {"recorded": 1})

    def say(self, conv: dict[str, Any], message_id: str, text: str, title: str | None, *,
            actor: str) -> dict[str, Any]:
        now = now_ms()
        with self._write() as conn:
            self._ensure(conn, conv)
            values: dict[str, Any] = {"updated": now}
            if title is not None:
                values |= {"title": title, "title_source": "cut"}
            self._set(conn, conv["id"], values)
            cur = conn.execute(
                "INSERT INTO messages (tape_seq, id, conversation_id, role, created, text)"
                " VALUES (?,?,?,?,?,?)",
                (self._position(), message_id, conv["id"], "user", now, text))
            row = conn.execute("SELECT * FROM messages WHERE seq = ?",
                               (cur.lastrowid,)).fetchone()
        return _message(row)

    def answer(self, conv: dict[str, Any], reply: dict[str, Any], text: str, *,
               cause: str | None) -> int:
        now = now_ms()
        facts = json.dumps({name: reply[name] for name in _FACTS})
        with self._write() as conn:
            self._ensure(conn, conv)
            # A regenerated reply takes the old one's place, and here the old
            # one really goes.
            conn.execute("DELETE FROM messages WHERE conversation_id = ? AND role = 'assistant'"
                         " AND reply_to = ?", (conv["id"], reply["reply_to"]))
            conn.execute(
                "INSERT INTO messages (tape_seq, id, conversation_id, role, created, text,"
                " reply_to, facts) VALUES (?,?,?,?,?,?,?,?)",
                (self._position(), reply["message_id"], conv["id"], "assistant", now, text,
                 reply["reply_to"], facts))
            self._set(conn, conv["id"], {"updated": now})
        return now

    def retract(self, conv_id: str, reply_to: str) -> None:
        """A newer reply, kept on the record, has taken this one's place:
        remove the reply kept here, if there is one."""
        with self._read() as conn:
            if conn is None or not conn.execute(
                    "SELECT 1 FROM messages WHERE conversation_id = ? AND role = 'assistant'"
                    " AND reply_to = ?", (conv_id, reply_to)).fetchone():
                return
        with self._write() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ? AND role = 'assistant'"
                         " AND reply_to = ?", (conv_id, reply_to))
        self._settle()

    def bring_up_to_date(self) -> None:
        """Give a file made by an earlier release the columns this one reads.
        Any write does this; the job that deletes archived conversations asks
        for it so that it need not wait for one."""
        if self.path.exists():
            with self._write():
                pass

    def forget(self, conv_id: str, *, archived_before: int | None = None,
               marked_before: int | None = None) -> int | None:
        """Remove, for good, everything of this conversation that is kept
        here: its messages, replies and title. Returns how many messages
        went.

        With ``archived_before`` nothing is removed, and None is returned,
        unless the conversation is archived here, has been since before
        that moment, and was marked for deletion before ``marked_before``. The
        check and the delete are one transaction, so a conversation restored
        a moment ago is safe from the job that deletes old archived ones."""
        with self._read() as conn:
            if conn is None:
                return 0
        with self._write() as conn:
            if archived_before is not None:
                row = conn.execute("SELECT archived, archived_at, delete_marked_at FROM"
                                   " conversations WHERE id = ?", (conv_id,)).fetchone()
                if (row is None or not row["archived"] or row["archived_at"] is None
                        or row["archived_at"] > archived_before
                        or marked_before is None or row["delete_marked_at"] is None
                        or row["delete_marked_at"] > marked_before):
                    return None
            gone = conn.execute("DELETE FROM messages WHERE conversation_id = ?",
                                (conv_id,)).rowcount
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._settle()
        return gone

    def _settle(self) -> None:
        """After a delete: the rows are gone and their pages overwritten; fold
        that into the main file now, so the words do not linger in the
        write-ahead log. Another process reading at this moment only delays it."""
        with contextlib.suppress(sqlite3.Error):
            conn = self._open()
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()
