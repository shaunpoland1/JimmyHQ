"""``apps.db``: one file that seven parts of the app platform keep tables in, with one
schema version for all of them.

- ``inbox.py``: ``inbox`` (what the core asked an app to do, kept so nothing is lost while
  an app is down), ``heartbeats`` and ``plugs``.
- ``schemas.py``: ``kind_schemas``, the event kinds full apps declared. They stay after an
  app is removed so that what it wrote on the Tape can still be read.
- ``files.py``: ``mounts``, which token opens which app version.
- ``quotas.py``: ``quota_counts``.
- ``plugs/state.py``: ``plug_jobs``, ``plug_cursors``, ``plug_told`` and ``plug_emits``.
- ``update.py``: ``app_updates``, the updates found.
- ``counts.py``: ``app_counts``, the number a full app's program last said waits on the
  owner (the count beside the app in the sidebar). Added at version 2.
- ``inbound.py``: ``inbound_keys``, ``inbound_apps`` and ``inbound_receipts``, messages
  from the owner's devices. Added at version 3; at version 4 a key may say it is for a
  sender that can only be given an address (``in_address``).

What can be made again and what cannot: ``kind_schemas`` and ``inbox`` cannot (an app's
declared kinds are read from the file, not from the app, once it is gone; a delivery
waiting for an app exists nowhere else). The rest is working state: heartbeats, plugs and
mounts come back when apps start and frames reopen, found updates at the next check, the
plug points' bookkeeping at the next tick, and quota counts start again. ``app_counts``
is working state too: a running program says its number again as soon as it starts.

A SQLite file has one ``user_version``, so the version belongs to the file and is kept
here. Whichever part opens the file first makes every table, and a change to any of these
tables is a new step on ``STORE``'s one list (see ``storeversion``).
"""

from __future__ import annotations

from jimmyhq import storeversion

FILE_NAME = "apps.db"

_INBOX = """
CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL,              -- JSON
    key TEXT,                        -- at most one unfinished delivery per (app, key)
    created INTEGER NOT NULL,        -- unix ms, UTC, as every time here
    state TEXT NOT NULL,             -- waiting | taken | answered | expired
    offered INTEGER,                 -- when it was last handed over
    offers INTEGER NOT NULL DEFAULT 0,
    answered INTEGER,
    ok INTEGER,
    result TEXT,                     -- JSON
    error TEXT
);
CREATE INDEX IF NOT EXISTS inbox_app ON inbox (app_id, state, created);
CREATE TABLE IF NOT EXISTS heartbeats (
    app_id TEXT PRIMARY KEY,
    at INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS plugs (
    app_id TEXT PRIMARY KEY,
    plugs TEXT NOT NULL,             -- JSON: what the app said it offers at start
    at INTEGER NOT NULL
) WITHOUT ROWID;
"""

_KINDS = """
CREATE TABLE IF NOT EXISTS kind_schemas (
    name TEXT NOT NULL,              -- the full kind: app.<id>.<short>
    v INTEGER NOT NULL,
    app_id TEXT NOT NULL,
    schema TEXT NOT NULL,            -- JSON Schema, as the app sent it
    example TEXT NOT NULL,
    created INTEGER NOT NULL,        -- unix ms, UTC
    PRIMARY KEY (name, v)
) WITHOUT ROWID;
"""

_MOUNTS = """
CREATE TABLE IF NOT EXISTS mounts (
    n INTEGER PRIMARY KEY AUTOINCREMENT,   -- the order they were made in
    token_hash TEXT NOT NULL UNIQUE, -- sha256 of the token; the token itself is not kept
    app_id TEXT NOT NULL,
    version TEXT NOT NULL,           -- the version folder's name: <version>-<hash8>
    created INTEGER NOT NULL         -- unix ms, UTC
);
CREATE INDEX IF NOT EXISTS mounts_app ON mounts (app_id, n);
"""

_QUOTAS = """
CREATE TABLE IF NOT EXISTS quota_counts (
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    window_start INTEGER NOT NULL,   -- unix ms, UTC
    used INTEGER NOT NULL,
    PRIMARY KEY (app_id, name)
) WITHOUT ROWID;
"""

_PLUGS = """
CREATE TABLE IF NOT EXISTS plug_jobs (
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    queued INTEGER,                  -- unix ms, UTC: when the last run was handed over
    delivery TEXT,                   -- the delivery of the run not yet finished, or NULL
    finished INTEGER,                -- when the last run ended
    ok INTEGER,                      -- how it ended: 1, 0, or NULL before the first
    error TEXT,
    PRIMARY KEY (app_id, name)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS plug_cursors (
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (app_id, name)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS plug_told (
    app_id TEXT NOT NULL,
    what TEXT NOT NULL,              -- a crash loop: "<generation>:<last exit at>"
    at INTEGER NOT NULL,
    PRIMARY KEY (app_id, what)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS plug_emits (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,              -- JSON
    at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS plug_emits_app ON plug_emits (app_id, seq);
"""

_UPDATES = """
CREATE TABLE IF NOT EXISTS app_updates (
    app_id TEXT PRIMARY KEY,
    checked_at INTEGER,          -- unix ms, UTC: when JimmyHQ last looked
    found_at INTEGER,            -- when the update below was first seen, or NULL
    source TEXT,                 -- "git" or "example", or NULL for none
    repo TEXT, ref TEXT, commit_ TEXT, example TEXT,
    version TEXT,                -- once known (a plan was made)
    needs_consent INTEGER,       -- once known
    problem TEXT                 -- why it could not be put in place by itself
);
"""

# Version 2: what each full app's program last said waits on the owner. A step, because a
# file made at version 1 has no such table and the first schema above is never edited.
_COUNTS_STEP = """
CREATE TABLE IF NOT EXISTS app_counts (
    app_id TEXT PRIMARY KEY,
    n INTEGER NOT NULL,              -- 0 to 999
    at INTEGER NOT NULL              -- unix ms, UTC: when the program last said it
) WITHOUT ROWID;
"""

# Version 3: messages from the owner's devices (``inbound.py``). The keys cannot be made
# again (only their digests are kept, and the devices hold the keys); the switch, the
# counts and the receipts are the owner's own view of the door, kept short.
_INBOUND_STEP = """
CREATE TABLE IF NOT EXISTS inbound_keys (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    label TEXT NOT NULL,             -- the owner's name for the device, given with each message
    digest TEXT NOT NULL UNIQUE,     -- sha256 of the key, never the key itself
    created INTEGER NOT NULL,        -- unix ms, UTC
    last_used INTEGER,
    uses INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS inbound_keys_app ON inbound_keys (app_id, created);
CREATE TABLE IF NOT EXISTS inbound_apps (
    app_id TEXT PRIMARY KEY,
    open INTEGER NOT NULL DEFAULT 0, -- the owner's switch on the app's page
    received INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,  -- messages the app's handler failed on
    last_error TEXT,                 -- the app's sentence for the last failure, never a body
    last_error_at INTEGER
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS inbound_receipts (
    n INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_label TEXT NOT NULL,
    at INTEGER NOT NULL,
    size INTEGER NOT NULL,           -- bytes of the body as sent, never the body
    outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inbound_receipts_app ON inbound_receipts (app_id, n);
"""

# Version 4: a key the owner made for a sender that can only be given an address, so the key
# travels in the address itself (``inbound.py``). Every key made before is a header key.
_ADDRESS_KEY_STEP = """
ALTER TABLE inbound_keys ADD COLUMN in_address INTEGER NOT NULL DEFAULT 0;
"""

# Version 5: a failed answer may carry the words JimmyHQ wrote for the owner (``owner_error``),
# which the page that asked is shown beside the program's own sentence.
_OWNER_ERROR_STEP = """
ALTER TABLE inbox ADD COLUMN owner_error TEXT;
"""

STORE = storeversion.declare(
    "apps", _INBOX + _KINDS + _MOUNTS + _QUOTAS + _PLUGS + _UPDATES,
    files=(f"apps/{FILE_NAME}",),
    steps=(_COUNTS_STEP, _INBOUND_STEP, _ADDRESS_KEY_STEP, _OWNER_ERROR_STEP))
