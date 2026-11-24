"""Reminders with no tracked account (paid another way) and reminders that are transfers."""

from __future__ import annotations

import pytest
from books_rig import TODAY
from jimmyhq_app import CallError


def other_way(rig, **more):
    args = {"name": "Cash rent", "amount": "300", "kind": "bill", "paid_other": True,
            "every": "monthly", "next_due": "2027-01-20", **more}
    return rig.call("reminder.add", args)["id"]


def move(rig, bank, save, **more):
    args = {"name": "Save for tax", "amount": "250", "kind": "transfer", "account": bank,
            "to_account": save, "every": "monthly", "next_due": "2027-01-20", **more}
    return rig.call("reminder.add", args)["id"]


def listed(rig, rem_id):
    seen = rig.call("reminders")
    return next(i for i in seen["items"] + seen["done"] if i["id"] == rem_id)


# --- paid another way ---------------------------------------------------------------------


def test_a_reminder_can_be_paid_another_way_with_no_account(rig):
    rem = other_way(rig)
    item = listed(rig, rem)
    assert item["paid_other"] is True and item["account"] is None
    assert item["account_name"] == "Paid another way"
    assert item["next_due"] == "2027-01-20"


def test_a_reminder_not_paid_another_way_still_needs_an_account(rig):
    with pytest.raises(CallError, match="That account is not in Books.*Nothing was changed"):
        rig.call("reminder.add", {"name": "Water bill", "amount": "45", "kind": "bill",
                                  "every": "monthly", "next_due": "2027-01-20"})
    assert rig.call("reminders")["items"] == []


def test_posting_one_paid_another_way_moves_it_on_and_adds_no_row_anywhere(rig):
    bank = rig.account()
    before = rig.call("overview")["accounts"][0]["today"]["cents"]
    rem = other_way(rig)
    done = rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    assert "marked paid" in done["said"] and "2027-02-20" in done["said"]
    assert listed(rig, rem)["next_due"] == "2027-02-20"
    assert rig.rows(bank) == []
    assert rig.store.one("SELECT count(*) AS n FROM txn")["n"] == 0
    assert rig.call("overview")["accounts"][0]["today"]["cents"] == before


def test_posting_one_paid_another_way_twice_changes_nothing_the_second_time(rig):
    rem = other_way(rig)
    rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    with pytest.raises(CallError, match="already posted or skipped"):
        rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    assert listed(rig, rem)["next_due"] == "2027-02-20"


def test_one_paid_another_way_that_comes_once_is_done_after_it_is_posted(rig):
    rem = other_way(rig, every="once")
    rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    assert listed(rig, rem)["next_due"] is None


def test_the_look_ahead_lists_one_paid_another_way_as_due_and_changes_no_balance(rig):
    bank = rig.account()
    plain = rig.call("ahead", {"account": bank, "days": 40})
    other_way(rig, next_due="2027-01-20")
    seen = rig.call("ahead", {"account": bank, "days": 40})
    assert [(d["date"], d["text"]) for d in seen["days"]] == [
        (d["date"], d["text"]) for d in plain["days"]]
    assert seen["low"] == plain["low"]
    day = next(d for d in seen["days"] if d["date"] == "2027-01-20")
    assert day["events"] == [] and "other" not in day
    assert seen["other"][0] == {"date": "2027-01-20", "due": "2027-01-20", "name": "Cash rent",
                                "text": "300.00"}


def test_an_overdue_one_paid_another_way_is_listed_today_and_counts_in_the_sidebar(rig):
    bank = rig.account()
    other_way(rig, next_due="2027-01-10")
    seen = rig.call("ahead", {"account": bank, "days": 5})
    assert seen["other"][0]["name"] == "Cash rent"
    assert (seen["other"][0]["date"], seen["other"][0]["due"]) == ("2027-01-15", "2027-01-10")
    assert rig.call("reminders")["due_now"] == 1


def test_a_bill_paid_another_way_still_puts_its_name_and_date_on_the_bench_and_no_amount(rig):
    other_way(rig, next_due="2027-01-17")
    rig.core.run_job("due")
    card = rig.cards()[0]
    assert card["title"] == "Cash rent is due on 17 January."
    assert "300" not in card["title"] + card.get("body", "")


def test_changing_a_reminder_to_paid_another_way_takes_its_account_off(rig):
    bank = rig.account()
    rem = rig.call("reminder.add", {"name": "Phone", "amount": "30", "account": bank,
                                    "kind": "bill", "every": "monthly",
                                    "next_due": "2027-01-20"})["id"]
    rig.call("reminder.edit", {"id": rem, "name": "Phone", "amount": "30", "kind": "bill",
                               "paid_other": True, "every": "monthly",
                               "next_due": "2027-01-20"})
    assert listed(rig, rem)["account"] is None
    rig.call("account.delete", {"id": bank})  # nothing uses it now


def test_one_paid_another_way_is_listed_once_in_its_own_group_not_under_each_account(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    other_way(rig, next_due="2027-01-20", every="once")
    for account in (bank, save):
        seen = rig.call("ahead", {"account": account, "days": 40})
        assert [e["name"] for e in seen["other"]] == ["Cash rent"]
        assert all("other" not in d for d in seen["days"])


# --- transfers ----------------------------------------------------------------------------


def test_a_transfer_reminder_names_two_accounts_and_lists_both(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    item = listed(rig, move(rig, bank, save))
    assert (item["kind"], item["account_name"], item["to_account_name"]) == (
        "transfer", "Example Bank", "Example Savings")


def test_a_transfer_reminder_needs_two_different_open_accounts(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    with pytest.raises(CallError, match="different account"):
        move(rig, bank, bank)
    with pytest.raises(CallError, match="not in Books"):
        move(rig, bank, None)
    with pytest.raises(CallError, match="cannot be paid another way"):
        move(rig, bank, save, paid_other=True)
    assert rig.call("reminders")["items"] == []


def test_posting_a_transfer_reminder_makes_the_linked_pair_and_moves_it_on(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    rem = move(rig, bank, save, payee="Tax pot")
    rig.call("reminder.post", {"id": rem, "due": "2027-01-20", "amount": "260"})
    out, into = rig.rows(bank)[0], rig.rows(save)[0]
    assert (out["payment"], out["transfer"], out["category"]) == (
        "260.00", True, "Transfer: Example Savings")
    assert (into["deposit"], into["transfer"], into["category"]) == (
        "260.00", True, "Transfer: Example Bank")
    assert out["date"] == into["date"] == "2027-01-20" and out["payee"] == "Tax pot"
    pair = rig.store.all("SELECT * FROM txn ORDER BY id")
    assert pair[0]["transfer_id"] == pair[1]["id"] and pair[1]["transfer_id"] == pair[0]["id"]
    assert listed(rig, rem)["next_due"] == "2027-02-20"
    totals = {a["name"]: a["today"]["text"] for a in rig.call("overview")["accounts"]}
    assert totals == {"Example Bank": "1,000.00", "Example Savings": "0.00"}  # dated later
    month = rig.call("spending", {"month": "2027-01"})
    assert (month["spent"], month["earned"]) == ("0.00", "0.00")  # never counted


def test_posting_a_transfer_reminder_twice_makes_one_pair(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    rem = move(rig, bank, save, next_due="2027-01-10")
    rig.call("reminder.post", {"id": rem, "due": "2027-01-10"})
    with pytest.raises(CallError, match="already posted or skipped"):
        rig.call("reminder.post", {"id": rem, "due": "2027-01-10"})
    assert rig.store.one("SELECT count(*) AS n FROM txn")["n"] == 2
    totals = {a["name"]: a["today"]["text"] for a in rig.call("overview")["accounts"]}
    assert totals == {"Example Bank": "750.00", "Example Savings": "250.00"}


def test_deleting_one_side_of_a_posted_transfer_deletes_both(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    rem = move(rig, bank, save, next_due="2027-01-10")
    rig.call("reminder.post", {"id": rem, "due": "2027-01-10"})
    rig.call("txn.delete", {"id": rig.rows(save)[0]["id"]})
    assert rig.store.one("SELECT count(*) AS n FROM txn")["n"] == 0


def test_the_look_ahead_takes_a_transfer_out_of_one_account_and_into_the_other(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    move(rig, bank, save, next_due="2027-01-20")
    out = rig.call("ahead", {"account": bank, "days": 10})
    into = rig.call("ahead", {"account": save, "days": 10})
    assert out["days"][-1]["text"] == "750.00" and into["days"][-1]["text"] == "250.00"
    assert out["days"][5]["events"][0]["text"] == "-250.00"
    assert into["days"][5]["events"][0]["text"] == "250.00"


def test_a_transfer_reminder_posted_before_an_accounts_opening_date_is_refused(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0", opening_date="2027-02-01")
    rem = move(rig, bank, save, next_due="2027-01-20")
    with pytest.raises(CallError, match="starts on 2027-02-01"):
        rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    assert rig.store.one("SELECT count(*) AS n FROM txn")["n"] == 0
    assert listed(rig, rem)["next_due"] == "2027-01-20"


def test_an_account_a_transfer_reminder_points_at_cannot_be_deleted(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    move(rig, bank, save)
    with pytest.raises(CallError, match="still uses Example Savings"):
        rig.call("account.delete", {"id": save})


def test_a_transfer_reminder_puts_no_card_on_the_bench(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    move(rig, bank, save, next_due=TODAY)
    rig.core.run_job("due")
    assert rig.cards() == []
