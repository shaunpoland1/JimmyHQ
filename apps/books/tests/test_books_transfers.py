"""Transfers: one action makes two linked rows that always sum to zero, and they move,
change and go together."""

from __future__ import annotations

import pytest
from books_rig import TODAY
from jimmyhq_app import CallError


@pytest.fixture
def two(rig):
    return rig, rig.account(), rig.account("Example Savings", "savings", opening="0")


def sides(rig, txn_id):
    row = rig.store.txn(txn_id)
    other = rig.store.txn(row["transfer_id"])
    return row, other


def test_a_transfer_is_two_linked_rows_that_sum_to_zero(two):
    rig, bank, save = two
    first, second = rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY,
                                              "amount": "200.00", "memo": "rainy day"})["ids"]
    a, b = sides(rig, first)
    assert a["amount_cents"] + b["amount_cents"] == 0 and a["amount_cents"] == -20000
    assert (a["transfer_id"], b["transfer_id"]) == (second, first)
    assert a["category_id"] is None and b["category_id"] is None
    out = rig.rows(bank)[0]
    assert out["payee"] == "Transfer to Example Savings"
    assert out["category"] == "Transfer: Example Savings" and out["transfer"] is True
    assert rig.rows(save)[0]["payee"] == "Transfer from Example Bank"


def test_typing_transfer_as_the_category_makes_a_transfer(two):
    rig, bank, save = two
    rig.call("txn.add", {"account": bank, "date": TODAY, "payee": "", "deposit": "30",
                         "category": "Transfer: Example Savings"})
    assert rig.rows(save)[0]["payment"] == "30.00"
    assert rig.rows(bank)[0]["deposit"] == "30.00"
    with pytest.raises(CallError, match="no account"):
        rig.call("txn.add", {"account": bank, "date": TODAY, "payment": "1",
                             "category": "Transfer: Nowhere"})


def test_editing_one_sides_amount_or_date_changes_the_other(two):
    rig, bank, save = two
    first, _ = rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY,
                                         "amount": "200"})["ids"]
    rig.call("txn.edit", {"id": first, "payment": "75.00", "deposit": ""})
    a, b = sides(rig, first)
    assert (a["amount_cents"], b["amount_cents"]) == (-7500, 7500)
    rig.call("txn.edit", {"id": b["id"], "date": "2027-01-20"})
    a, b = sides(rig, first)
    assert a["date"] == b["date"] == "2027-01-20"
    rig.call("txn.edit", {"id": b["id"], "payment": "10", "deposit": ""})  # turned round
    a, b = sides(rig, first)
    assert (a["amount_cents"], b["amount_cents"]) == (1000, -1000)
    assert a["amount_cents"] + b["amount_cents"] == 0


def test_deleting_one_side_deletes_both(two):
    rig, bank, save = two
    first, second = rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY,
                                              "amount": "200"})["ids"]
    rig.call("txn.delete", {"id": second})
    assert rig.store.txn(first) is None and rig.store.txn(second) is None
    assert rig.rows(bank) == [] and rig.rows(save) == []


def test_a_transfer_can_be_pointed_at_another_account_or_made_a_plain_row(two):
    rig, bank, save = two
    cash = rig.account("Wallet", "cash", opening="0")
    first, _ = rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY,
                                         "amount": "20"})["ids"]
    rig.call("txn.edit", {"id": first, "category": "Transfer: Wallet"})
    assert rig.rows(save) == [] and rig.rows(cash)[0]["deposit"] == "20.00"
    rig.call("txn.edit", {"id": first, "category": "Groceries", "payee": "Corner Shop"})
    assert rig.rows(cash) == []
    assert rig.rows(bank)[0]["category"] == "Groceries" and not rig.rows(bank)[0]["transfer"]
    rig.call("txn.edit", {"id": first, "category": "Transfer: Example Savings"})
    a, b = sides(rig, first)
    assert a["amount_cents"] + b["amount_cents"] == 0 and b["account_id"] == save


def test_a_transfer_cannot_go_to_the_account_it_comes_from(two):
    rig, bank, save = two
    with pytest.raises(CallError, match="two different"):
        rig.call("transfer.add", {"from": bank, "to": bank, "date": TODAY, "amount": "1"})
    first, second = rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY,
                                              "amount": "1"})["ids"]
    with pytest.raises(CallError, match="different account"):
        rig.call("txn.edit", {"id": first, "account": save})
    a, b = sides(rig, first)
    assert a["account_id"] == bank and b["account_id"] == save


def test_a_transfer_is_never_counted_as_spending(two):
    rig, bank, save = two
    rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY, "amount": "500"})
    rig.add(bank, TODAY, payment="20", category="Groceries")
    seen = rig.call("spending", {"month": "2027-01"})
    assert seen["spent"] == "20.00" and seen["earned"] == "0.00"
