"""Bills and income: what is due, posting into the register once, skipping, the forward
look and the sidebar count."""

from __future__ import annotations

import pytest
from books_rig import DAY, TODAY
from jimmyhq_app import CallError


def bill(rig, account, **more):
    args = {"name": "Water bill", "payee": "City Water", "amount": "45.00", "about": True,
            "account": account, "category": "Utilities", "kind": "bill",
            "every": "monthly", "next_due": "2027-01-20", **more}
    return rig.call("reminder.add", args)["id"]


def item(rig, rem_id):
    return next(i for i in rig.call("reminders")["items"] + rig.call("reminders")["done"]
                if i["id"] == rem_id)


def test_the_list_is_sorted_by_due_date_with_overdue_and_soon_marked(rig):
    bank = rig.account()
    later = bill(rig, bank, name="Insurance", next_due="2027-03-01", every="yearly")
    soon = bill(rig, bank, name="Water bill", next_due="2027-01-20")
    late = bill(rig, bank, name="Phone", next_due="2027-01-10")
    pay = bill(rig, bank, name="Pay", kind="income", amount="1,500", next_due=TODAY,
               every="biweekly", category="Pay")
    seen = rig.call("reminders")
    assert [i["id"] for i in seen["items"]] == [late, pay, soon, later]
    assert [i["standing"] for i in seen["items"]] == ["overdue", "today", "soon", "later"]
    assert seen["due_now"] == 2 and seen["window"] == 14


def test_posting_puts_the_row_in_the_register_and_moves_the_reminder_on(rig):
    bank = rig.account()
    rem = bill(rig, bank, next_due="2027-01-31")
    rig.call("reminder.post", {"id": rem, "due": "2027-01-31", "amount": "47.12"})
    row = rig.rows(bank)[0]
    assert (row["date"], row["payee"], row["payment"], row["category"]) == (
        "2027-01-31", "City Water", "47.12", "Utilities")
    assert row["from_reminder"] is True
    assert item(rig, rem)["next_due"] == "2027-02-28"


def test_posting_a_bill_twice_by_a_double_click_changes_nothing_the_second_time(rig):
    bank = rig.account()
    rem = bill(rig, bank)
    rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    with pytest.raises(CallError, match="already posted"):
        rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    assert len(rig.rows(bank)) == 1
    assert item(rig, rem)["next_due"] == "2027-02-20"


def test_one_due_date_can_be_posted_only_once_even_past_the_page(rig):
    bank = rig.account()
    rem = bill(rig, bank)
    rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    with rig.store.tx():  # as if something had put the reminder back
        rig.store.update("reminder", rem, next_due="2027-01-20")
    with pytest.raises(CallError):
        rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    assert len(rig.rows(bank)) == 1


def test_skip_moves_the_reminder_without_posting(rig):
    bank = rig.account()
    rem = bill(rig, bank)
    rig.call("reminder.skip", {"id": rem, "due": "2027-01-20"})
    assert rig.rows(bank) == [] and item(rig, rem)["next_due"] == "2027-02-20"
    with pytest.raises(CallError):
        rig.call("reminder.skip", {"id": rem, "due": "2027-01-20"})


def test_income_posts_as_money_in_and_a_once_reminder_is_done_after(rig):
    bank = rig.account()
    rem = bill(rig, bank, name="Refund", kind="income", amount="12", every="once")
    rig.call("reminder.post", {"id": rem, "due": "2027-01-20", "date": TODAY})
    assert rig.rows(bank)[0]["deposit"] == "12.00" and rig.rows(bank)[0]["date"] == TODAY
    assert item(rig, rem)["standing"] == "done"


def test_editing_a_reminder_keeps_its_own_day_of_the_month(rig):
    bank = rig.account()
    rem = bill(rig, bank, next_due="2027-01-31")
    rig.call("reminder.skip", {"id": rem, "due": "2027-01-31"})
    shown = item(rig, rem)
    assert shown["next_due"] == "2027-02-28"
    rig.call("reminder.edit", {"id": rem, "name": "Water", "amount": "50", "account": bank,
                               "kind": "bill", "every": "monthly", "next_due": "2027-02-28"})
    rig.call("reminder.skip", {"id": rem, "due": "2027-02-28"})
    assert item(rig, rem)["next_due"] == "2027-03-31"


def test_a_twice_monthly_reminder_needs_its_second_day(rig):
    bank = rig.account()
    with pytest.raises(CallError, match="second day"):
        bill(rig, bank, every="twice_monthly")
    rem = bill(rig, bank, every="twice_monthly", next_due="2027-01-15", day2="1")
    rig.call("reminder.skip", {"id": rem, "due": "2027-01-15"})
    assert item(rig, rem)["next_due"] == "2027-02-01"


def test_the_sidebar_counts_reminders_due_today_or_overdue(rig):
    bank = rig.account()
    bill(rig, bank, next_due="2027-01-10")
    bill(rig, bank, name="Gas", next_due=TODAY)
    bill(rig, bank, name="Later", next_due="2027-01-16")
    rig.call("overview")
    assert rig.core.count == 2
    rig.clock.advance(DAY)
    rig.core.run_job("due")
    assert rig.core.count == 3


def test_the_forward_look_adds_reminders_and_later_rows_and_finds_the_lowest_point(rig):
    bank = rig.account(opening="500.00")
    bill(rig, bank, name="Rent", amount="700", next_due="2027-01-20", about=False)
    bill(rig, bank, name="Pay", kind="income", amount="1,000", next_due="2027-01-25",
         every="biweekly")
    rig.add(bank, "2027-01-17", "Later", payment="50")
    look = rig.call("ahead", {"account": bank, "days": 30})
    assert look["start"]["text"] == "500.00"
    assert look["low"] == {"date": "2027-01-20", "text": "-250.00", "neg": True}
    days = {d["date"]: d for d in look["days"]}
    assert [e["name"] for e in days["2027-01-25"]["events"]] == ["Pay"]
    assert days["2027-02-08"]["text"] == "1,750.00"
    assert len(look["days"]) == 31


def test_a_reminder_on_a_closed_or_missing_account_is_refused(rig):
    bank = rig.account()
    rig.call("account.edit", {"id": bank, "closed": True})
    with pytest.raises(CallError, match="closed"):
        bill(rig, bank)
    with pytest.raises(CallError, match="not in Books"):
        bill(rig, 999)


def test_posting_a_bill_dated_before_the_accounts_opening_date_is_refused_and_it_stays_due(rig):
    bank = rig.account(opening_date="2027-01-25")
    rem = bill(rig, bank)
    with pytest.raises(CallError, match="Nothing was saved. Use a date on or after 2027-01-25"):
        rig.call("reminder.post", {"id": rem, "due": "2027-01-20"})
    assert rig.rows(bank) == [] and item(rig, rem)["next_due"] == "2027-01-20"
