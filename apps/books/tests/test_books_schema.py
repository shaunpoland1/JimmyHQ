"""A store made by Books 0.1.2 opens in 0.2.0 with every row kept and the new shape in place."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from books import store as store_module
from books.store import SCHEMA, SCHEMA_VERSION, Store


class Private:
    """What ``Store`` needs of the SDK's private database: a connection and ``close``."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)

    def close(self) -> None:
        self.conn.close()


def make_old(path: Path) -> None:
    """A file exactly as 0.1.2 left it: the first schema, version 1, with rows."""
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("PRAGMA user_version = 1")
    db.execute("INSERT INTO account VALUES (1, 'Example Bank', 'checking', 100000, "
               "'2026-12-01', 0, 1, 1)")
    db.execute("INSERT INTO account VALUES (2, 'Example Savings', 'savings', 0, "
               "'2026-12-01', 0, 1, 1)")
    db.execute("INSERT INTO category VALUES (1, 'Groceries', 0, 1)")
    db.execute("INSERT INTO reminder VALUES (7, 'Water bill', 'City Water', 4500, 1, 1, 1, "
                "'bill', 'monthly', 31, 15, 1, '2027-01-31', 'card-1', '2027-01-31', 1, 1)")
    db.execute("INSERT INTO txn (id, account_id, date, payee, category_id, memo, amount_cents, "
               "state, reminder_id, posted_due, created, updated) VALUES "
               "(1, 1, '2026-12-31', 'City Water', 1, 'old', -4500, 'c', 7, '2026-12-31', 1, 1)")
    db.execute("INSERT INTO txn (id, account_id, date, payee, amount_cents, state, "
               "transfer_id, created, updated) VALUES (2, 1, '2027-01-02', '', -500, '', 3, 1, 1)")
    db.execute("INSERT INTO txn (id, account_id, date, payee, amount_cents, state, "
               "transfer_id, created, updated) VALUES (3, 2, '2027-01-02', '', 500, '', 2, 1, 1)")
    db.execute("INSERT INTO meta VALUES ('seeded', 'true')")
    db.commit()
    db.close()


def test_a_store_made_by_books_0_1_2_opens_and_keeps_its_rows(tmp_path):
    path = tmp_path / "books.db"
    make_old(path)
    store = Store(Private(path), lambda: 5)
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    assert [a["name"] for a in store.accounts()] == ["Example Bank", "Example Savings"]
    rem = store.reminder(7)
    assert (rem["name"], rem["amount_cents"], rem["account_id"], rem["every"], rem["day"],
            rem["next_due"], rem["card_id"], rem["told_due"]) == (
        "Water bill", 4500, 1, "monthly", 31, "2027-01-31", "card-1", "2027-01-31")
    assert (rem["to_account_id"], rem["paid_other"]) == (None, 0)
    rows = store.txns(1)
    assert [(r["id"], r["amount_cents"], r["reminder_id"], r["posted_due"], r["number"])
            for r in rows] == [(1, -4500, 7, "2026-12-31", ""), (2, -500, None, None, "")]
    assert store.txn(2)["transfer_id"] == 3 and store.txn(3)["transfer_id"] == 2
    assert store.get_meta("seeded") is True
    store.close()


def test_an_upgraded_store_takes_the_new_things_a_fresh_one_does(tmp_path):
    path = tmp_path / "books.db"
    make_old(path)
    old = Store(Private(path), lambda: 5)
    fresh = Store(Private(tmp_path / "fresh.db"), lambda: 5)

    def shape(store: Store) -> dict:
        return {t: [(c[1], c[2], c[3]) for c in store.conn.execute(f"PRAGMA table_info({t})")]
                for t in ("txn", "reminder", "debt", "debt_balance")}
    assert shape(old) == shape(fresh)
    # An upgraded store takes a reminder with no account, a transfer one and a debt.
    now = 9
    old.insert("reminder", {"name": "Cash rent", "amount_cents": 100, "account_id": None,
                            "paid_other": 1, "kind": "bill", "every": "monthly",
                            "created": now, "updated": now})
    old.insert("debt", {"name": "Example Card", "created": now, "updated": now})
    old.conn.commit()
    assert old.one("SELECT paid_other FROM reminder WHERE name='Cash rent'")["paid_other"] == 1
    old.close()
    fresh.close()


def test_a_store_opened_twice_is_not_changed_the_second_time(tmp_path):
    path = tmp_path / "books.db"
    make_old(path)
    Store(Private(path), lambda: 5).close()
    again = Store(Private(path), lambda: 5)
    assert again.reminder(7)["name"] == "Water bill"
    assert again.conn.execute("PRAGMA user_version").fetchone()[0] == 3
    again.close()


def test_a_step_that_fails_leaves_the_old_store_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "books.db"
    make_old(path)
    # break the step half way, after it has already changed the txn table
    monkeypatch.setitem(store_module.STEPS, 2, store_module.STEPS[2].replace(
        "DROP TABLE reminder;", "SELECT nope FROM missing;"))
    with pytest.raises(sqlite3.OperationalError):
        Store(Private(path), lambda: 5)
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert db.execute("SELECT name FROM reminder WHERE id=7").fetchone()[0] == "Water bill"
    assert "number" not in [c[1] for c in db.execute("PRAGMA table_info(txn)")]
    db.close()
