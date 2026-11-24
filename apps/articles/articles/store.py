"""Articles' own records, in the app's own database (``articles.db`` in its data folder).

Configuration (sites, lessons) and work (pieces, their sources with numbered sentences,
revisions, checks, turns, publishing attempts) live here, never on the owner's record: the
Tape gets ids and short words only (``kinds``). The agents' side keeps its own rows
here too: the work given (``agent_work``), each tool call counted against a piece's
budget (``agent_step``), the checker's reviews (``agent_review``) and who did each step
(``credit``). Readers' replies are not here at all: they
are in the private store (``replies``). Every time is unix milliseconds, UTC. Every change
of a piece's state goes through ``move``, which asks ``states`` first.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from articles import states

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS site (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, audience TEXT NOT NULL DEFAULT '',
  register TEXT NOT NULL DEFAULT '', samples TEXT NOT NULL DEFAULT '',
  length TEXT NOT NULL DEFAULT '', headings TEXT NOT NULL DEFAULT '',
  closing TEXT NOT NULL DEFAULT '', publisher TEXT NOT NULL DEFAULT 'practice',
  wordpress_url TEXT NOT NULL DEFAULT '', wordpress_user TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lesson (
  id INTEGER PRIMARY KEY, site_id INTEGER, piece_id INTEGER, kind TEXT NOT NULL
  CHECK (kind IN ('decline', 'send_back', 'edit')), reason TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS piece (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL, way TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL, angle TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, came_from TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '', round INTEGER NOT NULL DEFAULT 0,
  passes INTEGER NOT NULL DEFAULT 0, step_started INTEGER, deadline_ms INTEGER,
  writing_day TEXT NOT NULL DEFAULT '', coverage TEXT NOT NULL DEFAULT '{}',
  phrasings TEXT NOT NULL DEFAULT '[]', candidates TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '[]', outline TEXT NOT NULL DEFAULT '{}',
  revision INTEGER NOT NULL DEFAULT 0, card_id TEXT, owner_note TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, n INTEGER, url TEXT NOT NULL,
  final_url TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
  sentences TEXT NOT NULL DEFAULT '[]', kept INTEGER NOT NULL, weak INTEGER NOT NULL DEFAULT 0,
  why TEXT NOT NULL, score REAL NOT NULL DEFAULT 0, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS revision (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, n INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('draft', 'repair', 'edit', 'owner_note')),
  title TEXT NOT NULL, body TEXT NOT NULL, aside TEXT NOT NULL DEFAULT '[]',
  kept INTEGER, open_count INTEGER, changes TEXT NOT NULL DEFAULT '[]',
  review TEXT, review_aside INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL,
  UNIQUE (piece_id, n));
CREATE TABLE IF NOT EXISTS check_result (
  id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL, name TEXT NOT NULL,
  key TEXT NOT NULL, level TEXT NOT NULL CHECK (level IN ('ok', 'warn', 'ask')),
  detail TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]', answer TEXT, answered INTEGER);
CREATE TABLE IF NOT EXISTS turn (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, step TEXT NOT NULL, role TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '', local INTEGER, gloss TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS attempt (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, publisher TEXT NOT NULL,
  started INTEGER NOT NULL, finished INTEGER,
  outcome TEXT NOT NULL CHECK (outcome IN ('pending', 'sent', 'refused', 'unsure', 'cleared')),
  detail TEXT NOT NULL DEFAULT '', external TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS practice_log (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, would_send TEXT NOT NULL,
  created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS agent_work (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, step TEXT NOT NULL, role TEXT NOT NULL,
  work_id TEXT, state TEXT NOT NULL DEFAULT 'open', answered INTEGER,
  reminded INTEGER NOT NULL DEFAULT 0, card_id TEXT, agent_label TEXT,
  why TEXT NOT NULL DEFAULT '', given INTEGER NOT NULL, ended INTEGER);
CREATE TABLE IF NOT EXISTS agent_step (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, agent TEXT NOT NULL, role TEXT,
  tool TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS agent_review (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, revision_id INTEGER NOT NULL,
  agent TEXT NOT NULL, verdict TEXT NOT NULL, kept INTEGER NOT NULL DEFAULT 0,
  aside INTEGER NOT NULL DEFAULT 0, used INTEGER, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS credit (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, step TEXT NOT NULL, by TEXT NOT NULL
  CHECK (by IN ('agent', 'plain', 'owner')), role TEXT NOT NULL DEFAULT '', label TEXT,
  agent TEXT, note TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS source_piece ON source (piece_id, kept, n);
CREATE INDEX IF NOT EXISTS agent_work_piece ON agent_work (piece_id, step);
CREATE INDEX IF NOT EXISTS agent_step_piece ON agent_step (piece_id, tool);
CREATE INDEX IF NOT EXISTS check_revision ON check_result (revision_id);
CREATE INDEX IF NOT EXISTS piece_site ON piece (site_id, state);
"""

# Columns added after a table was first made: (table, column, definition). A database made
# before them gains each when it is opened; nothing already in it changes.
ADDED_COLUMNS = (
    ("site", "timezone", "TEXT NOT NULL DEFAULT ''"),
)

_JSON = {"coverage", "phrasings", "candidates", "summary", "outline", "sentences", "aside",
         "changes", "review", "evidence", "data", "external", "would_send"}


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
        for table, column, definition in ADDED_COLUMNS:
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.conn.close()

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

    def site(self, site_id: Any) -> dict[str, Any] | None:
        return self.one("SELECT * FROM site WHERE id=?", (site_id,))

    def sites(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM site ORDER BY name")

    def lessons(self, site_id: int | None, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM lesson WHERE site_id IS NULL OR site_id=? "
                        "ORDER BY id DESC LIMIT ?", (site_id, limit))

    # --- work -----------------------------------------------------------------------------

    def piece(self, piece_id: Any) -> dict[str, Any] | None:
        return self.one("SELECT * FROM piece WHERE id=?", (piece_id,))

    def sources(self, piece_id: int) -> list[dict[str, Any]]:
        """The kept sources, S1 first."""
        return self.all("SELECT * FROM source WHERE piece_id=? AND kept=1 ORDER BY n",
                        (piece_id,))

    def dropped_sources(self, piece_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT id, url, host, title, why FROM source WHERE piece_id=? AND "
                        "kept=0 ORDER BY id", (piece_id,))

    def revision(self, piece_id: int, n: int | None = None) -> dict[str, Any] | None:
        """Revision ``n``, or the piece's current one."""
        if n is None:
            piece = self.piece(piece_id)
            n = piece["revision"] if piece else 0
        return self.one("SELECT * FROM revision WHERE piece_id=? AND n=?", (piece_id, n))

    def revisions(self, piece_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM revision WHERE piece_id=? ORDER BY n", (piece_id,))

    def next_revision(self, piece_id: int) -> int:
        row = self.one("SELECT MAX(n) AS n FROM revision WHERE piece_id=?", (piece_id,))
        return int(row["n"] or 0) + 1 if row else 1

    def checks(self, revision_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM check_result WHERE revision_id=? ORDER BY id",
                        (revision_id,))

    def turns(self, piece_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM turn WHERE piece_id=? ORDER BY id", (piece_id,))

    def add_turn(self, piece_id: int, step: str, gloss: str, *, role: str = "code",
                 model: str = "", local: bool | None = None,
                 data: dict[str, Any] | None = None) -> int:
        """One human line saying what a step did."""
        return self.insert("turn", {"piece_id": piece_id, "step": step, "role": role,
                                    "model": model or "", "local": local, "gloss": gloss[:600],
                                    "data": data or {}, "created": self.now()})

    def attempts(self, piece_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM attempt WHERE piece_id=? ORDER BY id", (piece_id,))

    # --- the only way a state changes -----------------------------------------------------

    def move(self, piece_id: int, new: str, **more: Any) -> dict[str, Any]:
        piece = self.piece(piece_id)
        if piece is None:
            raise states.StateError("There is no such article. Nothing was changed.")
        states.need(piece["state"], new)
        self.update("piece", piece_id, {"state": new, "updated": self.now(), **more})
        return self.piece(piece_id) or {}
