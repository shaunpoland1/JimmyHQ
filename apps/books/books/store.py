"""Books' own records, in the app's private database (``books.db`` in its data folder).

Accounts, transactions, reminders, categories, reconciliations and imports live here and
nowhere else: never on the owner's record, never in a Bench card. The database is a
private one, so a deleted row is overwritten in the file, not only unlinked.

Money is whole cents (``amount_cents``: money in above zero). A date the owner typed is
``YYYY-MM-DD``; a time is unix milliseconds, UTC. A transaction's ``state`` is ``""``,
``"c"`` (cleared) or ``"R"`` (reconciled). A transfer is two rows whose ``transfer_id``
name each other. A row posted from a reminder keeps ``reminder_id`` and ``posted_due``,
and the pair is unique, so one due date can be posted once. A row may carry a ``number``
(a check number). A reminder with no ``account_id`` is "paid another way" (``paid_other``);
one of kind ``transfer`` moves money from ``account_id`` to ``to_account_id``. A debt keeps
its dated balances in ``debt_balance`` (none means the balance is not known); its rate is
in hundredths of a percent (``rate_hundredths``, 1999 is 19.99%) and it has a ``kind``
(card, loan, mortgage, heloc or other). A reminder with ``auto`` set is posted by Books
itself on or after each due date (see ``auto.py``); ``auto_post`` lists what it posted.

Every change that touches money runs inside ``with store.tx():``, which commits all of
it or none of it.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from typing import Any

SCHEMA_VERSION = 3

# The first schema (version 1), exactly as 0.1.x made it. Never edit it: a file made by
# 0.1.x has these tables, and ``STEPS`` takes it from there. A new file is made with this
# and then brought up by every step, so a new file and an old one end up the same.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS account (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  opening_cents INTEGER NOT NULL DEFAULT 0, opening_date TEXT NOT NULL,
  closed INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS account_name ON account (name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS category (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, income INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS category_name ON category (name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS txn (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES account (id),
  date TEXT NOT NULL, payee TEXT NOT NULL DEFAULT '',
  category_id INTEGER REFERENCES category (id), memo TEXT NOT NULL DEFAULT '',
  amount_cents INTEGER NOT NULL, state TEXT NOT NULL DEFAULT '',
  transfer_id INTEGER, reminder_id INTEGER, posted_due TEXT, import_id INTEGER,
  created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS txn_account ON txn (account_id, date, id);
CREATE UNIQUE INDEX IF NOT EXISTS txn_posted ON txn (reminder_id, posted_due)
  WHERE reminder_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS reminder (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, payee TEXT NOT NULL DEFAULT '',
  amount_cents INTEGER NOT NULL, about INTEGER NOT NULL DEFAULT 0,
  account_id INTEGER NOT NULL REFERENCES account (id),
  category_id INTEGER REFERENCES category (id), kind TEXT NOT NULL,
  every TEXT NOT NULL, day INTEGER NOT NULL DEFAULT 1, day2 INTEGER NOT NULL DEFAULT 15,
  n INTEGER NOT NULL DEFAULT 1, next_due TEXT, card_id TEXT, told_due TEXT,
  created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reconciliation (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES account (id),
  through TEXT NOT NULL, statement_cents INTEGER NOT NULL, row_count INTEGER NOT NULL,
  done_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS import_batch (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES account (id),
  name TEXT NOT NULL DEFAULT '', added INTEGER NOT NULL, skipped INTEGER NOT NULL,
  unread INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, undone INTEGER);
CREATE TABLE IF NOT EXISTS upload (
  id INTEGER PRIMARY KEY, token TEXT NOT NULL, part INTEGER NOT NULL, text TEXT NOT NULL,
  created INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS upload_part ON upload (token, part);
"""

# Version 1 to version 2 (Books 0.2.0): a check number on a row; a reminder may have no
# account (paid another way) or move money to a second account (a transfer), so the
# reminder table is made again with ``account_id`` free to be empty; debts and their
# dated balances. Rows are copied across with their ids.
STEP_2 = """
BEGIN;
ALTER TABLE txn ADD COLUMN number TEXT NOT NULL DEFAULT '';
CREATE TABLE reminder_v2 (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, payee TEXT NOT NULL DEFAULT '',
  amount_cents INTEGER NOT NULL, about INTEGER NOT NULL DEFAULT 0,
  account_id INTEGER REFERENCES account (id),
  to_account_id INTEGER REFERENCES account (id),
  paid_other INTEGER NOT NULL DEFAULT 0,
  category_id INTEGER REFERENCES category (id), kind TEXT NOT NULL,
  every TEXT NOT NULL, day INTEGER NOT NULL DEFAULT 1, day2 INTEGER NOT NULL DEFAULT 15,
  n INTEGER NOT NULL DEFAULT 1, next_due TEXT, card_id TEXT, told_due TEXT,
  created INTEGER NOT NULL, updated INTEGER NOT NULL);
INSERT INTO reminder_v2 (id, name, payee, amount_cents, about, account_id, category_id,
  kind, every, day, day2, n, next_due, card_id, told_due, created, updated)
  SELECT id, name, payee, amount_cents, about, account_id, category_id, kind, every, day,
  day2, n, next_due, card_id, told_due, created, updated FROM reminder;
DROP TABLE reminder;
ALTER TABLE reminder_v2 RENAME TO reminder;
CREATE TABLE debt (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, rate_hundredths INTEGER,
  min_cents INTEGER NOT NULL DEFAULT 0, target_date TEXT,
  account_id INTEGER REFERENCES account (id), note TEXT NOT NULL DEFAULT '',
  closed INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE debt_balance (
  id INTEGER PRIMARY KEY, debt_id INTEGER NOT NULL REFERENCES debt (id) ON DELETE CASCADE,
  on_date TEXT NOT NULL, balance_cents INTEGER NOT NULL, created INTEGER NOT NULL);
CREATE UNIQUE INDEX debt_balance_day ON debt_balance (debt_id, on_date);
PRAGMA user_version = 2;
COMMIT;
"""

# Version 2 to version 3 (Books 0.3.0): a reminder may happen by itself (``auto``, with
# ``auto_held`` and ``auto_said`` saying why Books stopped posting it) and may carry a
# note; a debt has a kind; ``auto_post`` is the short list of what Books posted by itself,
# kept so the Bills and Ahead screens can say so. Every existing row keeps its place: the
# new columns take their defaults (nothing happens by itself, no note, a debt of kind
# ``other``).
STEP_3 = """
BEGIN;
ALTER TABLE reminder ADD COLUMN auto INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reminder ADD COLUMN note TEXT NOT NULL DEFAULT '';
ALTER TABLE reminder ADD COLUMN auto_held INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reminder ADD COLUMN auto_said TEXT NOT NULL DEFAULT '';
ALTER TABLE debt ADD COLUMN kind TEXT NOT NULL DEFAULT 'other';
CREATE TABLE auto_post (
  id INTEGER PRIMARY KEY, reminder_id INTEGER NOT NULL, name TEXT NOT NULL,
  due TEXT NOT NULL, kind TEXT NOT NULL, account_id INTEGER, to_account_id INTEGER,
  paid_other INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
PRAGMA user_version = 3;
COMMIT;
"""

# ``STEPS[n]`` takes a file from version n-1 to version n. Add to the end only.
STEPS = {2: STEP_2, 3: STEP_3}

TABLES = ("txn", "reminder", "reconciliation", "import_batch", "upload", "account",
          "category", "meta", "debt", "debt_balance", "auto_post")


class Store:
    def __init__(self, private: Any, clock: Any):
        self.private = private
        self.conn: sqlite3.Connection = private.conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self.now = clock
        self.lock = threading.RLock()
        self._depth = 0

    def _migrate(self) -> None:
        """Bring a database made by an older Books up to ``SCHEMA_VERSION``: each step
        takes it from the version before. Never edit a step that has shipped."""
        have = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if have > SCHEMA_VERSION:
            raise RuntimeError("This database was made by a newer Books. Update Books "
                               "instead of opening it with an older one.")
        # A file with no version has just been made with the first schema (or is from
        # before versions were kept, which is the same shape).
        for version in range(max(have, 1) + 1, SCHEMA_VERSION + 1):
            self.conn.commit()
            try:
                self.conn.executescript(STEPS[version])
            except BaseException:
                self.conn.rollback()
                raise

    def close(self) -> None:
        self.private.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[None]:
        """One transaction: everything inside is kept together, or none of it is."""
        with self.lock:
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.rollback()
                raise
            self._depth -= 1
            if self._depth == 0:
                self.conn.commit()

    def wipe_log(self) -> None:
        """After a deletion: empty the write-ahead log so the old rows are gone from it."""
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # --- plain helpers (never commit: a caller holds ``tx``) ------------------------------

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def run(self, sql: str, params: Iterable[Any] = ()) -> int:
        return self.conn.execute(sql, tuple(params)).rowcount

    def insert(self, table: str, values: dict[str, Any]) -> int:
        if table not in TABLES:
            raise ValueError(f"{table!r} is not one of Books' tables.")
        keys = list(values)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",  # noqa: S608
            [values[k] for k in keys])
        return int(cur.lastrowid or 0)

    def update(self, table: str, row_id: int, **values: Any) -> None:
        if table not in TABLES:
            raise ValueError(f"{table!r} is not one of Books' tables.")
        if table in ("txn", "reminder", "account"):
            values["updated"] = self.now()
        sets = ", ".join(f"{k}=?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",  # noqa: S608
                          [*values.values(), row_id])

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) "
                          "DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # --- reading -------------------------------------------------------------------------

    def account(self, account_id: Any) -> dict[str, Any] | None:
        try:
            key = int(account_id)
        except (TypeError, ValueError):
            return None
        return self.one("SELECT * FROM account WHERE id=?", (key,))

    def accounts(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM account ORDER BY closed, name COLLATE NOCASE, id")

    def txn(self, txn_id: Any) -> dict[str, Any] | None:
        try:
            key = int(txn_id)
        except (TypeError, ValueError):
            return None
        return self.one("SELECT * FROM txn WHERE id=?", (key,))

    def txns(self, account_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM txn WHERE account_id=? ORDER BY date, id", (account_id,))

    def categories(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM category ORDER BY income, name COLLATE NOCASE")

    def category(self, category_id: Any) -> dict[str, Any] | None:
        try:
            key = int(category_id)
        except (TypeError, ValueError):
            return None
        return self.one("SELECT * FROM category WHERE id=?", (key,))

    def category_named(self, name: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM category WHERE name=? COLLATE NOCASE", (name.strip(),))

    def reminder(self, reminder_id: Any) -> dict[str, Any] | None:
        try:
            key = int(reminder_id)
        except (TypeError, ValueError):
            return None
        return self.one("SELECT * FROM reminder WHERE id=?", (key,))

    def debt(self, debt_id: Any) -> dict[str, Any] | None:
        try:
            key = int(debt_id)
        except (TypeError, ValueError):
            return None
        return self.one("SELECT * FROM debt WHERE id=?", (key,))

    def debt_history(self, debt_id: int) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM debt_balance WHERE debt_id=? ORDER BY on_date",
                        (debt_id,))

    def reminders(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM reminder ORDER BY next_due IS NULL, next_due, name, id")
