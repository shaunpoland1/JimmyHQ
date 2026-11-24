"""Reader's own records, in the app's own database (``reader.db`` in its data folder).

Topics (the owner's configuration), articles with their cleaned text and numbered
sentences, searches made, facts mined, articles offered to other apps (``offer``;
``withdrawn`` is when one was taken back, else NULL), what Posts said
went out (``posted``), and the researcher's rounds live here, never on the owner's
record: the Tape gets only ids and short words (``kinds``), and the offer receipt.
Every time is unix milliseconds, UTC.

An article's ``state`` says where it is:

    read     read (by Reader or the researcher), not suggested to anyone
    dropped  could not be used: unreadable, too short, or a story already suggested
    passed   read and looked at, and not picked
    new      suggested, waiting for the owner (Triage)
    kept, later, skipped, sent   what the owner chose ("sent" is Send on)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

SUGGESTED = ("new", "kept", "later", "skipped", "sent")  # shown to the owner
CHOSEN = ("kept", "later", "skipped", "sent")  # the owner's votes
STATES = ("read", "dropped", "passed", *SUGGESTED)
OUTCOMES = ("list", "bench", "memory", "offer")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topic (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, phrasings TEXT NOT NULL DEFAULT '[]',
  audience TEXT NOT NULL DEFAULT '', outcomes TEXT NOT NULL DEFAULT '["list"]',
  researcher INTEGER NOT NULL DEFAULT 1, paused INTEGER NOT NULL DEFAULT 0,
  next_phrasing INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL,
  updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS article (
  id INTEGER PRIMARY KEY, url TEXT NOT NULL, final_url TEXT NOT NULL DEFAULT '',
  host TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', topic_id INTEGER,
  phrasing TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT '',
  why TEXT NOT NULL DEFAULT '', by TEXT NOT NULL DEFAULT 'plain', agent TEXT,
  state TEXT NOT NULL, said TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
  sentences TEXT NOT NULL DEFAULT '[]', vector TEXT, vector_model TEXT,
  runs TEXT NOT NULL DEFAULT '[]', seen TEXT NOT NULL DEFAULT '{}',
  outcomes TEXT NOT NULL DEFAULT '[]', pending INTEGER NOT NULL DEFAULT 0,
  card_id TEXT, card_filed INTEGER, mine_wanted INTEGER NOT NULL DEFAULT 0,
  mined INTEGER NOT NULL DEFAULT 0, offered INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '', text_deleted INTEGER, created INTEGER NOT NULL,
  suggested INTEGER, decided INTEGER, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS search (
  id INTEGER PRIMARY KEY, topic_id INTEGER, query TEXT NOT NULL, by TEXT NOT NULL,
  agent TEXT, results INTEGER NOT NULL DEFAULT 0, said TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS found (
  id INTEGER PRIMARY KEY, search_id INTEGER NOT NULL, topic_id INTEGER, url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS fact (
  id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL, sentences TEXT NOT NULL,
  text TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', by TEXT NOT NULL, agent TEXT,
  created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS offer (
  id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL UNIQUE, url TEXT NOT NULL,
  title TEXT NOT NULL, topic TEXT NOT NULL, why TEXT NOT NULL, text_ref TEXT NOT NULL,
  created INTEGER NOT NULL, withdrawn INTEGER);
CREATE TABLE IF NOT EXISTS round (
  id INTEGER PRIMARY KEY, work_id TEXT, state TEXT NOT NULL DEFAULT 'open',
  answered INTEGER, reminded INTEGER NOT NULL DEFAULT 0, card_id TEXT,
  why TEXT NOT NULL DEFAULT '', plain INTEGER NOT NULL DEFAULT 0, agent_label TEXT,
  given INTEGER NOT NULL, ended INTEGER);
CREATE TABLE IF NOT EXISTS agent_step (
  id INTEGER PRIMARY KEY, round_id INTEGER, agent TEXT NOT NULL, tool TEXT NOT NULL,
  created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS posted (
  seq INTEGER PRIMARY KEY, post INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '', runs TEXT NOT NULL DEFAULT '[]', when_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS posted_close (
  article_id INTEGER PRIMARY KEY, seq INTEGER NOT NULL, score REAL NOT NULL,
  how TEXT NOT NULL, note TEXT NOT NULL, created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS article_url ON article (url);
CREATE INDEX IF NOT EXISTS article_state ON article (state, topic_id);
CREATE INDEX IF NOT EXISTS found_url ON found (url);
CREATE INDEX IF NOT EXISTS fact_article ON fact (article_id);
"""

_JSON = {"phrasings", "outcomes", "sentences", "vector", "runs", "seen"}


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
        self.conn.execute("PRAGMA secure_delete=ON")  # deleted text is overwritten
        self.conn.executescript(SCHEMA)
        self._add_columns()
        self.conn.commit()
        self.now = clock

    def _add_columns(self) -> None:
        """Columns added since a database was first made."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(offer)").fetchall()}
        if "withdrawn" not in have:
            self.conn.execute("ALTER TABLE offer ADD COLUMN withdrawn INTEGER")

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

    # --- topics and articles --------------------------------------------------------------

    def topic(self, topic_id: int | None) -> dict[str, Any] | None:
        if topic_id is None:
            return None
        return self.one("SELECT * FROM topic WHERE id=?", (topic_id,))

    def topics(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM topic ORDER BY name COLLATE NOCASE")

    def article(self, article_id: int | None) -> dict[str, Any] | None:
        if article_id is None:
            return None
        return self.one("SELECT * FROM article WHERE id=?", (article_id,))

    def by_url(self, url: str) -> dict[str, Any] | None:
        """The article first kept at this address, or read there."""
        return self.one("SELECT * FROM article WHERE url=? OR final_url=? ORDER BY id LIMIT 1",
                        (url, url))

    def set_article(self, article_id: int, **values: Any) -> dict[str, Any]:
        self.update("article", article_id, {**values, "updated": self.now()})
        return self.article(article_id) or {}

    def suggested_since(self, since_ms: int) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(SUGGESTED))
        return self.all(f"SELECT * FROM article WHERE state IN ({marks}) AND "  # noqa: S608
                        "COALESCE(suggested, created) >= ? ORDER BY id",
                        (*SUGGESTED, since_ms))
