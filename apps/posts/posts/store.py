"""Posts' own records, in the app's own database (``posts.db`` in its data folder).

Configuration (pages, rules, lessons, the owner's edits) and work (pieces, turns, posts,
checks, attempts, upstream copies, what agents were given and handed back, and the ideas
Reader offered, ``from_reader``) live here,
never on the owner's record: the Tape gets only ids and short words (``kinds``). Every
time is unix milliseconds, UTC. Every change of state goes through ``move_piece`` or
``move_post``, which ask ``states`` first.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from posts import states

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS page (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, register TEXT NOT NULL DEFAULT '',
  samples TEXT NOT NULL DEFAULT '', timezone TEXT NOT NULL DEFAULT '',
  hours TEXT NOT NULL DEFAULT '["08:00"]', accounts TEXT NOT NULL DEFAULT '[]',
  tags TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rule (
  id INTEGER PRIMARY KEY, page_id INTEGER, kind TEXT NOT NULL
  CHECK (kind IN ('phrase', 'tag', 'question')), text TEXT NOT NULL,
  says TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lesson (
  id INTEGER PRIMARY KEY, page_id INTEGER, post_id INTEGER, kind TEXT NOT NULL
  CHECK (kind IN ('decline', 'send_back')), reason TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', quotes TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS piece (
  id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL, way TEXT NOT NULL,
  source_url TEXT, source_title TEXT NOT NULL DEFAULT '', came_from TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', sponsored INTEGER NOT NULL
  DEFAULT 0, source_text TEXT NOT NULL DEFAULT '', sentences TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL, round INTEGER NOT NULL DEFAULT 0, step_started INTEGER,
  deadline_ms INTEGER, error TEXT NOT NULL DEFAULT '', overlap TEXT NOT NULL DEFAULT '{}',
  owner_note TEXT NOT NULL DEFAULT '', writing_day TEXT NOT NULL DEFAULT '',
  card_id TEXT, created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS turn (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, step TEXT NOT NULL, role TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '', local INTEGER, gloss TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS post (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, page_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, claims TEXT NOT NULL DEFAULT '[]',
  links TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
  card_id TEXT, card_filed INTEGER, nudged INTEGER NOT NULL DEFAULT 0, slot_ms INTEGER,
  scheduler TEXT NOT NULL DEFAULT '', out_of_step INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS check_result (
  id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, revision INTEGER NOT NULL,
  name TEXT NOT NULL, key TEXT NOT NULL, level TEXT NOT NULL
  CHECK (level IN ('ok', 'warn', 'ask')), detail TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '[]', answer TEXT, answered INTEGER);
CREATE TABLE IF NOT EXISTS attempt (
  id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, action TEXT NOT NULL,
  scheduler TEXT NOT NULL, started INTEGER NOT NULL, finished INTEGER,
  outcome TEXT NOT NULL CHECK (outcome IN ('pending', 'sent', 'refused', 'unsure', 'cleared')),
  detail TEXT NOT NULL DEFAULT '', sent TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS upstream (
  id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, account TEXT NOT NULL,
  external_id TEXT, state TEXT NOT NULL DEFAULT 'booked', seen INTEGER, slot_ms INTEGER,
  said TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS figure (
  id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, page_id INTEGER NOT NULL,
  account TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL, seen INTEGER NOT NULL,
  UNIQUE (post_id, account, metric));
CREATE TABLE IF NOT EXISTS practice_log (
  id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, action TEXT NOT NULL,
  would_send TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS agent_work (
  id INTEGER PRIMARY KEY, piece_id INTEGER, page_id INTEGER NOT NULL, step TEXT NOT NULL,
  role TEXT NOT NULL, work_id TEXT, state TEXT NOT NULL DEFAULT 'open', answered INTEGER,
  partial INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0, card_id TEXT,
  agent_label TEXT, why TEXT NOT NULL DEFAULT '', given INTEGER NOT NULL, ended INTEGER);
CREATE TABLE IF NOT EXISTS agent_step (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, agent TEXT NOT NULL, role TEXT,
  tool TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS credit (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, step TEXT NOT NULL, by TEXT NOT NULL
  CHECK (by IN ('agent', 'plain', 'owner')), role TEXT NOT NULL DEFAULT '', label TEXT,
  agent TEXT, note TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS review (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, post_id INTEGER NOT NULL,
  revision INTEGER NOT NULL, agent TEXT NOT NULL, verdict TEXT NOT NULL,
  points TEXT NOT NULL DEFAULT '[]', dropped INTEGER NOT NULL DEFAULT 0, used INTEGER,
  created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS edit (
  id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL, post_id INTEGER NOT NULL,
  before TEXT NOT NULL, after TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS picture (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, post_id INTEGER, ref TEXT NOT NULL,
  sha256 TEXT NOT NULL, dhash TEXT, note TEXT NOT NULL DEFAULT '', analysis TEXT,
  created INTEGER NOT NULL, deleted INTEGER);
CREATE TABLE IF NOT EXISTS idea (
  id INTEGER PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, offer INTEGER, url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
  why TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'new', piece_id INTEGER,
  started_by TEXT NOT NULL DEFAULT '', started_day TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, decided INTEGER);
CREATE INDEX IF NOT EXISTS picture_piece ON picture (piece_id);
CREATE INDEX IF NOT EXISTS post_page ON post (page_id, state);
CREATE INDEX IF NOT EXISTS agent_work_piece ON agent_work (piece_id, step);
CREATE INDEX IF NOT EXISTS agent_step_piece ON agent_step (piece_id);
CREATE INDEX IF NOT EXISTS check_post ON check_result (post_id, revision);
CREATE INDEX IF NOT EXISTS figure_page ON figure (page_id, post_id);
"""

# Columns added after a table was first made: (table, column, its declaration).
ADDED = (("upstream", "slot_ms", "INTEGER"), ("upstream", "said", "TEXT NOT NULL DEFAULT ''"),
         ("piece", "origin", "TEXT NOT NULL DEFAULT ''"),  # "from Reader, topic X"
         ("page", "paused", "INTEGER NOT NULL DEFAULT 0"),  # no new work while set
         ("page", "book_through", "TEXT NOT NULL DEFAULT ''"))  # '' follows Posts' setting

# Upstream copies that still stand for the post on the scheduler.
LIVE_UPSTREAM = ("booked", "unconfirmed")

_JSON = {"hours", "accounts", "tags", "sentences", "overlap", "data", "claims", "links",
         "evidence", "quotes", "sent", "would_send", "points", "analysis"}


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key in _JSON & out.keys():
        if isinstance(out[key], str):
            out[key] = json.loads(out[key])
    return out


def _dump(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, dict | list) else value


class Store:
    def __init__(self, conn: sqlite3.Connection, clock: Any):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._add_columns()
        self.conn.commit()
        self.now = clock

    def _add_columns(self) -> None:
        """A database made before a column existed gets it, empty."""
        for table, column, declared in ADDED:
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declared}")

    # --- plain helpers --------------------------------------------------------------------

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        return _row(self.conn.execute(sql, tuple(params)).fetchone())

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [_row(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def insert(self, table: str, values: dict[str, Any]) -> int:
        keys = list(values)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",  # noqa: S608
            [_dump(values[k]) for k in keys])
        self.conn.commit()
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        sets = ", ".join(f"{k}=?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",  # noqa: S608
                          [*(_dump(v) for v in values.values()), row_id])
        self.conn.commit()

    def get_meta(self, key: str) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO "
                          "UPDATE SET value=excluded.value", (key, json.dumps(value)))
        self.conn.commit()

    # --- configuration --------------------------------------------------------------------

    def page(self, page_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM page WHERE id=?", (page_id,))

    def pages(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM page ORDER BY name")

    def active_pages(self) -> list[dict[str, Any]]:
        """The pages new work may be started for: every page that is not paused."""
        return [p for p in self.pages() if not p.get("paused")]

    def rules(self, page_id: int | None) -> list[dict[str, Any]]:
        """The rules for one page, with the rules for every page."""
        return self.all("SELECT * FROM rule WHERE page_id IS NULL OR page_id=? ORDER BY id",
                        (page_id,))

    def lessons(self, page_id: int | None, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM lesson WHERE page_id IS NULL OR page_id=? "
                        "ORDER BY id DESC LIMIT ?", (page_id, limit))

    # --- work -----------------------------------------------------------------------------

    def piece(self, piece_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM piece WHERE id=?", (piece_id,))

    def post(self, post_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM post WHERE id=?", (post_id,))

    def post_of(self, piece_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM post WHERE piece_id=? ORDER BY id DESC LIMIT 1",
                        (piece_id,))

    def turns(self, piece_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM turn WHERE piece_id=? ORDER BY id", (piece_id,))

    def add_turn(self, piece_id: int, step: str, gloss: str, *, role: str = "code",
                 model: str = "", local: bool | None = None,
                 data: dict[str, Any] | None = None) -> int:
        """One human line saying what a step did, with what it was given back."""
        return self.insert("turn", {"piece_id": piece_id, "step": step, "role": role,
                                    "model": model or "", "local": local, "gloss": gloss[:500],
                                    "data": data or {}, "created": self.now()})

    def checks(self, post_id: int, revision: int | None = None) -> list[dict[str, Any]]:
        if revision is None:
            post = self.post(post_id)
            revision = post["revision"] if post else 0
        return self.all("SELECT * FROM check_result WHERE post_id=? AND revision=? ORDER BY id",
                        (post_id, revision))

    def attempts(self, post_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM attempt WHERE post_id=? ORDER BY id", (post_id,))

    def upstream(self, post_id: int, *, live: bool = False) -> list[dict[str, Any]]:
        """The post's copies on its scheduler; with ``live``, only those that still stand
        for it there (not cancelled, cleared or gone)."""
        if live:
            return self.all("SELECT * FROM upstream WHERE post_id=? AND state IN (?, ?) "
                            "ORDER BY id", (post_id, *LIVE_UPSTREAM))
        return self.all("SELECT * FROM upstream WHERE post_id=? ORDER BY id", (post_id,))

    # --- the only ways a state changes ----------------------------------------------------

    def move_piece(self, piece_id: int, new: str, **more: Any) -> dict[str, Any]:
        piece = self.piece(piece_id)
        if piece is None:
            raise states.StateError("There is no such piece. Nothing was changed.")
        states.need(states.PIECE, "piece", piece["state"], new)
        self.update("piece", piece_id, {"state": new, "updated": self.now(), **more})
        return self.piece(piece_id) or {}

    def move_post(self, post_id: int, new: str, **more: Any) -> dict[str, Any]:
        post = self.post(post_id)
        if post is None:
            raise states.StateError("There is no such post. Nothing was changed.")
        states.need(states.POST, "post", post["state"], new)
        self.update("post", post_id, {"state": new, "updated": self.now(), **more})
        return self.post(post_id) or {}
