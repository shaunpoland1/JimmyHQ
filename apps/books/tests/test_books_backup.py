"""Files out and in: CSV exports, and everything as one JSON file that comes back whole."""

from __future__ import annotations

import csv
import io
import json

import pytest
from books_rig import TODAY
from jimmyhq_app import CallError


def fetch(rig, **args) -> dict:
    first = rig.call("export.part", {**args, "part": 0})
    text = first["text"]
    for part in range(1, first["parts"]):
        text += rig.call("export.part", {"token": first["token"], "part": part})["text"]
    return {**first, "text": text}


def filled(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    rig.add(bank, "2027-01-03", "Corner Shop", payment="12.50", category="Groceries",
            memo="milk, eggs")
    rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY, "amount": "100"})
    rig.call("reminder.add", {"name": "Water bill", "amount": "45", "account": bank,
                              "kind": "bill", "every": "monthly", "next_due": "2027-01-31"})
    return bank, save


def test_an_accounts_register_exports_as_csv_with_plain_amounts(rig):
    bank, _ = filled(rig)
    got = fetch(rig, what="csv", account=bank)
    rows = list(csv.reader(io.StringIO(got["text"])))
    assert rows[0] == ["Account", "Date", "Payee", "Category", "Memo", "Payment", "Deposit",
                       "Cleared", "Number"]
    assert rows[1] == ["Example Bank", "2027-01-03", "Corner Shop", "Groceries", "milk, eggs",
                       "12.50", "", "", ""]
    assert rows[2][2:4] == ["Transfer to Example Savings", "Transfer: Example Savings"]
    assert got["name"] == "books-Example Bank" and got["type"] == "text/csv"
    assert len(list(csv.reader(io.StringIO(fetch(rig, what="csv")["text"])))) == 4


def test_everything_goes_out_as_json_and_comes_back_whole(make_rig):
    first = make_rig()
    filled(first)
    out = fetch(first, what="json")
    data = json.loads(out["text"])
    assert data["format"] == "jimmyhq-books" and data["version"] == 2
    assert {len(data[k]) for k in ("accounts", "reminders")} == {2, 1}
    second = make_rig()
    said = second.call("everything.import", {"token": second.upload(out["text"])})
    assert (said["accounts"], said["transactions"], said["reminders"]) == (2, 3, 1)
    assert said["said"] == "Brought in 2 accounts, 3 rows and 1 reminder."
    again = json.loads(fetch(second, what="json")["text"])
    strip = lambda d: {k: v for k, v in d.items() if k != "exported_ms"}  # noqa: E731
    assert strip(again) == strip(data)
    bank = next(a for a in second.call("overview")["accounts"] if a["name"] == "Example Bank")
    assert bank["today"]["text"] == "887.50"


def test_import_everything_fills_only_an_empty_books(rig):
    filled(rig)
    text = fetch(rig, what="json")["text"]
    with pytest.raises(CallError, match="empty Books"):
        rig.call("everything.import", {"token": rig.upload(text)})


def test_one_bad_item_and_nothing_is_brought_in(make_rig):
    first = make_rig()
    filled(first)
    data = json.loads(fetch(first, what="json")["text"])
    data["transactions"][-1]["date"] = "31/01/2027"
    second = make_rig()
    with pytest.raises(CallError, match="date like"):
        second.call("everything.import", {"token": second.upload(json.dumps(data))})
    assert second.call("overview")["accounts"] == []
    assert second.call("overview")["categories"]  # the starter set is still there


def test_a_transfer_without_its_other_side_is_refused(make_rig):
    first = make_rig()
    filled(first)
    data = json.loads(fetch(first, what="json")["text"])
    data["transactions"] = [t for t in data["transactions"] if not (
        t["transfer"] and t["amount_cents"] > 0)]
    second = make_rig()
    with pytest.raises(CallError, match="other side"):
        second.call("everything.import", {"token": second.upload(json.dumps(data))})


def test_erase_everything_needs_a_yes_and_leaves_a_fresh_books(rig):
    filled(rig)
    with pytest.raises(CallError):
        rig.call("everything.erase", {})
    rig.call("everything.erase", {"confirm": True})
    seen = rig.call("overview")
    assert seen["accounts"] == [] and seen["empty"] and seen["categories"]
    raw = b"".join(p.read_bytes() for p in rig.ctx.app.data_dir.glob("books.db*"))
    assert b"Corner Shop" not in raw and b"milk, eggs" not in raw


def test_import_everything_still_takes_a_row_dated_before_its_accounts_opening_date(make_rig):
    first = make_rig()
    bank = first.account(opening_date="2027-01-01")
    first.add(bank, "2027-01-05", payment="5.00")
    data = json.loads(fetch(first, what="json")["text"])
    data["accounts"][0]["opening_date"] = "2027-02-01"
    second = make_rig()
    second.call("everything.import", {"token": second.upload(json.dumps(data))})
    assert len(second.rows(second.call("overview")["accounts"][0]["id"])) == 1
