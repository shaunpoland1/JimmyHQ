"""Accounts and the register: entering, editing, deleting, searching and clearing rows,
with every running balance right afterwards."""

from __future__ import annotations

import pytest
from books_rig import TODAY
from jimmyhq_app import CallError


def test_an_account_shows_its_balance_today_its_cleared_balance_and_a_total(rig):
    bank = rig.account()
    rig.account("Example Card", "credit_card", opening="250.00")
    rig.add(bank, "2027-01-03", payment="100.00")
    paid = rig.add(bank, "2027-01-04", deposit="50.00")
    rig.add(bank, "2027-02-01", payment="10.00")  # dated after today
    rig.call("txn.clear", {"id": paid})
    seen = {a["name"]: a for a in rig.call("overview")["accounts"]}
    bank_now = seen["Example Bank"]
    assert bank_now["today"]["text"] == "950.00" and bank_now["ending"]["text"] == "940.00"
    assert bank_now["cleared"]["text"] == "1,050.00" and bank_now["later"] is True
    assert seen["Example Card"]["today"]["text"] == "250.00" and seen["Example Card"]["owed"]
    total = rig.call("overview")["total"]
    assert total["text"] == "700.00" and total["owe"] == "250.00" and total["have"] == "950.00"


def test_a_closed_account_is_left_out_of_the_total_and_takes_no_new_rows(rig):
    bank = rig.account()
    rig.account("Old Savings", "savings", opening="5.00")
    old = next(a for a in rig.call("overview")["accounts"] if a["name"] == "Old Savings")
    rig.call("account.edit", {"id": old["id"], "closed": True})
    assert rig.call("overview")["total"]["text"] == "1,000.00"
    with pytest.raises(CallError, match="closed"):
        rig.add(old["id"], TODAY, payment="1")
    assert bank


def test_an_account_with_rows_cannot_be_deleted_only_closed(rig):
    bank = rig.account()
    rig.add(bank, TODAY, payment="1")
    with pytest.raises(CallError, match="Close it instead"):
        rig.call("account.delete", {"id": bank})
    empty = rig.account("Spare", "cash", opening="0")
    rig.call("account.delete", {"id": empty})
    assert [a["name"] for a in rig.call("overview")["accounts"]] == ["Example Bank"]


def test_a_card_cannot_become_a_checking_account_because_every_balance_would_turn(rig):
    card = rig.account("Example Card", "credit_card", opening="0")
    with pytest.raises(CallError, match="turn over"):
        rig.call("account.edit", {"id": card, "kind": "checking"})
    rig.call("account.edit", {"id": card, "kind": "loan"})


def test_balances_are_right_after_a_row_is_put_in_the_past(rig):
    bank = rig.account()
    rig.add(bank, "2027-01-05", "A", payment="100.00")
    rig.add(bank, "2027-01-10", "B", payment="100.00")
    rig.add(bank, "2027-01-02", "Early", deposit="20.00")
    assert rig.balances(bank) == [("Early", "1,020.00"), ("A", "920.00"), ("B", "820.00")]


def test_rows_on_one_day_run_in_the_order_they_were_entered(rig):
    bank = rig.account()
    rig.add(bank, "2027-01-05", "First", payment="1.00")
    rig.add(bank, "2027-01-05", "Second", payment="2.00")
    assert rig.balances(bank) == [("First", "999.00"), ("Second", "997.00")]
    assert [r["payee"] for r in rig.rows(bank)] == ["Second", "First"]  # newest first


def test_balances_are_right_after_an_edit_and_after_a_delete(rig):
    bank = rig.account()
    a = rig.add(bank, "2027-01-05", "A", payment="100.00")
    b = rig.add(bank, "2027-01-06", "B", payment="100.00")
    rig.add(bank, "2027-01-07", "C", payment="100.00")
    rig.call("txn.edit", {"id": a, "payment": "", "deposit": "50.00"})
    assert rig.balances(bank) == [("A", "1,050.00"), ("B", "950.00"), ("C", "850.00")]
    rig.call("txn.edit", {"id": a, "date": "2027-01-09"})
    assert rig.balances(bank) == [("B", "900.00"), ("C", "800.00"), ("A", "850.00")]
    rig.call("txn.delete", {"id": b})
    assert rig.balances(bank) == [("C", "900.00"), ("A", "950.00")]


def test_a_negative_balance_is_marked(rig):
    bank = rig.account(opening="10.00")
    rig.add(bank, TODAY, payment="25.00")
    assert rig.rows(bank)[0]["neg"] is True and rig.rows(bank)[0]["balance"] == "-15.00"


def test_a_row_needs_one_amount_and_no_minus_sign(rig):
    bank = rig.account()
    for bad, words in ((dict(payment="1", deposit="2"), "not both"),
                       (dict(), "payment or a deposit"), (dict(payment="-3"), "minus"),
                       (dict(payment="1.234"), "two places")):
        with pytest.raises(CallError, match=words):
            rig.call("txn.add", {"account": bank, "date": TODAY, "payee": "X", **bad})
    with pytest.raises(CallError, match="date like"):
        rig.call("txn.add", {"account": bank, "date": "15/01/2027", "payment": "1"})
    assert rig.rows(bank) == []


def test_the_date_used_last_is_offered_for_the_next_row(rig):
    bank = rig.account()
    assert rig.call("register", {"account": bank})["last_date"] == TODAY
    rig.add(bank, "2027-01-03", payment="1")
    assert rig.call("register", {"account": bank})["last_date"] == "2027-01-03"


def test_a_payee_fills_in_the_category_and_amount_it_had_last_time(rig):
    bank = rig.account()
    card = rig.account("Example Card", "credit_card", opening="0")
    rig.add(bank, "2027-01-02", "Corner Shop", payment="12.00", category="Groceries")
    rig.add(card, "2027-01-04", "corner shop", payment="14.50", category="Eating out")
    rig.add(bank, "2027-01-03", "Payroll", deposit="900.00", category="Pay")
    rig.call("transfer.add", {"from": bank, "to": card, "date": TODAY, "amount": "5"})
    offered = rig.call("register", {"account": bank})["payees"]
    assert [p["name"] for p in offered] == ["corner shop", "Payroll"]
    assert offered[0] == {"name": "corner shop", "category": "Eating out",
                          "payment": "14.50", "deposit": ""}
    assert offered[1]["deposit"] == "900.00"


def test_a_new_category_typed_in_the_register_is_added_to_the_list(rig):
    bank = rig.account()
    said = rig.call("txn.add", {"account": bank, "date": TODAY, "payee": "Vet",
                                "payment": "40", "category": "Pets"})["said"]
    assert "Pets" in said
    assert "Pets" in rig.call("register", {"account": bank})["categories"]


def test_search_finds_payee_memo_or_amount(rig):
    bank = rig.account()
    rig.add(bank, "2027-01-02", "Corner Shop", payment="12.00")
    rig.add(bank, "2027-01-03", "Hardware Barn", payment="30.00", memo="garden hose")
    rig.add(bank, "2027-01-04", "Payroll", deposit="12.00")
    found = lambda q: sorted(r["payee"] for r in rig.rows(bank, q=q))  # noqa: E731
    assert found("corner") == ["Corner Shop"]
    assert found("HOSE") == ["Hardware Barn"]
    assert found("12") == ["Corner Shop", "Payroll"]
    assert found("12.00") == ["Corner Shop", "Payroll"]
    hit = rig.rows(bank, q="hose")[0]
    assert hit["balance"] == "958.00"  # a search never changes the balance shown


def test_one_click_clears_and_a_second_takes_it_off(rig):
    bank = rig.account()
    row = rig.add(bank, TODAY, payment="5")
    assert rig.call("txn.clear", {"id": row})["state"] == "c"
    assert rig.call("txn.clear", {"id": row})["state"] == ""


def test_the_register_shows_the_newest_rows_and_says_how_many_more(make_rig):
    rig = make_rig(settings={"register_rows": 50})
    bank = rig.account()
    with rig.store.tx():
        for i in range(60):
            rig.store.insert("txn", {"account_id": bank, "date": "2027-01-02",
                                     "payee": f"P{i}", "amount_cents": -100, "state": "",
                                     "created": 0, "updated": 0})
    seen = rig.call("register", {"account": bank})
    assert len(seen["rows"]) == 50 and seen["more"] == 10
    assert seen["rows"][0]["balance"] == "940.00"
    assert seen["rows"][-1]["balance"] == "989.00"  # worked out from the very first row
    assert len(rig.call("register", {"account": bank, "limit": 100})["rows"]) == 60


def test_categories_can_be_added_renamed_and_merged(rig):
    bank = rig.account()
    rig.add(bank, TODAY, "Vet", payment="40", category="Pets")
    rig.add(bank, TODAY, "Feed", payment="10", category="Animals")
    cats = {c["name"]: c["id"] for c in rig.call("overview")["categories"]}
    assert {"Groceries", "Pay", "Other income"} <= set(cats)
    rig.call("category.rename", {"id": cats["Animals"], "name": "Pet food"})
    said = rig.call("category.merge", {"id": cats["Animals"], "into": cats["Pets"]})["said"]
    assert "1 row moved" in said
    assert {r["category"] for r in rig.rows(bank)} == {"Pets"}
    with pytest.raises(CallError, match="already have"):
        rig.call("category.add", {"name": "pets"})
    with pytest.raises(CallError, match="Transfer"):
        rig.call("category.add", {"name": "Transfer: Savings"})


def test_a_row_dated_before_the_accounts_opening_date_is_refused_with_what_to_do(rig):
    bank = rig.account(opening_date="2027-01-10")
    with pytest.raises(CallError, match=r"Nothing was saved\. Use a date on or after 2027-01-10, "
                                        r"or change the account's opening date"):
        rig.add(bank, "2027-01-09", payment="5.00")
    assert rig.rows(bank) == []
    rig.add(bank, "2027-01-10", payment="5.00")


def test_editing_a_row_to_a_date_before_the_opening_date_is_refused_and_keeps_the_row(rig):
    bank = rig.account(opening_date="2027-01-10")
    row = rig.add(bank, "2027-01-12", payment="5.00")
    with pytest.raises(CallError, match="Nothing was saved"):
        rig.call("txn.edit", {"id": row, "date": "2027-01-02"})
    assert rig.rows(bank)[0]["date"] == "2027-01-12"


def test_a_transfer_is_refused_when_either_side_would_be_before_its_opening_date(rig):
    early = rig.account("Early", opening_date="2027-01-01")
    late = rig.account("Late", opening_date="2027-01-20")
    for args in ({"from": early, "to": late}, {"from": late, "to": early}):
        with pytest.raises(CallError, match="Late starts on 2027-01-20.*Nothing was saved"):
            rig.call("transfer.add", {**args, "date": "2027-01-05", "amount": "10"})
    with pytest.raises(CallError, match="Nothing was saved"):
        rig.call("txn.add", {"account": early, "date": "2027-01-05", "payee": "x",
                             "payment": "10", "category": "Transfer: Late"})
    assert rig.rows(early) == [] and rig.rows(late) == []
