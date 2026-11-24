"""Reminders that happen by themselves: posted by the job, once for each due date that has
come, never ahead, caught up to a cap and then stopped, and said so on the screens."""

from __future__ import annotations

import pytest
from books_rig import DAY, TODAY
from jimmyhq_app import CallError


def salary(rig, account, **more):
    args = {"name": "Salary", "payee": "Example Employer", "amount": "1,500.00",
            "account": account, "category": "Pay", "kind": "income", "every": "weekly",
            "next_due": TODAY, "auto": True, **more}
    return rig.call("reminder.add", args)["id"]


def listed(rig, rem_id):
    seen = rig.call("reminders")
    return next(i for i in seen["items"] + seen["done"] if i["id"] == rem_id)


def job(rig):
    return rig.core.run_job("due")


def test_a_reminder_that_happens_by_itself_is_posted_on_its_due_date_and_moved_on(rig):
    bank = rig.account()
    rem = salary(rig, bank)
    job(rig)
    rows = rig.rows(bank)
    assert [(r["date"], r["payee"], r["deposit"]) for r in rows] == [
        (TODAY, "Example Employer", "1,500.00")]
    assert rig.store.one("SELECT state FROM txn")["state"] == ""  # uncleared
    assert listed(rig, rem)["next_due"] == "2027-01-22"


def test_the_job_posts_one_due_date_only_once_however_often_it_looks(rig):
    bank = rig.account()
    salary(rig, bank)
    for _ in range(4):
        job(rig)
    assert len(rig.rows(bank)) == 1


def test_it_never_posts_a_date_in_the_future(rig):
    bank = rig.account()
    rem = salary(rig, bank, next_due="2027-01-16")
    job(rig)
    assert rig.rows(bank) == [] and listed(rig, rem)["next_due"] == "2027-01-16"
    rig.clock.advance(DAY)  # the day comes
    job(rig)
    assert [r["date"] for r in rig.rows(bank)] == ["2027-01-16"]


def test_a_missed_date_is_posted_for_the_day_it_was_due_not_for_today(rig):
    bank = rig.account()
    salary(rig, bank, next_due="2027-01-12", every="monthly")
    job(rig)
    assert [r["date"] for r in rig.rows(bank)] == ["2027-01-12"]


def test_it_catches_missed_dates_up_to_the_setting_then_stops_and_says_so(make_rig):
    rig = make_rig(settings={"auto_catch_up": 3})
    bank = rig.account(opening_date="2026-11-01")
    rem = salary(rig, bank, next_due="2026-12-04")  # six Fridays on or before today
    job(rig)
    assert [r["date"] for r in rig.rows(bank)] == ["2026-12-18", "2026-12-11", "2026-12-04"]
    seen = listed(rig, rem)
    assert seen["next_due"] == "2026-12-25" and seen["pays_itself"] is False
    assert "posted 3 missed dates by itself and stopped" in seen["auto_said"]
    assert "most it posts at once is 3" in seen["auto_said"]
    job(rig)
    job(rig)
    assert len(rig.rows(bank)) == 3  # it stays stopped: nothing more by itself


def test_a_stopped_reminder_is_free_again_once_the_owner_has_dealt_with_what_is_overdue(make_rig):
    rig = make_rig(settings={"auto_catch_up": 2})
    bank = rig.account(opening_date="2026-11-01")
    rem = salary(rig, bank, next_due="2026-12-25")  # 25 Dec, 1 Jan, 8 Jan, 15 Jan
    job(rig)
    assert listed(rig, rem)["auto_said"]
    for due in ("2027-01-08", "2027-01-15"):
        rig.call("reminder.skip", {"id": rem, "due": due})
    seen = listed(rig, rem)
    assert seen["next_due"] == "2027-01-22" and seen["auto_said"] == ""
    assert seen["pays_itself"] is True
    assert [r["date"] for r in rig.rows(bank)] == ["2027-01-01", "2026-12-25"]  # the rest skipped


def test_a_transfer_that_happens_by_itself_makes_the_linked_pair(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    rem = rig.call("reminder.add", {"name": "Save for tax", "amount": "250", "kind": "transfer",
                                    "account": bank, "to_account": save, "every": "monthly",
                                    "next_due": TODAY, "auto": True})["id"]
    job(rig)
    out, into = rig.rows(bank)[0], rig.rows(save)[0]
    assert (out["payment"], out["transfer"]) == ("250.00", True)
    assert (into["deposit"], into["transfer"]) == ("250.00", True)
    pair = rig.store.all("SELECT * FROM txn ORDER BY id")
    assert pair[0]["transfer_id"] == pair[1]["id"] and {p["state"] for p in pair} == {""}
    assert listed(rig, rem)["next_due"] == "2027-02-15"


def test_one_paid_another_way_that_happens_by_itself_just_moves_on_and_adds_no_row(rig):
    rig.account()
    rem = rig.call("reminder.add", {"name": "Cash rent", "amount": "300", "kind": "bill",
                                    "paid_other": True, "every": "monthly", "next_due": TODAY,
                                    "auto": True})["id"]
    job(rig)
    assert rig.store.one("SELECT count(*) AS n FROM txn")["n"] == 0
    assert listed(rig, rem)["next_due"] == "2027-02-15"
    assert [(e["name"], e["paid_other"]) for e in rig.call("reminders")["auto_recent"]] == [
        ("Cash rent", True)]


def test_an_amount_that_is_only_about_this_much_is_never_posted_by_itself(rig):
    bank = rig.account()
    with pytest.raises(CallError, match="only about this much cannot happen by itself.*Nothing"):
        salary(rig, bank, about=True)
    rem = salary(rig, bank)
    rig.store.run("UPDATE reminder SET about=1 WHERE id=?", (rem,))  # as a hand-edited store
    rig.store.conn.commit()
    job(rig)
    assert rig.rows(bank) == [] and listed(rig, rem)["pays_itself"] is False


def test_a_reminder_about_this_much_still_raises_its_card_but_one_that_pays_itself_does_not(rig):
    bank = rig.account()
    salary(rig, bank, name="Rent", kind="bill", category="Home", next_due="2027-01-16")
    salary(rig, bank, name="Water bill", kind="bill", category="Utilities", auto=False,
           next_due="2027-01-16")
    job(rig)
    assert [c["title"] for c in rig.cards()] == ["Water bill is due tomorrow."]


def test_a_stopped_bill_raises_its_card_like_any_other(make_rig):
    rig = make_rig(settings={"auto_catch_up": 1})
    bank = rig.account()
    salary(rig, bank, name="Rent", kind="bill", category="Home", next_due="2027-01-01")
    job(rig)
    assert [c["title"] for c in rig.cards()] == ["Rent was due on 8 January."]


def test_a_card_filed_before_the_bill_was_marked_is_taken_away_when_it_is(rig):
    bank = rig.account()
    rem = salary(rig, bank, name="Rent", kind="bill", category="Home", auto=False,
                 next_due="2027-01-16")
    job(rig)
    assert len(rig.cards()) == 1
    rig.call("reminder.edit", {"id": rem, "name": "Rent", "amount": "1,500.00", "kind": "bill",
                               "account": bank, "category": "Home", "every": "weekly",
                               "next_due": "2027-01-16", "auto": True})
    assert rig.cards() == []


def test_the_whole_thing_can_be_switched_off_and_then_every_reminder_waits_for_you(make_rig):
    rig = make_rig(settings={"auto_post": False})
    bank = rig.account()
    rem = salary(rig, bank, kind="bill", category="Home", next_due="2027-01-16")
    rig.clock.advance(DAY)
    job(rig)
    assert rig.rows(bank) == [] and listed(rig, rem)["pays_itself"] is False
    assert len(rig.cards()) == 1


def test_a_date_pressed_by_hand_first_is_not_posted_again_by_the_job(rig):
    bank = rig.account()
    rem = salary(rig, bank)
    rig.call("reminder.post", {"id": rem, "due": TODAY})
    job(rig)
    assert len(rig.rows(bank)) == 1


def test_pressing_post_after_the_job_has_posted_it_changes_nothing(rig):
    bank = rig.account()
    rem = salary(rig, bank)
    job(rig)
    with pytest.raises(CallError, match="already posted or skipped"):
        rig.call("reminder.post", {"id": rem, "due": TODAY})
    assert len(rig.rows(bank)) == 1


def test_a_date_that_cannot_be_posted_stops_it_and_says_why_and_it_is_tried_again(rig):
    bank = rig.account()
    rem = salary(rig, bank)
    rig.call("account.edit", {"id": bank, "closed": True})
    job(rig)
    seen = listed(rig, rem)
    assert seen["next_due"] == TODAY and "could not post it by itself for 2027-01-15" in (
        seen["auto_said"])
    assert "closed" in seen["auto_said"] and rig.rows(bank) == []
    rig.call("account.edit", {"id": bank, "closed": False})
    job(rig)
    assert len(rig.rows(bank)) == 1 and listed(rig, rem)["auto_said"] == ""


def test_what_was_posted_by_itself_is_noted_on_the_bills_screen_and_in_ahead(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    salary(rig, bank)
    job(rig)
    bills = rig.call("reminders")["auto_recent"]
    assert [(e["name"], e["due"], e["on"], e["account_name"]) for e in bills] == [
        ("Salary", TODAY, TODAY, "Example Bank")]
    assert [e["name"] for e in rig.call("ahead", {"account": bank})["auto_recent"]] == ["Salary"]
    assert rig.call("ahead", {"account": save})["auto_recent"] == []  # not this account's


def test_the_note_of_what_was_posted_fades_after_the_days_the_owner_looks_back(rig):
    bank = rig.account()
    salary(rig, bank, every="yearly")
    job(rig)
    rig.clock.advance(15 * DAY)  # the setting "within (days)" is 14
    assert rig.call("reminders")["auto_recent"] == []
    rig.clock.advance(80 * DAY)
    job(rig)
    assert rig.store.one("SELECT count(*) AS n FROM auto_post")["n"] == 0


def test_deleting_the_reminder_or_erasing_everything_removes_its_notes_too(rig):
    bank = rig.account()
    rem = salary(rig, bank)
    job(rig)
    rig.call("reminder.delete", {"id": rem})
    assert rig.store.one("SELECT count(*) AS n FROM auto_post")["n"] == 0
    other = salary(rig, bank, name="Bonus")
    job(rig)
    assert other and rig.store.one("SELECT count(*) AS n FROM auto_post")["n"] == 1
    rig.call("everything.erase", {"confirm": True})
    assert rig.store.one("SELECT count(*) AS n FROM auto_post")["n"] == 0


def test_posting_by_itself_writes_nothing_on_the_record_and_asks_no_one(rig):
    bank = rig.account()
    salary(rig, bank)
    job(rig)
    assert not [name for name in rig.asked if name.split(".")[0] in ("tape", "model", "net")]
