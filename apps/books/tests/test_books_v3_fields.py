"""What Books 0.3.0 adds to what a person writes down: a debt's kind, a reminder's note and
its "happens by itself" flag, in the screens and in the everything file; plus the small
sentences that changed (a rate that is not a percentage, a target date that has passed,
check numbers that were cut)."""

from __future__ import annotations

import copy
import json

import pytest
from books_rig import TODAY
from jimmyhq_app import CallError
from test_books_backup import fetch
from test_books_everything_v2 import bring, v2


def add_debt(rig, name="Example Card", **more):
    return rig.call("debt.add", {"name": name, **more})


def debt_items(rig):
    return {i["name"]: i for i in rig.call("debts")["items"]}


def out_of(rig) -> dict:
    return json.loads(fetch(rig, what="json")["text"])


# --- a debt's kind --------------------------------------------------------------------------


def test_a_debt_has_a_kind_which_is_other_unless_the_owner_says(rig):
    add_debt(rig)
    add_debt(rig, "Example Home Loan", kind="mortgage")
    items = debt_items(rig)
    assert (items["Example Card"]["kind"], items["Example Card"]["kind_words"]) == (
        "other", "Other")
    assert items["Example Home Loan"]["kind_words"] == "Mortgage"
    assert [k["id"] for k in rig.call("debts")["kinds"]] == [
        "card", "loan", "mortgage", "heloc", "other"]


def test_a_kind_that_is_not_one_of_the_five_is_refused_and_nothing_is_added(rig):
    with pytest.raises(CallError, match="card, loan, mortgage, heloc or other.*Nothing"):
        add_debt(rig, kind="boat")
    assert rig.call("debts")["items"] == []


def test_changing_a_debt_without_naming_a_kind_keeps_the_kind_it_had(rig):
    made = add_debt(rig, kind="loan", balance="900")["id"]
    rig.call("debt.edit", {"id": made, "name": "Example Card", "note": "x"})
    assert debt_items(rig)["Example Card"]["kind"] == "loan"
    rig.call("debt.edit", {"id": made, "name": "Example Card", "kind": "heloc"})
    assert debt_items(rig)["Example Card"]["kind"] == "heloc"


def test_debts_are_grouped_by_kind_with_what_is_owed_in_each_group(rig):
    add_debt(rig, "Card A", kind="card", balance="100")
    add_debt(rig, "Card B", kind="card", balance="50.50")
    add_debt(rig, "Loan A", kind="loan", balance="1,000")
    add_debt(rig, "Mystery", kind="loan")
    closed = add_debt(rig, "Old Card", kind="card", balance="999")["id"]
    rig.call("debt.edit", {"id": closed, "name": "Old Card", "closed": True})
    groups = rig.call("debts")["groups"]
    assert [(g["kind"], g["words"], g["count"], g["total"], g["unknown"]) for g in groups] == [
        ("card", "Credit card", 2, "150.50", 0), ("loan", "Loan", 2, "1,000.00", 1)]


# --- the sentences that changed -------------------------------------------------------------


def test_a_rate_that_is_not_a_number_says_it_is_not_a_percentage(rig):
    with pytest.raises(CallError, match="\"lots\" is not a percentage.*such as 19.99"):
        add_debt(rig, rate="lots")
    with pytest.raises(CallError, match="is not a percentage"):
        add_debt(rig, rate="1.2.3")
    with pytest.raises(CallError, match="percent from 0 to 100"):
        add_debt(rig, rate="101")
    assert "not an amount" not in str(add_debt(rig, rate="19.99"))


def test_a_rate_in_the_comma_style_is_shown_the_way_it_is_typed(make_rig):
    rig = make_rig(settings={"number_style": "comma"})
    with pytest.raises(CallError, match="not a percentage.*such as 19,99"):
        add_debt(rig, rate="x")


def test_a_target_date_that_has_passed_is_allowed_and_said_so(rig):
    said = add_debt(rig, target_date="2026-12-31")
    assert said["target_passed"] is True
    assert "The date to pay it off by (2026-12-31) has passed." in said["said"]
    assert debt_items(rig)["Example Card"]["target_date"] == "2026-12-31"
    edit = rig.call("debt.edit", {"id": said["id"], "name": "Example Card",
                                  "target_date": "2026-12-30"})
    assert "(2026-12-30) has passed" in edit["said"] and edit["target_passed"] is True


def test_a_target_date_today_or_later_is_not_flagged(rig):
    for day in (TODAY, "2030-01-01", ""):
        said = add_debt(rig, f"Debt {day or 'none'}", target_date=day)
        assert said["target_passed"] is False and "has passed" not in said["said"]


def test_check_numbers_cut_to_twenty_characters_are_counted_in_the_preview_and_the_result(rig):
    bank = rig.account()
    long = "123456789012345678901234"  # 24 characters
    text = ("Date,Description,Amount,Check Number\r\n"
            f"2027-01-10,Plumber,-90.00,{long}\r\n"
            "2027-01-11,Baker,-5.00,1002\r\n"
            f"2027-01-12,Joiner,-7.00,{long}\r\n")
    preview = rig.call("import.preview", {"account": bank, "token": rig.upload(text)})
    assert preview["numbers_cut"] == 2
    done = rig.call("import.commit", {"account": bank, "token": rig.upload(text)})
    assert done["numbers_cut"] == 2
    assert "2 check numbers were longer than 20 characters and were cut to 20." in done["said"]
    assert {r["number"] for r in rig.rows(bank)} == {long[:20], "1002"}


def test_one_cut_check_number_is_said_in_the_singular_and_none_says_nothing(rig):
    bank = rig.account()
    one = f"Date,Description,Amount,Check Number\r\n2027-01-10,Plumber,-90.00,{'9' * 21}\r\n"
    done = rig.call("import.commit", {"account": bank, "token": rig.upload(one)})
    assert "1 check number was longer than 20 characters and was cut to 20." in done["said"]
    other = rig.account("Example Savings", "savings", opening="0")
    plain = "Date,Description,Amount,Check Number\r\n2027-01-10,Plumber,-90.00,2001\r\n"
    said = rig.call("import.commit", {"account": other, "token": rig.upload(plain)})
    assert "cut" not in said["said"] and said["numbers_cut"] == 0


def test_a_cut_number_on_a_row_already_in_the_account_is_not_counted(rig):
    bank = rig.account()
    text = f"Date,Description,Amount,Check Number\r\n2027-01-10,Plumber,-90.00,{'7' * 25}\r\n"
    rig.call("import.commit", {"account": bank, "token": rig.upload(text)})
    again = rig.call("import.preview", {"account": bank, "token": rig.upload(text)})
    assert again["new"] == 0 and again["numbers_cut"] == 0


# --- a reminder's note ----------------------------------------------------------------------


def test_a_reminder_keeps_a_free_text_note_and_it_can_be_changed_or_cleared(rig):
    bank = rig.account()
    made = rig.call("reminder.add", {"name": "Water bill", "amount": "45", "account": bank,
                                     "kind": "bill", "every": "monthly",
                                     "next_due": "2027-01-20",
                                     "note": "Account no. is on the bill; ring first"})["id"]

    def noted():
        return next(i for i in rig.call("reminders")["items"] if i["id"] == made)["note"]
    assert noted() == "Account no. is on the bill; ring first"
    rig.call("reminder.edit", {"id": made, "name": "Water bill", "amount": "45",
                               "account": bank, "kind": "bill", "every": "monthly",
                               "next_due": "2027-01-20", "note": ""})
    assert noted() == ""


def test_a_note_longer_than_five_hundred_characters_is_refused(rig):
    bank = rig.account()
    with pytest.raises(CallError, match="at most 500 characters.*Nothing was changed"):
        rig.call("reminder.add", {"name": "Water bill", "amount": "45", "account": bank,
                                  "kind": "bill", "every": "monthly",
                                  "next_due": "2027-01-20", "note": "x" * 501})


# --- the everything file --------------------------------------------------------------------


def with_new_fields() -> dict:
    data = copy.deepcopy(v2())
    data["reminders"][0].update(auto=True, note="Pay on the 20th")  # cash rent
    data["reminders"][1].update(auto=True)  # the transfer
    data["reminders"][2].update(note="About this much")
    data["debts"][0].update(kind="card")
    data["debts"][1].update(kind="heloc")
    return data


def test_the_new_fields_come_in_and_go_out_again_unchanged(make_rig):
    first, second = make_rig(), make_rig()
    bring(first, with_new_fields())
    out = out_of(first)
    by_name = {r["name"]: r for r in out["reminders"]}
    pair = lambda name: (by_name[name]["auto"], by_name[name]["note"])  # noqa: E731
    assert pair("Cash rent") == (True, "Pay on the 20th")
    assert pair("Save for tax") == (True, "")
    assert pair("Water bill") == (False, "About this much")
    assert {d["name"]: d["kind"] for d in out["debts"]} == {
        "Example Card": "card", "Mystery": "heloc"}
    assert out["version"] == 2
    bring(second, out)
    strip = lambda d: {k: v for k, v in d.items() if k != "exported_ms"}  # noqa: E731
    assert strip(out_of(second)) == strip(out)


def test_a_version_2_file_written_before_these_fields_takes_their_defaults(rig):
    bring(rig, v2())
    out = out_of(rig)
    assert all(r["auto"] is False and r["note"] == "" for r in out["reminders"])
    assert {d["kind"] for d in out["debts"]} == {"other"}
    assert {i["kind"] for i in rig.call("debts")["items"]} == {"other"}


def test_a_null_for_the_new_optional_fields_is_the_default(rig):
    data = with_new_fields()
    data["reminders"][0].update(auto=None, note=None)
    data["debts"][0].update(kind=None)
    bring(rig, data)
    out = out_of(rig)
    cash = next(r for r in out["reminders"] if r["name"] == "Cash rent")
    assert (cash["auto"], cash["note"]) == (False, "")
    assert next(d for d in out["debts"] if d["name"] == "Example Card")["kind"] == "other"


@pytest.mark.parametrize("change, said", [
    (lambda d: d["reminders"][0].update(auto="yes"), "\"auto\" that is not true or false"),
    (lambda d: d["reminders"][0].update(auto=1), "\"auto\" that is not true or false"),
    (lambda d: d["reminders"][2].update(auto=True), "\"about\" and \"auto\" both true"),
    (lambda d: d["reminders"][0].update(note="x" * 501), "no proper \"note\""),
    (lambda d: d["reminders"][0].update(note=5), "no proper \"note\""),
    (lambda d: d["debts"][0].update(kind="boat"), "\"kind\" that is not one of card, loan"),
    (lambda d: d["debts"][0].update(kind=3), "\"kind\" that is not one of"),
])
def test_a_bad_new_field_refuses_the_whole_file(make_rig, change, said):
    rig = make_rig()
    data = with_new_fields()
    change(data)
    with pytest.raises(CallError, match=said):
        bring(rig, data)
    assert rig.call("overview")["accounts"] == []
    assert rig.store.one("SELECT count(*) AS n FROM reminder")["n"] == 0


def test_a_file_with_a_field_this_books_does_not_know_still_comes_in(rig):
    data = v2()
    data["reminders"][0]["colour"] = "green"
    assert bring(rig, data)["debts"] == 2
