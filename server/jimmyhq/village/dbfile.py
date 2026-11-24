"""``village.db``: one file that three parts of the village keep tables in, with one
schema version for all of them.

- ``log.py``: ``events``, the village event log.
- ``roster.py``: ``commands``, what the web process asks the village process to do, and
  ``cursors``, where the village process has read the Tape to.
- ``comms/benchlink.py``: ``cards``, the village's cards on the Bench, and ``task_refs``.

A SQLite file has one ``user_version``, so the version belongs to the file, not to any one
of them, and it is kept here. Whichever of the three opens the file first makes every
table, and a change to any of these tables is a new step on ``STORE``'s one list (see
``storeversion``).
"""

from __future__ import annotations

from jimmyhq import storeversion

FILE_NAME = "village.db"

_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    agent TEXT,
    kind  TEXT NOT NULL,
    data  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_agent_ts ON events (agent, ts_ms);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts_ms);
"""

_COMMANDS = """
CREATE TABLE IF NOT EXISTS cursors (
    name TEXT PRIMARY KEY,
    seq  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    agent   TEXT,
    payload TEXT NOT NULL,
    "by"    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    done_ts INTEGER,
    result  TEXT
);
CREATE INDEX IF NOT EXISTS commands_open ON commands (done_ts, ts);
"""

_CARDS = """
CREATE TABLE IF NOT EXISTS cards (
    card_id     TEXT PRIMARY KEY,
    agent       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    first_title TEXT NOT NULL,
    paths       TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'open',
    note        TEXT NOT NULL DEFAULT '',
    urgent      INTEGER NOT NULL DEFAULT 0,
    filed_ts    INTEGER NOT NULL,
    decided_ts  INTEGER,
    extra       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS cards_agent ON cards (agent, kind, status);
CREATE TABLE IF NOT EXISTS task_refs (
    agent   TEXT NOT NULL,
    ref     TEXT NOT NULL,
    task_id TEXT NOT NULL,
    PRIMARY KEY (agent, ref),
    UNIQUE (agent, task_id)
);
"""

STORE = storeversion.declare("village", _EVENTS + _COMMANDS + _CARDS, files=(FILE_NAME,))
