"""The arithmetic of a register, as pure functions."""

from __future__ import annotations

from datetime import date

from books import ledger


def row(i, day, cents, state="", **more):
    return {"id": i, "date": day, "amount_cents": cents, "state": state, **more}


def test_each_row_carries_the_balance_after_it_in_date_then_entry_order():
    rows = [row(3, "2027-01-02", -500), row(1, "2027-01-05", -100), row(2, "2027-01-02", 2000)]
    got = [(r["id"], b) for r, b in ledger.running(rows, 1000)]
    assert got == [(2, 3000), (3, 2500), (1, 2400)]


def test_the_balance_on_a_day_leaves_out_later_rows():
    rows = [row(1, "2027-01-01", -100), row(2, "2027-02-01", -100)]
    assert ledger.balance_on(rows, 0, "2027-01-15") == -100
    assert ledger.balance(rows, 0) == -200


def test_the_cleared_balance_counts_what_the_bank_has_seen():
    rows = [row(1, "2027-01-01", -100, "R"), row(2, "2027-01-02", -200, "c"),
            row(3, "2027-01-03", -400, ""), row(4, "2027-02-01", -800, "c")]
    assert ledger.cleared_balance(rows, 1000) == -100
    assert ledger.cleared_balance(rows, 1000, "2027-01-31") == 700
    assert ledger.reconcile_difference(700, 700) == 0
    assert ledger.reconcile_difference(650, 700) == -50


def test_a_reconcile_lists_rows_not_yet_reconciled_up_to_its_date():
    rows = [row(1, "2027-01-01", 1, "R"), row(2, "2027-01-02", 1, "c"),
            row(3, "2027-01-03", 1), row(4, "2027-02-01", 1)]
    assert [r["id"] for r in ledger.to_reconcile(rows, "2027-01-31")] == [3, 2]


def test_one_payee_however_it_was_typed():
    assert ledger.payee_key("Corner Shop") == ledger.payee_key(" corner-shop ")
    assert ledger.payee_key("CORNER SHOP") == "cornershop"


def test_an_import_skips_what_the_register_already_has_but_keeps_real_repeats():
    coffee = ledger.dup_key(1, "2027-01-05", -350, "Corner Cafe")
    tea = ledger.dup_key(1, "2027-01-05", -200, "Corner Cafe")
    have = [coffee]
    assert ledger.split_duplicates(have, [coffee, coffee, tea]) == [True, False, False]
    assert ledger.split_duplicates([coffee, coffee], [coffee, coffee]) == [True, True]
    assert ledger.dup_key(1, "2027-01-05", -350, "CORNER CAFE") == coffee
    assert ledger.dup_key(2, "2027-01-05", -350, "Corner Cafe") != coffee


def test_spending_leaves_out_transfers_and_keeps_income_apart():
    rows = [row(1, "d", -1000, category_id=5), row(2, "d", 200, category_id=5),
            row(3, "d", -999, transfer_id=9), row(4, "d", 50000, category_id=7),
            row(5, "d", -300, category_id=None), row(6, "d", 40, category_id=None)]
    spend, earn = ledger.totals_by_category(rows, income_ids={7})
    assert spend == {5: 800, ledger.UNCATEGORISED: 300}
    assert earn == {7: 50000, ledger.UNCATEGORISED: 40}


def test_months_have_their_bounds_and_their_month_before():
    assert ledger.month_bounds("2028-02") == ("2028-02-01", "2028-02-29")
    assert ledger.month_bounds("2027-12") == ("2027-12-01", "2027-12-31")
    assert ledger.previous_month("2027-01") == "2026-12"
    assert ledger.previous_month("2027-03") == "2027-02"


def test_the_forward_look_finds_the_lowest_point():
    today = date(2027, 1, 15)
    events = [(date(2027, 1, 20), -70000, "Rent"), (date(2027, 1, 25), 150000, "Pay"),
              (date(2027, 1, 10), -1000, "Overdue"), (date(2027, 3, 1), -5, "Too far")]
    look = ledger.look_ahead(50000, today, 30, events)
    assert len(look["days"]) == 31
    assert look["days"][0]["balance"] == 49000  # the overdue one counts today
    assert look["low"] == {"date": "2027-01-20", "balance": -21000}
    assert look["days"][-1]["balance"] == 129000


def test_the_lowest_point_is_the_first_day_it_is_reached():
    look = ledger.look_ahead(100, date(2027, 1, 1), 5, [])
    assert look["low"] == {"date": "2027-01-01", "balance": 100}
