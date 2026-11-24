"""Heard's own records, in the app's private database (``heard.db`` in its data folder).

Conversations, their lines, the to-dos suggested from them and a tally of refused messages
live here and nowhere else: never on the owner's record, never in Memory. The database is
a private one: a deleted row is overwritten in the file, not only unlinked, and a deleted
conversation's words are also removed from the search index. Every time is unix
milliseconds, UTC. No audio is ever kept: a line is text, a speaker and when.

``conversation.key`` is the sender's own id for a finished conversation (``omi:<id>``),
so the same one sent twice is kept once. A conversation stitched from live pieces has the
key ``live:<session>`` (and ``~2``, ``~3`` for each later one of the same session).
``recorder`` is the sender's own id for the recorder (Omi's ``?uid=``), or "" when none came.
``scope`` is what a line's ``uid`` is unique within: for a stitched session it is shared by
all its conversations, so a resent piece is recognised even after the gap closed it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversation (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, source TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', overview TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', summary_ms INTEGER,
  base_ms INTEGER, started_ms INTEGER NOT NULL, ended_ms INTEGER NOT NULL,
  last_seen_ms INTEGER NOT NULL, created_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS conversation_started ON conversation (started_ms, id);
CREATE INDEX IF NOT EXISTS conversation_scope ON conversation (scope, last_seen_ms);
CREATE TABLE IF NOT EXISTS segment (
  id INTEGER PRIMARY KEY, conv_id INTEGER NOT NULL, scope TEXT NOT NULL, uid TEXT NOT NULL,
  speaker TEXT NOT NULL DEFAULT '', is_user INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL, words INTEGER NOT NULL DEFAULT 0, offset_ms INTEGER NOT NULL DEFAULT 0,
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, UNIQUE (scope, uid));
CREATE INDEX IF NOT EXISTS segment_conv ON segment (conv_id, start_ms, id);
CREATE TABLE IF NOT EXISTS todo (
  id INTEGER PRIMARY KEY, conv_id INTEGER NOT NULL, text TEXT NOT NULL,
  quote TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'suggested',
  source TEXT NOT NULL DEFAULT 'model', task_id TEXT NOT NULL DEFAULT '',
  created_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS todo_conv ON todo (conv_id);
CREATE TABLE IF NOT EXISTS declined (
  conv_id INTEGER NOT NULL, line TEXT NOT NULL, PRIMARY KEY (conv_id, line));
CREATE TABLE IF NOT EXISTS refused (
  kind TEXT PRIMARY KEY, n INTEGER NOT NULL, last_ms INTEGER NOT NULL);
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS segment_fts USING fts5(
  text, content='segment', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS segment_after_insert AFTER INSERT ON segment BEGIN
  INSERT INTO segment_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS segment_after_delete AFTER DELETE ON segment BEGIN
  INSERT INTO segment_fts (segment_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

HIT_ON, HIT_OFF = "\x01", "\x02"  # marks round a matched word in a snippet; never in kept text
_WORD = re.compile(r"\w+", re.UNICODE)
MAX_QUERY_WORDS = 12


def words_of(query: str) -> list[str]:
    return _WORD.findall(query or "")[:MAX_QUERY_WORDS]


def snippet_parts(text: str) -> list[list[Any]]:
    """A snippet with hit marks as ``[[text, is_hit], ...]``, for a page to draw."""
    parts: list[list[Any]] = []
    hit = False
    for piece in re.split(f"([{HIT_ON}{HIT_OFF}])", text):
        if piece == HIT_ON:
            hit = True
        elif piece == HIT_OFF:
            hit = False
        elif piece:
            parts.append([piece, hit])
    return parts


@dataclass(frozen=True)
class Line:
    """One line of speech as it is kept. Times are unix milliseconds, UTC."""

    uid: str
    speaker: str
    is_user: bool
    text: str
    offset_ms: int
    start_ms: int
    end_ms: int


class Store:
    def __init__(self, private: Any, clock: Any):
        self.private = private
        self.conn: sqlite3.Connection = private.conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.has_fts = self._make_fts()
        self._migrate()
        self.conn.commit()
        self.now = clock

    def _make_fts(self) -> bool:
        """Full-text search where this SQLite has it; otherwise search falls back to a
        plain scan, slower and with no ranking."""
        try:
            self.conn.executescript(FTS)
        except sqlite3.OperationalError:
            return False
        return True

    def _migrate(self) -> None:
        """Bring a database made by an older Heard up to ``SCHEMA_VERSION``: each step
        takes it from the version before. Never edit a step that has shipped."""
        have = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if have > SCHEMA_VERSION:
            raise RuntimeError("This database was made by a newer Heard. Update Heard "
                               "instead of opening it with an older one.")
        if have < 2:  # 1 -> 2: the id of the recorder a conversation came from (?uid=)
            kept = {r[1] for r in self.conn.execute("PRAGMA table_info(conversation)")}
            if "recorder" not in kept:
                self.conn.execute(
                    "ALTER TABLE conversation ADD COLUMN recorder TEXT NOT NULL DEFAULT ''")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self.private.close()

    # --- plain helpers --------------------------------------------------------------------

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def insert(self, table: str, values: dict[str, Any], *, commit: bool = True) -> int:
        keys = list(values)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",  # noqa: S608
            [values[k] for k in keys])
        if commit:
            self.conn.commit()
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        sets = ", ".join(f"{k}=?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",  # noqa: S608
                          [*values.values(), row_id])
        self.conn.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, **values: Any) -> None:
        for key, value in values.items():
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        self.conn.commit()

    def heard(self, received_ms: int) -> None:
        """Something arrived (kept or refused): the door is in use."""
        if self.get_meta("first_seen_ms") is None:
            self.set_meta(first_seen_ms=received_ms)
        self.set_meta(last_heard_ms=received_ms)

    def refuse(self, kind: str, received_ms: int) -> None:
        self.conn.execute(
            "INSERT INTO refused (kind, n, last_ms) VALUES (?, 1, ?) ON CONFLICT(kind) "
            "DO UPDATE SET n = n + 1, last_ms = excluded.last_ms", (kind, received_ms))
        self.conn.commit()
        self.heard(received_ms)

    def refused_tally(self) -> list[dict[str, Any]]:
        return self.all("SELECT kind, n, last_ms FROM refused ORDER BY n DESC, kind")

    # --- conversations --------------------------------------------------------------------

    def conversation(self, conv_id: Any) -> dict[str, Any] | None:
        if not isinstance(conv_id, int) or isinstance(conv_id, bool):
            return None
        return self.one("SELECT * FROM conversation WHERE id=?", (conv_id,))

    def by_key(self, key: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM conversation WHERE key=?", (key,))

    def open_for(self, scope: str, since_ms: int) -> dict[str, Any] | None:
        """The newest conversation of ``scope`` that was added to since ``since_ms``."""
        return self.one(
            "SELECT * FROM conversation WHERE scope=? AND last_seen_ms >= ? "
            "ORDER BY last_seen_ms DESC, id DESC LIMIT 1", (scope, since_ms))

    def free_key(self, key: str) -> str:
        """``key``, or ``key~2``, ``key~3``... the first no conversation has."""
        if self.by_key(key) is None:
            return key
        n = 2
        while self.by_key(f"{key}~{n}") is not None:
            n += 1
        return f"{key}~{n}"

    def has_segment(self, scope: str, uid: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM segment WHERE scope=? AND uid=?", (scope, uid)).fetchone() is not None

    def add_segments(self, conv_id: int, scope: str, segments: Iterable[Any]) -> int:
        """Keep lines (a duplicate is ignored). Returns how many were new. Not committed."""
        added = 0
        for s in segments:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO segment (conv_id, scope, uid, speaker, is_user, text, "
                "words, offset_ms, start_ms, end_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conv_id, scope, s.uid, s.speaker, 1 if s.is_user else 0, s.text,
                 len(s.text.split()), s.offset_ms, s.start_ms, s.end_ms))
            added += cur.rowcount
        return added

    def fit_times(self, conv_id: int) -> None:
        """A conversation starts at its first line and ends at its last. Not committed."""
        self.conn.execute(
            "UPDATE conversation SET "
            "started_ms = COALESCE((SELECT MIN(start_ms) FROM segment WHERE conv_id=?), "
            "started_ms), "
            "ended_ms = COALESCE((SELECT MAX(end_ms) FROM segment WHERE conv_id=?), ended_ms) "
            "WHERE id=?", (conv_id, conv_id, conv_id))

    def stretch_to(self, conv_id: int, ended_ms: int) -> None:
        """Let a conversation end later than its last line, never earlier. Not committed."""
        self.conn.execute("UPDATE conversation SET ended_ms = MAX(ended_ms, ?) WHERE id=?",
                          (ended_ms, conv_id))

    # --- deleting -------------------------------------------------------------------------

    def delete_conversation(self, conv_id: int) -> None:
        """Delete a conversation and everything under it for good."""
        self.private.purge("segment", "conv_id=?", (conv_id,))
        self.private.purge("todo", "conv_id=?", (conv_id,))
        self.private.purge("declined", "conv_id=?", (conv_id,))
        self.private.purge("conversation", "id=?", (conv_id,))
        self.tidy_index()

    def tidy_index(self) -> None:
        """Rewrite the search index so the words of what was deleted are gone from it."""
        if self.has_fts:
            self.conn.execute("INSERT INTO segment_fts (segment_fts) VALUES ('optimize')")
        self.conn.commit()
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def trim(self, before_ms: int) -> int:
        """Delete conversations older than ``before_ms``: the earlier of when the sender says
        one ended and when Heard last heard it (a time only Heard sets), so a sender that
        claims a time far ahead cannot make what it sent outlast the retention. Returns how
        many."""
        old = [r["id"] for r in self.all(
            "SELECT id FROM conversation WHERE MIN(ended_ms, last_seen_ms) < ?", (before_ms,))]
        for conv_id in old:
            self.delete_conversation(conv_id)
        return len(old)

    def forget_everything(self) -> int:
        """Delete every conversation, to-do, the tally and the notes, for good."""
        count = self.count()
        for table in ("segment", "todo", "declined", "conversation", "refused", "meta"):
            self.private.purge(table, "1=1")
        self.tidy_index()
        return count

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0])

    # --- searching ------------------------------------------------------------------------

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Lines that hold every word of ``query`` (the last may be the start of a word),
        best first. Each is ``{segment_id, conv_id, speaker, start_ms, title, started_ms,
        parts}`` where ``parts`` is the line, cut to what is round the match."""
        words = words_of(query)
        if not words:
            return []
        if self.has_fts:
            match = " ".join(f'"{w}"' for w in words[:-1])
            match = f'{match} "{words[-1]}"*'.strip()
            rows = self.all(
                "SELECT s.id AS segment_id, s.conv_id, s.speaker, s.start_ms, c.title, "
                "c.started_ms, snippet(segment_fts, 0, ?, ?, '…', 16) AS snip "
                "FROM segment_fts JOIN segment s ON s.id = segment_fts.rowid "
                "JOIN conversation c ON c.id = s.conv_id "
                "WHERE segment_fts MATCH ? ORDER BY rank LIMIT ?",
                (HIT_ON, HIT_OFF, match, limit))
            for row in rows:
                row["parts"] = snippet_parts(row.pop("snip"))
            return rows
        return self._scan(words, limit)

    def _scan(self, words: list[str], limit: int) -> list[dict[str, Any]]:
        like = " AND ".join("s.text LIKE ? ESCAPE '\\'" for _ in words)
        patterns = ["%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    for w in words]
        rows = self.all(
            "SELECT s.id AS segment_id, s.conv_id, s.speaker, s.start_ms, s.text, c.title, "
            f"c.started_ms FROM segment s JOIN conversation c ON c.id = s.conv_id WHERE {like} "  # noqa: S608
            "ORDER BY s.start_ms DESC LIMIT ?", (*patterns, limit))
        for row in rows:
            text = row.pop("text")
            at = min((i for w in words if (i := text.casefold().find(w.casefold())) >= 0),
                     default=0)
            lo = max(0, at - 60)
            cut = text[lo:lo + 160]
            mark = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
            cut = mark.sub(lambda m: f"{HIT_ON}{m.group(0)}{HIT_OFF}", cut)
            row["parts"] = snippet_parts(("…" if lo else "") + cut)
        return rows
