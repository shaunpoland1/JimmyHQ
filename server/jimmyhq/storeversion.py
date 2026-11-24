"""Schema versions for the stores that cannot be rebuilt, and the steps that bring an older
file up to date when it is opened.

A store's version is SQLite's own ``PRAGMA user_version``, kept in the file's header. Each
store declares itself once, with ``declare``:

- ``schema``: the script that made it when versions began. This is version 1. It stays
  ``CREATE ... IF NOT EXISTS`` and is never edited again.
- ``steps``: an ordered list. The first step takes a file from version 1 to 2, the next
  from 2 to 3, and so on, so this program knows version ``1 + len(steps)``. A step is a
  script or a function given the connection. A released step is never edited either: a
  change to the shape is always a new step at the end.
- ``unversioned``: what brings a file from before versions existed (version 0, tables
  already there) to the shape of version 1. These are the checks the store made by hand
  before, kept so such a file still opens.
- ``files``: where it lives in the data folder, so ``refuse_newer`` can look at every
  store before anything starts.

The store calls ``prepare(conn, store)`` each time it opens the file:

- at the latest version, nothing is done beyond reading one number;
- at version 0 (a new file, or one from before versions) the schema runs, then
  ``unversioned``, and the file is stamped 1; then the steps run as below;
- at an older version each missing step runs in order;
- at a newer version ``StoreTooNew`` is raised and nothing in the file is changed.

Each step and its stamp are one ``BEGIN IMMEDIATE`` transaction. A step that fails leaves
the file at the last version that finished, with its rows as they were, and of two
processes opening one file at once, one waits and then finds the work already done.

**One file, one version.** Where several modules keep tables in the same file, the file is
declared once, with all their tables in one schema, and every module that opens it calls
``prepare`` with that one declaration. ``village.db`` is like this: the event log, the
commands and the cards are three modules' tables, and ``village/dbfile.py`` owns the
version for all of them. A step for any of those tables goes on that one list.

An app's ``app.db`` belongs to the platform (``apps/appdata.py``): an app's own tables are
rows in it, never SQL tables, and the SDK refuses an app database named ``app``, so its
version is the platform's to keep.
"""

from __future__ import annotations

import importlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

Step = str | Callable[[sqlite3.Connection], None]

# Every module that declares a store. ``refuse_newer`` imports them all, so the check
# before start-up sees every store whether or not anything has opened it yet.
DECLARED_IN = (
    "jimmyhq.tape.store",
    "jimmyhq.auth",
    "jimmyhq.chat.offrecord",
    "jimmyhq.memory.store",
    "jimmyhq.village.dbfile",
    "jimmyhq.hands.store",
    "jimmyhq.apps.appdata",
    "jimmyhq.apps.dbfile",
)


class StoreTooNew(RuntimeError):
    """The file was written by a newer JimmyHQ than this one. Nothing in it was changed."""

    def __init__(self, path: str, found: int, known: int):
        super().__init__(
            f"{path} was written by a newer version of JimmyHQ: its layout is at version "
            f"{found} and this JimmyHQ knows up to version {known}. Nothing was changed. "
            "Update JimmyHQ to the version that wrote it, or stop JimmyHQ and put back a "
            "backup of the data folder made with this version.")
        self.path, self.found, self.known = path, found, known


@dataclass(frozen=True)
class Store:
    name: str
    schema: str
    files: tuple[str, ...]
    steps: tuple[Step, ...] = ()
    unversioned: Callable[[sqlite3.Connection], None] | None = None

    @property
    def latest(self) -> int:
        return 1 + len(self.steps)


STORES: dict[str, Store] = {}


def declare(name: str, schema: str, *, files: tuple[str, ...], steps: tuple[Step, ...] = (),
            unversioned: Callable[[sqlite3.Connection], None] | None = None) -> Store:
    """Name a store, its first schema, the files it lives in and its steps, in order."""
    if name in STORES:
        raise ValueError(f"A store named {name!r} is already declared. One file has one "
                         "version: declare it once and open it with that declaration.")
    store = STORES[name] = Store(name, schema, tuple(files), tuple(steps), unversioned)
    return store


def version_of(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _stamp(conn: sqlite3.Connection, version: int) -> None:
    # A pragma takes no bound parameter, so the number is written into the statement,
    # and only ever as an int.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def run_script(conn: sqlite3.Connection, sql: str) -> None:
    """Run a script one statement at a time. ``executescript`` would commit the open
    transaction first, and a step must stay inside its own."""
    statement = ""
    for piece in sql.split(";"):
        statement += piece + ";"
        # A ';' inside a comment or a string does not end a statement.
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip(" \t\r\n;"):
        conn.execute(statement)


def _path(conn: sqlite3.Connection) -> str:
    row = next((r for r in conn.execute("PRAGMA database_list") if r[1] == "main"), None)
    return str(row[2]) if row is not None and row[2] else "this store"


def _step(conn: sqlite3.Connection, store: Store, version: int) -> None:
    """Inside the transaction: take the file from ``version`` to ``version + 1``."""
    if version == 0:
        run_script(conn, store.schema)
        if store.unversioned is not None:
            store.unversioned(conn)
        return
    step = store.steps[version - 1]
    if isinstance(step, str):
        run_script(conn, step)
    else:
        step(conn)


def prepare(conn: sqlite3.Connection, store: Store) -> int:
    """Bring the file ``conn`` has open up to ``store.latest``, one step per transaction.
    Returns the version it was at. Raises ``StoreTooNew``, having changed nothing, if it
    was written by a newer JimmyHQ. Call it with no transaction open."""
    if conn.in_transaction:
        raise RuntimeError("prepare opens its own transactions; call it with none open.")
    found = version_of(conn)
    version = found
    while version != store.latest:
        if version > store.latest:
            raise StoreTooNew(_path(conn), version, store.latest)
        conn.execute("BEGIN IMMEDIATE")
        try:
            now = version_of(conn)  # another process may have moved it on meanwhile
            if now == version:
                _step(conn, store, now)
                _stamp(conn, now + 1)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        if now == version and now > 0:
            log.info("upgraded %s from version %d to %d", store.name, now, now + 1)
        version = version_of(conn)
    return found


def refuse_newer(data: Path) -> None:
    """Before anything starts: raise ``StoreTooNew`` for the first file in the data folder
    that a newer JimmyHQ wrote. Only reads; a file that is not there, or not a database,
    is left for its store to report when it opens."""
    for module in DECLARED_IN:
        importlib.import_module(module)
    for store in STORES.values():
        for pattern in store.files:
            for path in sorted(Path(data).glob(pattern)):
                try:
                    conn = sqlite3.connect(path)
                    try:
                        found = version_of(conn)
                    finally:
                        conn.close()
                except sqlite3.DatabaseError:
                    continue
                if found > store.latest:
                    raise StoreTooNew(str(path), found, store.latest)
