"""Importing a bank's CSV file: the mapping guessed and remembered, dates detected, a
preview first, duplicates skipped, and the whole import undone."""

from __future__ import annotations

import pytest
from books import csvin
from jimmyhq_app import CallError

BANK_FILE = (
    "Date,Description,Amount,Memo\r\n"
    "2027-01-03,Corner Shop,-12.50,milk\r\n"
    "2027-01-04,Payroll,\"1,500.00\",\r\n"
    "2027-01-05,Corner Cafe,-3.50,\r\n"
    "2027-01-05,Corner Cafe,-3.50,\r\n"
)


def preview(rig, account, text, **more):
    return rig.call("import.preview", {"account": account, "token": rig.upload(text), **more})


def commit(rig, account, text, **more):
    return rig.call("import.commit", {"account": account, "token": rig.upload(text), **more})


def test_the_mapping_is_guessed_from_the_header():
    m = csvin.guess(["Posted Date", "Description", "Debit", "Credit", "Category"])
    assert (m.date, m.payee, m.payment, m.deposit, m.category, m.amount) == (0, 1, 2, 3, 4, None)
    one = csvin.guess(["Date", "Payee", "Amount", "Debit"])
    assert one.amount == 2 and one.payment is None
    assert csvin.guess(["Posted", "Details", "Debit", "Credit"]).date == 0


def test_a_preview_shows_the_first_rows_and_saves_nothing(rig):
    bank = rig.account()
    seen = preview(rig, bank, BANK_FILE)
    assert seen["how"] == "guessed" and seen["date_format"] == "ymd" and not seen["problem"]
    assert seen["lines"] == 4 and seen["new"] == 4 and seen["duplicates"] == 0
    assert [(r["payee"], r["payment"], r["deposit"]) for r in seen["rows"][:2]] == [
        ("Corner Shop", "12.50", ""), ("Payroll", "", "1,500.00")]
    assert rig.rows(bank) == []


def test_an_import_skips_duplicates_and_counts_them(rig):
    bank = rig.account()
    rig.add(bank, "2027-01-03", "CORNER SHOP", payment="12.50")
    first = commit(rig, bank, BANK_FILE)
    assert (first["added"], first["skipped"]) == (3, 1)
    again = commit(rig, bank, BANK_FILE)
    assert (again["added"], again["skipped"]) == (0, 4)
    cafes = [r for r in rig.rows(bank) if r["payee"] == "Corner Cafe"]
    assert len(cafes) == 2  # two real coffees on one day both arrived, once


def test_undo_removes_exactly_what_that_import_added(rig):
    bank = rig.account()
    mine = rig.add(bank, "2027-01-02", "Typed by hand", payment="1")
    one = commit(rig, bank, BANK_FILE)
    two = commit(rig, bank, "Date,Description,Amount\r\n2027-01-08,Hardware Barn,-30\r\n")
    assert len(rig.rows(bank)) == 6
    said = rig.call("import.undo", {"id": one["id"]})
    assert said["removed"] == 4
    left = rig.store.all("SELECT id, import_id FROM txn ORDER BY id")
    assert [r["id"] for r in left] == [mine, max(r["id"] for r in left)]
    assert {r["import_id"] for r in left} == {None, two["id"]}
    with pytest.raises(CallError, match="not there"):
        rig.call("import.undo", {"id": one["id"]})
    assert rig.call("import.list", {"account": bank})["imports"][1]["undone"]


def test_imported_rows_arrive_cleared_unless_the_owner_says_not(rig):
    bank = rig.account()
    commit(rig, bank, BANK_FILE)
    assert {r["state"] for r in rig.rows(bank)} == {"c"}
    other = rig.account("Example Savings", "savings", opening="0")
    commit(rig, other, BANK_FILE, cleared=False)
    assert {r["state"] for r in rig.rows(other)} == {""}


def test_an_ambiguous_date_format_is_asked_and_then_used(rig):
    bank = rig.account()
    text = "Date,Description,Amount\r\n3/4/2027,Corner Shop,-1\r\n5/6/2027,Corner Shop,-2\r\n"
    seen = preview(rig, bank, text)
    assert seen["date_format"] == "" and "two ways" in seen["problem"]
    with pytest.raises(CallError, match="two ways"):
        commit(rig, bank, text)
    commit(rig, bank, text, date_format="dmy")
    assert sorted(r["date"] for r in rig.rows(bank)) == ["2027-04-03", "2027-06-05"]


def test_separate_columns_and_a_cards_flipped_sign_are_read(rig):
    card = rig.account("Example Card", "credit_card", opening="0")
    text = ("Trans Date,Merchant,Amount\r\n01/03/2027,Corner Shop,28.88\r\n"
            "01/13/2027,Payment,-100\r\n")
    seen = preview(rig, card, text, mapping={"date": 0, "payee": 1, "amount": 2, "flip": True})
    assert seen["date_format"] == "mdy"
    assert [(r["payment"], r["deposit"]) for r in seen["rows"]] == [("28.88", ""), ("", "100.00")]
    bank = rig.account()
    two = "Date,Payee,Debit,Credit\r\n2027-01-02,Corner Shop,12.00,\r\n2027-01-03,Payroll,,900\r\n"
    seen = preview(rig, bank, two)
    assert [(r["payment"], r["deposit"]) for r in seen["rows"]] == [("12.00", ""), ("", "900.00")]


def test_the_mapping_is_remembered_for_the_account(rig):
    bank = rig.account()
    text = "When,Who,How much\r\n2027-01-02,Corner Shop,-1\r\n"
    assert "date" in preview(rig, bank, text)["problem"]
    commit(rig, bank, text, mapping={"date": 0, "payee": 1, "amount": 2})
    again = preview(rig, bank, "When,Who,How much\r\n2027-01-09,Corner Shop,-4\r\n")
    assert again["how"] == "remembered" and again["new"] == 1


def test_lines_that_cannot_be_read_are_left_out_and_said(rig):
    bank = rig.account()
    text = "Date,Description,Amount\r\n2027-01-02,Corner Shop,-1\r\n2027-01-03,Odd,abc\r\n"
    seen = preview(rig, bank, text)
    assert seen["unread_count"] == 1 and "Line 3" in seen["unread"][0]
    done = commit(rig, bank, text)
    assert done["added"] == 1 and "could not be read" in done["said"]


def test_a_stray_line_with_no_date_does_not_stop_the_import(rig):
    bank = rig.account()
    text = ("Date,Description,Amount\r\n2027-01-02,Corner Shop,-1\r\n"
            "2027-01-03,Cafe,-2\r\nTotal,,-3\r\n")
    seen = preview(rig, bank, text)
    assert not seen["problem"] and seen["new"] == 2 and seen["unread_count"] == 1
    assert commit(rig, bank, text)["added"] == 2


def test_a_file_bigger_than_one_piece_arrives_whole(rig):
    bank = rig.account()
    lines = "".join(f"2027-01-{1 + i % 28:02d},Shop number {i},-{i}.00\r\n" for i in range(9000))
    text = "Date,Description,Amount\r\n" + lines
    assert len(text) > 200_000
    assert commit(rig, bank, text)["added"] == 9000


def test_categories_in_the_file_are_used_and_new_ones_added(rig):
    bank = rig.account()
    text = "Date,Description,Amount,Category\r\n2027-01-02,Corner Shop,-1,groceries\r\n" \
           "2027-01-03,Vet,-40,Pets\r\n"
    done = commit(rig, bank, text)
    assert "Pets" in done["said"]
    assert {r["category"] for r in rig.rows(bank)} == {"Groceries", "Pets"}


def test_lines_dated_before_the_opening_date_are_left_out_and_counted_not_a_failure(rig):
    bank = rig.account(opening_date="2027-01-04")
    seen = preview(rig, bank, BANK_FILE)
    assert seen["before_opening"] == 1 and seen["new"] == 3 and not seen["problem"]
    assert seen["rows"][0]["before_opening"] is True
    done = commit(rig, bank, BANK_FILE)
    assert done["added"] == 3 and done["before_opening"] == 1
    assert "1 dated before the account's opening date (2027-01-04) and was left out" in done["said"]
    assert all(r["date"] >= "2027-01-04" for r in rig.rows(bank))
