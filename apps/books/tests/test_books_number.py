"""A row's optional check number: entered, changed, shown, searched, and in CSV in and out."""

from __future__ import annotations

import csv
import io

from books_rig import TODAY
from test_books_backup import fetch


def test_a_row_keeps_the_check_number_it_was_given_and_shows_it_in_the_register(rig):
    bank = rig.account()
    rig.call("txn.add", {"account": bank, "date": TODAY, "payee": "Landlord",
                         "payment": "800", "number": "1042"})
    rig.add(bank, TODAY, "Corner Shop", payment="5")
    rows = {r["payee"]: r for r in rig.rows(bank)}
    assert rows["Landlord"]["number"] == "1042" and rows["Corner Shop"]["number"] == ""


def test_a_check_number_can_be_changed_and_taken_off(rig):
    bank = rig.account()
    row = rig.call("txn.add", {"account": bank, "date": TODAY, "payee": "Landlord",
                               "payment": "800", "number": "1042"})["id"]
    rig.call("txn.edit", {"id": row, "number": "1043"})
    assert rig.rows(bank)[0]["number"] == "1043"
    rig.call("txn.edit", {"id": row, "number": ""})
    assert rig.rows(bank)[0]["number"] == ""


def test_a_check_number_that_is_too_long_is_refused_and_nothing_is_saved(rig):
    import pytest
    from jimmyhq_app import CallError
    bank = rig.account()
    with pytest.raises(CallError, match="at most 20 characters.*Nothing was changed"):
        rig.call("txn.add", {"account": bank, "date": TODAY, "payment": "5",
                             "number": "1" * 21})
    assert rig.rows(bank) == []


def test_search_finds_a_row_by_its_check_number(rig):
    bank = rig.account()
    rig.call("txn.add", {"account": bank, "date": TODAY, "payee": "Landlord",
                         "payment": "800", "number": "1042"})
    rig.add(bank, TODAY, "Corner Shop", payment="5")
    assert [r["payee"] for r in rig.rows(bank, q="1042")] == ["Landlord"]
    assert [r["payee"] for r in rig.rows(bank, q="104")] == ["Landlord"]


def test_the_check_number_is_in_the_csv_export_and_comes_back_through_an_import(rig):
    bank = rig.account()
    rig.call("txn.add", {"account": bank, "date": TODAY, "payee": "Landlord",
                         "payment": "800", "number": "1042"})
    text = fetch(rig, what="csv", account=bank)["text"]
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][-1] == "Number" and rows[1][-1] == "1042"
    other = rig.account("Example Savings", "savings", opening="0")
    csv_in = "Date,Description,Amount,Check Number\r\n2027-01-10,Plumber,-90.00,2001\r\n"
    preview = rig.call("import.preview", {"account": other, "token": rig.upload(csv_in)})
    assert preview["mapping"]["number"] == 3
    assert [r["number"] for r in preview["rows"]] == ["2001"]
    rig.call("import.commit", {"account": other, "token": rig.upload(csv_in)})
    assert rig.rows(other)[0]["number"] == "2001"


def test_a_transfers_check_number_belongs_to_the_side_it_was_typed_on(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    rig.call("txn.add", {"account": bank, "date": TODAY, "payment": "50",
                         "category": "Transfer: Example Savings", "number": "77"})
    assert rig.rows(bank)[0]["number"] == "77" and rig.rows(save)[0]["number"] == ""
