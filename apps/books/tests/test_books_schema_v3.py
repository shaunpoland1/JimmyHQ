"""A store made by Books 0.2.1 (schema version 2) opens in 0.3.0 with everything kept and the
new columns at their defaults."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from books import store as store_module
from books.store import SCHEMA, STEP_2, Store


class Private:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)

    def close(self) -> None:
        self.conn.close()


def make_021(path: Path) -> None:
    """A file as 0.2.1 left it: the first schema and the first step, version 2, with rows."""
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executescript(STEP_2)
    db.execute("INSERT INTO account VALUES (1, 'Example Bank', 'checking', 100000, "
               "'2026-12-01', 0, 1, 1)")
    db.execute("INSERT INTO account VALUES (2, 'Example Savings', 'savings', 0, "
               "'2026-12-01', 0, 1, 1)")
    db.execute("INSERT INTO category VALUES (1, 'Groceries', 0, 1)")
    db.execute("INSERT INTO reminder (id, name, payee, amount_cents, about, account_id, "
               "to_account_id, paid_other, kind, every, day, day2, n, next_due, card_id, "
               "told_due, created, updated) VALUES (7, 'Water bill', 'City Water', 4500, 1, 1, "
               "NULL, 0, 'bill', 'monthly', 31, 15, 1, '2027-01-31', 'card-1', '2027-01-31', 1, 1)")
    db.execute("INSERT INTO reminder (id, name, amount_cents, account_id, to_account_id, "
               "paid_other, kind, every, next_due, created, updated) VALUES "
               "(8, 'Save for tax', 25000, 1, 2, 0, 'transfer', 'monthly', '2027-01-20', 1, 1)")
    db.execute("INSERT INTO reminder (id, name, amount_cents, account_id, paid_other, kind, "
               "every, next_due, created, updated) VALUES "
               "(9, 'Cash rent', 30000, NULL, 1, 'bill', 'monthly', '2027-01-20', 1, 1)")
    db.execute("INSERT INTO txn (id, account_id, date, payee, category_id, memo, amount_cents, "
               "state, reminder_id, posted_due, number, created, updated) VALUES "
               "(1, 1, '2026-12-31', 'City Water', 1, 'old', -4500, 'c', 7, '2026-12-31', "
               "'1042', 1, 1)")
    db.execute("INSERT INTO debt (id, name, rate_hundredths, min_cents, target_date, "
               "account_id, note, closed, created, updated) VALUES "
               "(1, 'Example Card', 1999, 2500, '2028-01-01', NULL, 'a note', 0, 1, 1)")
    db.execute("INSERT INTO debt_balance (debt_id, on_date, balance_cents, created) VALUES "
               "(1, '2027-01-01', 40000, 1)")
    db.execute("INSERT INTO debt_balance (debt_id, on_date, balance_cents, created) VALUES "
               "(1, '2027-01-10', 38000, 1)")
    db.execute("INSERT INTO meta VALUES ('seeded', 'true')")
    db.commit()
    db.close()


def test_a_store_made_by_books_0_2_1_upgrades_and_keeps_every_row(tmp_path):
    path = tmp_path / "books.db"
    make_021(path)
    store = Store(Private(path), lambda: 5)
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert [a["name"] for a in store.accounts()] == ["Example Bank", "Example Savings"]
    water = store.reminder(7)
    assert (water["name"], water["amount_cents"], water["about"], water["every"], water["day"],
            water["next_due"], water["card_id"], water["told_due"]) == (
        "Water bill", 4500, 1, "monthly", 31, "2027-01-31", "card-1", "2027-01-31")
    assert (store.reminder(8)["to_account_id"], store.reminder(9)["paid_other"],
            store.reminder(9)["account_id"]) == (2, 1, None)
    row = store.txn(1)
    assert (row["amount_cents"], row["number"], row["reminder_id"], row["posted_due"]) == (
        -4500, "1042", 7, "2026-12-31")
    debt = store.debt(1)
    assert (debt["name"], debt["rate_hundredths"], debt["min_cents"], debt["target_date"],
            debt["note"]) == ("Example Card", 1999, 2500, "2028-01-01", "a note")
    assert [(h["on_date"], h["balance_cents"]) for h in store.debt_history(1)] == [
        ("2027-01-01", 40000), ("2027-01-10", 38000)]
    assert store.get_meta("seeded") is True
    store.close()


def test_an_upgraded_reminder_and_debt_take_the_defaults_for_what_is_new(tmp_path):
    path = tmp_path / "books.db"
    make_021(path)
    store = Store(Private(path), lambda: 5)
    for rem in store.reminders():
        assert (rem["auto"], rem["note"], rem["auto_held"], rem["auto_said"]) == (0, "", 0, "")
    assert store.debt(1)["kind"] == "other"
    assert store.one("SELECT count(*) AS n FROM auto_post")["n"] == 0
    store.close()


def test_an_upgraded_store_has_the_same_shape_as_a_new_one(tmp_path):
    path = tmp_path / "books.db"
    make_021(path)
    old = Store(Private(path), lambda: 5)
    fresh = Store(Private(tmp_path / "fresh.db"), lambda: 5)

    def shape(store: Store) -> dict:
        return {t: [(c[1], c[2], c[3], c[4]) for c in store.conn.execute(f"PRAGMA table_info({t})")]
                for t in ("txn", "reminder", "debt", "debt_balance", "auto_post")}
    assert shape(old) == shape(fresh)
    old.close()
    fresh.close()


def test_a_step_that_fails_leaves_a_0_2_1_store_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "books.db"
    make_021(path)
    monkeypatch.setitem(store_module.STEPS, 3, store_module.STEPS[3].replace(
        "PRAGMA user_version = 3;", "SELECT nope FROM missing;"))
    with pytest.raises(sqlite3.OperationalError):
        Store(Private(path), lambda: 5)
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert db.execute("SELECT name FROM reminder WHERE id=7").fetchone()[0] == "Water bill"
    assert "auto" not in [c[1] for c in db.execute("PRAGMA table_info(reminder)")]
    assert "kind" not in [c[1] for c in db.execute("PRAGMA table_info(debt)")]
    db.close()
