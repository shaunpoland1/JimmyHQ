"""Reconciling against a statement: the difference live, finishing only at zero, and
reconciled rows locked."""

from __future__ import annotations

import pytest
from books_rig import TODAY
from jimmyhq_app import CallError


@pytest.fixture
def books(rig):
    bank = rig.account(opening="1,000.00")
    a = rig.add(bank, "2027-01-03", "Corner Shop", payment="100.00")
    b = rig.add(bank, "2027-01-05", "Payroll", deposit="500.00")
    c = rig.add(bank, "2027-01-09", "Hardware Barn", payment="40.00")
    d = rig.add(bank, "2027-01-14", "After the statement", payment="5.00")
    return rig, bank, (a, b, c, d)


def args(bank, statement="1,400.00", through="2027-01-10", **more):
    return {"account": bank, "through": through, "statement": statement, **more}


def test_the_rows_up_to_the_end_date_are_listed_and_the_difference_is_live(books):
    rig, bank, (a, b, c, d) = books
    seen = rig.call("reconcile.view", args(bank))
    assert [r["id"] for r in seen["rows"]] == [c, b, a]
    assert seen["cleared"]["text"] == "1,000.00" and seen["difference"]["text"] == "400.00"
    seen = rig.call("reconcile.tick", args(bank, id=a))
    seen = rig.call("reconcile.tick", args(bank, id=b))
    assert seen["cleared"]["text"] == "1,400.00" and seen["difference"]["zero"] is True
    assert seen["can_finish"] is True


def test_reconcile_cannot_finish_with_a_difference_that_is_not_zero(books):
    rig, bank, (a, b, c, d) = books
    rig.call("reconcile.tick", args(bank, id=a))
    with pytest.raises(CallError, match="not zero"):
        rig.call("reconcile.finish", args(bank))
    assert {rig.store.txn(i)["state"] for i in (a, b, c)} == {"c", ""}
    assert rig.call("reconcile.start", {"account": bank})["history"] == []


def test_finishing_locks_the_cleared_rows_and_records_the_reconciliation(books):
    rig, bank, (a, b, c, d) = books
    rig.call("txn.clear", {"id": d})  # cleared, but after the statement's date
    for i in (a, b):
        rig.call("reconcile.tick", args(bank, id=i))
    rig.call("reconcile.finish", args(bank))
    states = {i: rig.store.txn(i)["state"] for i in (a, b, c, d)}
    assert states == {a: "R", b: "R", c: "", d: "c"}
    past = rig.call("reconcile.start", {"account": bank})["history"]
    assert past[0]["through"] == "2027-01-10" and past[0]["statement"] == "1,400.00"
    assert past[0]["rows"] == 2
    nxt = rig.call("reconcile.view", args(bank, statement="1,355.00", through=TODAY))
    assert [r["id"] for r in nxt["rows"]] == [d, c]
    assert nxt["cleared"]["text"] == "1,395.00"


def test_changing_a_reconciled_rows_amount_asks_first(books):
    rig, bank, (a, b, c, d) = books
    for i in (a, b):
        rig.call("reconcile.tick", args(bank, id=i))
    rig.call("reconcile.finish", args(bank))
    asked = rig.call("txn.edit", {"id": a, "payment": "99", "deposit": ""})
    assert "no longer balance" in asked["needs_confirm"]
    assert rig.store.txn(a)["amount_cents"] == -10000
    rig.call("txn.edit", {"id": a, "memo": "a memo needs no question"})
    rig.call("txn.edit", {"id": a, "payment": "99", "deposit": "", "confirm": True})
    assert rig.store.txn(a)["amount_cents"] == -9900
    assert "no longer balance" in rig.call("txn.delete", {"id": b})["needs_confirm"]
    assert "no longer balance" in rig.call("txn.clear", {"id": b})["needs_confirm"]
    assert rig.store.txn(b)["state"] == "R"


def test_a_card_is_reconciled_with_the_amount_owed_as_the_statement_says_it(rig):
    card = rig.account("Example Card", "credit_card", opening="0")
    a = rig.add(card, "2027-01-03", "Corner Shop", payment="30.00")
    seen = rig.call("reconcile.tick", args(card, statement="30.00", id=a))
    assert seen["cleared"]["text"] == "30.00" and seen["difference"]["zero"] is True
    rig.call("reconcile.finish", args(card, statement="30.00"))
    assert rig.store.txn(a)["state"] == "R"


def test_a_row_outside_the_reconcile_cannot_be_ticked_in_it(books):
    rig, bank, (a, b, c, d) = books
    with pytest.raises(CallError, match="not one of"):
        rig.call("reconcile.tick", args(bank, id=d))
