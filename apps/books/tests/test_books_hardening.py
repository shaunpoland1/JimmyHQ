"""What a stranger's file cannot do to Books: run as a formula where the register is opened,
go past the caps an amount typed in is held to, or make a pattern slow. And that Books reads
its own export back as it wrote it."""

from __future__ import annotations

import csv
import io
import json
import time

import pytest
from books import money
from jimmyhq_app import CallError

FORMULAS = ["=HYPERLINK(\"http://x\",\"y\")", "+1+1", "-2+3", "@SUM(1+1)", "\t=1", "\r=1"]


def fetch_csv(rig, **args) -> str:
    first = rig.call("export.part", {"what": "csv", **args, "part": 0})
    text = first["text"]
    for part in range(1, first["parts"]):
        text += rig.call("export.part", {"token": first["token"], "part": part})["text"]
    return text


def document(**more) -> dict:
    base = {"format": "jimmyhq-books", "version": 2,
            "accounts": [{"id": 1, "name": "A", "kind": "checking", "opening_cents": 0,
                          "opening_date": "2027-01-01"}],
            "categories": [],
            "transactions": [{"id": 1, "account": 1, "date": "2027-01-02", "payee": "p",
                              "memo": "", "number": "", "amount_cents": -100, "state": ""}],
            "reminders": [], "debts": [], "reconciliations": []}
    return {**base, **more}


# --- the register as CSV -------------------------------------------------------------------------


@pytest.mark.parametrize("text", FORMULAS)
def test_every_text_cell_of_the_register_that_could_run_as_a_formula_is_written_as_text(
        rig, text):
    bank = rig.account()
    rig.add(bank, "2027-01-02", text, payment="1", memo=text)
    [row] = list(csv.reader(io.StringIO(fetch_csv(rig), newline="")))[1:]
    kept = rig.rows(bank)[0]
    assert row[2] == "'" + kept["payee"] and row[4] == "'" + kept["memo"]
    assert row[5] == "1.00" and row[6] == ""  # amounts are numbers and are never touched


def test_a_payee_memo_number_account_and_category_from_a_backup_are_all_guarded(make_rig):
    one = make_rig()
    nasty = "=cmd|' /C calc'!A0"
    doc = document(
        accounts=[{"id": 1, "name": "-Bank", "kind": "checking", "opening_cents": 0,
                   "opening_date": "2027-01-01"}],
        categories=[{"id": 1, "name": "@Cat", "income": False}],
        transactions=[{"id": 1, "account": 1, "date": "2027-01-02", "payee": nasty,
                       "category": 1, "memo": "+memo", "number": "-9", "amount_cents": -100,
                       "state": ""}])
    one.call("everything.import", {"token": one.upload(json.dumps(doc))})
    [row] = list(csv.reader(io.StringIO(fetch_csv(one), newline="")))[1:]
    assert row == ["'-Bank", "2027-01-02", "'" + nasty, "'@Cat", "'+memo", "1.00", "", "",
                   "'-9"]


def test_a_transfer_label_and_ordinary_text_are_not_given_an_apostrophe_they_do_not_need(rig):
    bank = rig.account()
    rig.add(bank, "2027-01-03", "Corner Shop", payment="12.50", memo="milk, eggs")
    [row] = list(csv.reader(io.StringIO(fetch_csv(rig, account=bank), newline="")))[1:]
    assert row[2] == "Corner Shop" and row[4] == "milk, eggs"


def test_books_reads_its_own_export_back_exactly_as_it_wrote_it(make_rig):
    first = make_rig()
    nasty = ["=SUM(1+1)", "+44 20 7946 0000", "-Refund", "@home", "'=already quoted",
             "'Tis fine", "''=twice"]
    doc = document(transactions=[
        {"id": i + 1, "account": 1, "date": f"2027-01-{i + 2:02d}", "payee": text,
         "memo": text, "number": text[:19], "amount_cents": -100 - i, "state": ""}
        for i, text in enumerate(nasty)])
    first.call("everything.import", {"token": first.upload(json.dumps(doc))})
    text = fetch_csv(first)
    second = make_rig()
    bank = second.account("A", opening="0", opening_date="2027-01-01")
    seen = second.call("import.commit", {"account": bank, "token": second.upload(text)})
    assert seen["added"] == len(nasty) and seen["unread"] == 0
    got = {r["date"]: r for r in second.rows(bank)}
    for i, original in enumerate(nasty):
        row = got[f"2027-01-{i + 2:02d}"]
        assert row["payee"] == original[:200] and row["memo"] == original
        assert row["number"] == original[:19]
    amounts = {r["date"]: r["amount_cents"] for r in second.store.all(
        "SELECT date, amount_cents FROM txn WHERE account_id=?", (bank,))}
    assert [amounts[f"2027-01-{i + 2:02d}"] for i in range(len(nasty))] == [
        -100 - i for i in range(len(nasty))]


def test_a_bank_file_whose_payee_starts_with_a_quote_and_a_letter_is_left_alone(rig):
    bank = rig.account()
    text = "Date,Description,Amount\r\n2027-01-03,'Tis a shop,-5.00\r\n"
    rig.call("import.commit", {"account": bank, "token": rig.upload(text)})
    assert rig.rows(bank)[0]["payee"] == "'Tis a shop"


# --- a backup past the caps ---------------------------------------------------------------------


@pytest.mark.parametrize("where,change", [
    ("opening_cents", lambda d: d["accounts"][0].update(opening_cents=2**62)),
    ("amount_cents", lambda d: d["transactions"][0].update(amount_cents=2**62)),
    ("amount_cents", lambda d: d["transactions"][0].update(amount_cents=-(2**70))),
    ("amount_cents", lambda d: d["transactions"][0].update(amount_cents=money.MOST_CENTS)),
    ("opening_cents", lambda d: d["accounts"][0].update(opening_cents=-money.MOST_CENTS)),
    ("id", lambda d: d["transactions"][0].update(id=2**64)),
])
def test_a_backup_with_an_amount_past_the_cap_is_refused_whole_and_nothing_crashes(
        rig, where, change):
    doc = document()
    change(doc)
    with pytest.raises(CallError) as caught:
        rig.call("everything.import", {"token": rig.upload(json.dumps(doc))})
    assert where in caught.value.message and "Nothing was" in caught.value.message
    assert rig.call("overview")["accounts"] == []


def test_the_other_amounts_of_a_backup_are_held_to_the_same_cap(rig):
    big = 2**62
    for change in (
        {"reminders": [{"id": 1, "name": "R", "payee": "", "amount_cents": big,
                        "account": 1, "kind": "bill", "every": "monthly", "day": 1,
                        "next_due": "2027-02-01"}]},
        {"debts": [{"id": 1, "name": "D", "kind": "other", "min_payment_cents": big}]},
        {"debts": [{"id": 1, "name": "D", "kind": "other", "balance_history": [
            {"on": "2027-01-01", "balance_cents": big}]}]},
        {"reconciliations": [{"account": 1, "through": "2027-01-02",
                              "statement_cents": big}]},
        {"reconciliations": [{"account": 1, "through": "2027-01-02", "statement_cents": 0,
                              "done_ms": 2**70}]},
    ):
        with pytest.raises(CallError, match="larger than Books keeps|no proper|below zero"):
            rig.call("everything.import", {"token": rig.upload(json.dumps(document(**change)))})
        assert rig.call("overview")["accounts"] == []


def test_the_largest_amount_books_keeps_still_comes_back_in(rig):
    top = money.MOST_CENTS - 1
    doc = document()
    doc["transactions"][0]["amount_cents"] = -top
    rig.call("everything.import", {"token": rig.upload(json.dumps(doc))})
    [account] = rig.call("overview")["accounts"]
    assert account["id"] and rig.call("register", {"account": account["id"]})["rows"]


# --- a pattern on text from outside ---------------------------------------------------------------


def test_a_csv_of_hostile_cells_is_read_in_well_under_a_second(rig):
    bank = rig.account()
    cells = ["a" * 100_000, "-" * 100_000, "'" * 100_000, ("=" + "a" * 50_000) * 2,
             "1," * 40_000, "(" * 100_000]
    lines = "".join(f"2027-01-0{n + 1},{c},{c},{c}\r\n" for n, c in enumerate(cells))
    text = "Date,Description,Amount,Memo\r\n" + lines
    start = time.perf_counter()
    rig.call("import.preview", {"account": bank, "token": rig.upload(text)})
    assert time.perf_counter() - start < 1.0
