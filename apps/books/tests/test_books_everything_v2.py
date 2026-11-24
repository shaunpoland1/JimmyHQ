"""The "everything" file, version 2: debts, reminders with no account, transfer reminders and
check numbers; and a version 1 file still comes in."""

from __future__ import annotations

import copy
import json

import pytest
from books_rig import TODAY
from jimmyhq_app import CallError
from test_books_backup import fetch


def v2(**more) -> dict:
    """A small version 2 file as an outside program would write it."""
    data = {
        "format": "jimmyhq-books", "version": 2, "exported_ms": 1_800_000_000_000,
        "accounts": [
            {"id": 1, "name": "Example Bank", "kind": "checking", "opening_cents": 100000,
             "opening_date": "2027-01-01", "closed": False},
            {"id": 2, "name": "Example Savings", "kind": "savings", "opening_cents": 0,
             "opening_date": "2027-01-01", "closed": False},
            {"id": 3, "name": "Example Card Account", "kind": "credit_card",
             "opening_cents": -40000, "opening_date": "2027-01-01", "closed": False}],
        "categories": [{"id": 1, "name": "Groceries", "income": False}],
        "transactions": [
            {"id": 1, "account": 1, "date": "2027-01-03", "payee": "Landlord", "category": None,
             "memo": "", "number": "1042", "amount_cents": -80000, "state": "c",
             "transfer": None}],
        "reminders": [
            {"id": 1, "name": "Cash rent", "payee": "", "amount_cents": 30000, "about": False,
             "account": None, "paid_other": True, "category": None, "kind": "bill",
             "every": "monthly", "day": 20, "day2": 15, "n": 1, "next_due": "2027-01-20"},
            {"id": 2, "name": "Save for tax", "payee": "", "amount_cents": 25000, "about": False,
             "account": 1, "to_account": 2, "paid_other": False, "category": None,
             "kind": "transfer", "every": "monthly", "day": 20, "day2": 15, "n": 1,
             "next_due": "2027-01-20"},
            {"id": 3, "name": "Water bill", "payee": "", "amount_cents": 4500, "about": True,
             "account": 1, "category": 1, "kind": "bill", "every": "monthly", "day": 31,
             "day2": 15, "n": 1, "next_due": "2027-01-31"}],
        "debts": [
            {"id": 1, "name": "Example Card", "rate_percent": 19.99, "min_payment_cents": 2500,
             "target_date": "2028-01-01", "account": 3, "note": "Statement on the 3rd",
             "closed": False,
             "balance_history": [{"on": "2027-01-10", "balance_cents": 38000},
                                 {"on": "2027-01-01", "balance_cents": 40000}]},
            {"id": 2, "name": "Mystery", "rate_percent": None, "min_payment_cents": 0,
             "target_date": None, "account": None, "note": "", "closed": False,
             "balance_history": []}],
        "reconciliations": []}
    data.update(more)
    return data


def bring(rig, data) -> dict:
    return rig.call("everything.import", {"token": rig.upload(json.dumps(data))})


def test_a_version_2_file_comes_in_with_debts_paid_other_ways_transfers_and_numbers(rig):
    said = bring(rig, v2())
    assert said["said"] == "Brought in 3 accounts, 1 row, 3 reminders and 2 debts."
    seen = rig.call("reminders")["items"]
    by_name = {i["name"]: i for i in seen}
    assert by_name["Cash rent"]["paid_other"] is True and by_name["Cash rent"]["account"] is None
    assert by_name["Save for tax"]["kind"] == "transfer"
    assert by_name["Save for tax"]["to_account_name"] == "Example Savings"
    bank = next(a for a in rig.call("overview")["accounts"] if a["name"] == "Example Bank")
    assert rig.rows(bank["id"])[0]["number"] == "1042"
    board = rig.call("debts")
    card = next(i for i in board["items"] if i["name"] == "Example Card")
    assert (card["balance"], card["as_of"], card["rate"], card["minimum"],
            card["target_date"], card["account_name"], card["note"]) == (
        "380.00", "2027-01-10", "19.99", "25.00", "2028-01-01", "Example Card Account",
        "Statement on the 3rd")
    assert [h["on"] for h in card["history"]] == ["2027-01-10", "2027-01-01"]
    mystery = next(i for i in board["items"] if i["name"] == "Mystery")
    assert mystery["known"] is False and board["total"]["text"] == "380.00"


def test_what_goes_out_is_exactly_the_shape_the_guide_describes(make_rig):
    first = make_rig()
    bring(first, v2())
    out = json.loads(fetch(first, what="json")["text"])
    assert out["version"] == 2 and out["format"] == "jimmyhq-books"
    assert set(out) == {"format", "version", "exported_ms", "accounts", "categories",
                        "transactions", "reminders", "debts", "reconciliations"}
    assert set(out["transactions"][0]) == {"id", "account", "date", "payee", "category", "memo",
                                           "number", "amount_cents", "state", "transfer"}
    assert set(out["reminders"][0]) == {"id", "name", "payee", "amount_cents", "about",
                                        "account", "to_account", "paid_other", "category",
                                        "kind", "every", "day", "day2", "n", "next_due",
                                        "auto", "note"}
    assert set(out["debts"][0]) == {"id", "name", "kind", "rate_percent", "min_payment_cents",
                                    "target_date", "account", "note", "closed",
                                    "balance_history"}
    assert out["debts"][0]["rate_percent"] == 19.99
    assert out["debts"][0]["balance_history"] == [
        {"on": "2027-01-01", "balance_cents": 40000}, {"on": "2027-01-10", "balance_cents": 38000}]
    cash = next(r for r in out["reminders"] if r["name"] == "Cash rent")
    assert (cash["account"], cash["paid_other"], cash["to_account"]) == (None, True, None)
    tax = next(r for r in out["reminders"] if r["name"] == "Save for tax")
    assert (tax["kind"], tax["to_account"], tax["account"]) == ("transfer", 2, 1)
    second = make_rig()
    assert bring(second, out)["debts"] == 2
    again = json.loads(fetch(second, what="json")["text"])
    strip = lambda d: {k: v for k, v in d.items() if k != "exported_ms"}  # noqa: E731
    assert strip(again) == strip(out)


def test_a_version_1_file_still_comes_in_with_the_new_things_empty(rig):
    old = v2(version=1)
    for key in ("debts",):
        del old[key]
    for t in old["transactions"]:
        del t["number"]
    old["reminders"] = [r for r in old["reminders"] if r["kind"] != "transfer"
                        and not r.get("paid_other")]
    for r in old["reminders"]:
        r.pop("to_account", None)
        r.pop("paid_other", None)
    said = bring(rig, old)
    assert said["said"] == "Brought in 3 accounts, 1 row and 1 reminder."
    assert rig.call("debts")["items"] == []
    bank = next(a for a in rig.call("overview")["accounts"] if a["name"] == "Example Bank")
    assert rig.rows(bank["id"])[0]["number"] == ""


def test_a_version_this_books_does_not_read_is_refused(rig):
    with pytest.raises(CallError, match="version 3.*reads versions 1 and 2.*Nothing was changed"):
        bring(rig, v2(version=3))
    with pytest.raises(CallError, match="version True"):
        bring(rig, v2(version=True))
    assert rig.call("overview")["accounts"] == []


def broken(change) -> dict:
    data = copy.deepcopy(v2())
    change(data)
    return data


@pytest.mark.parametrize("change, said", [
    (lambda d: d["reminders"][0].update(account=1), "must have an \"account\" unless"),
    (lambda d: d["reminders"][2].update(paid_other=True), "must have an \"account\" unless"),
    (lambda d: d["reminders"][2].update(account=None), "must have an \"account\" unless"),
    (lambda d: d["reminders"][2].update(to_account=2), "has a \"to_account\" but is not"),
    (lambda d: d["reminders"][1].update(to_account=1), "needs two different accounts"),
    (lambda d: d["reminders"][1].update(to_account=None), "needs two different accounts"),
    (lambda d: d["reminders"][1].update(paid_other=True), "needs two different accounts"),
    (lambda d: d["reminders"][1].update(category=1), "\"category\" must be null"),
    (lambda d: d["reminders"][1].update(to_account=9), "names an account"),
    (lambda d: d["reminders"][0].update(paid_other="yes"), "not true or false"),
    (lambda d: d["debts"][0].update(rate_percent=19.999), "at most two decimals"),
    (lambda d: d["debts"][0].update(rate_percent=101), "from 0 to 100"),
    (lambda d: d["debts"][0].update(rate_percent="19"), "no number for \"rate_percent\""),
    (lambda d: d["debts"][0].update(min_payment_cents=-1), "below zero"),
    (lambda d: d["debts"][0].update(account=9), "names an account"),
    (lambda d: d["debts"][0].update(target_date="2028-13-01"), "date like"),
    (lambda d: d["debts"][1].update(name="example card"), "two debts are called"),
    (lambda d: d["debts"][0]["balance_history"][0].update(balance_cents=-5), "below zero"),
    (lambda d: d["debts"][0]["balance_history"][0].update(on="2027-01-01"), "two balances for"),
    (lambda d: d["debts"][0]["balance_history"][0].update(on="soon"), "date like"),
    (lambda d: d["debts"][0].update(balance_history={"a": 1}), "not a list of items"),
    (lambda d: d["transactions"][0].update(number="1" * 21), "no proper \"number\""),
    (lambda d: d["accounts"][1].update(id=1), "two accounts have the id 1"),
    (lambda d: d["categories"].append({"id": 1, "name": "Home"}), "two categories have the id"),
    (lambda d: d["transactions"].append(dict(d["transactions"][0])), "two transactions have"),
    (lambda d: d["accounts"][0].update(closed=0), "not true or false"),
    (lambda d: d["categories"][0].update(income="no"), "not true or false"),
    (lambda d: d["reminders"][2].update(about=1), "not true or false"),
    (lambda d: d["reminders"][2].update(day="31"), "no whole number for \"day\""),
    (lambda d: d["reminders"][2].update(every="twice_monthly", day=15), "two different days"),
])


def test_one_bad_item_in_the_new_parts_and_nothing_is_brought_in(make_rig, change, said):
    rig = make_rig()
    with pytest.raises(CallError, match=said):
        bring(rig, broken(change))
    assert rig.call("overview")["accounts"] == []
    assert rig.store.one("SELECT count(*) AS n FROM debt")["n"] == 0


def test_a_transfer_must_be_two_rows_in_two_accounts_that_sum_to_zero(make_rig):
    def pair(**second):
        data = v2()
        data["transactions"] = [
            {"id": 1, "account": 1, "date": "2027-01-03", "payee": "", "category": None,
             "memo": "", "number": "", "amount_cents": -5000, "state": "", "transfer": 2},
            {"id": 2, "account": 2, "date": "2027-01-03", "payee": "", "category": None,
             "memo": "", "number": "", "amount_cents": 5000, "state": "", "transfer": 1}]
        data["transactions"][1].update(second)
        return data
    assert bring(make_rig(), pair())["transactions"] == 2
    for bad in ({"amount_cents": 4000}, {"account": 1}):
        with pytest.raises(CallError, match="sum to zero"):
            bring(make_rig(), pair(**bad))


def test_reminder_fields_and_reconciliation_counts_may_be_left_out(rig):
    data = v2()
    for r in data["reminders"]:
        for key in ("day", "day2", "n", "payee", "about"):
            r.pop(key, None)
    data["reminders"][2].update(every="weekly")
    data["reconciliations"] = [{"account": 1, "through": "2027-01-01", "statement_cents": 100000}]
    assert bring(rig, data)["reminders"] == 3
    assert rig.call("reconcile.start", {"account": 1, "through": "2027-01-15",
                                        "statement": "1,000.00"})


def test_a_rate_written_as_a_whole_number_or_with_two_decimals_is_taken(rig):
    data = v2()
    data["debts"][0]["rate_percent"] = 20
    data["debts"][1]["rate_percent"] = 0.05
    bring(rig, data)
    rates = {i["name"]: i["rate"] for i in rig.call("debts")["items"]}
    assert rates == {"Example Card": "20.00", "Mystery": "0.05"}


def test_erase_everything_takes_the_debts_and_their_history_too(rig):
    bring(rig, v2())
    rig.call("everything.erase", {"confirm": True})
    assert rig.call("debts")["items"] == []
    assert rig.store.one("SELECT count(*) AS n FROM debt_balance")["n"] == 0
    assert rig.call("overview")["accounts"] == []


def test_debts_and_a_reminder_paid_another_way_come_back_after_a_round_trip(make_rig):
    first = make_rig()
    bank = first.account()
    first.call("reminder.add", {"name": "Cash rent", "amount": "300", "kind": "bill",
                                "paid_other": True, "every": "monthly",
                                "next_due": "2027-01-20"})
    first.call("debt.add", {"name": "Example Card", "balance": "400", "rate": "19.99",
                            "minimum": "25", "account": bank, "target_date": "2028-01-01"})
    first.call("debt.balance", {"id": 1, "balance": "380", "on": TODAY})
    out = fetch(first, what="json")["text"]
    second = make_rig()
    second.call("everything.import", {"token": second.upload(out)})
    assert second.call("debts")["items"][0]["balance"] == "380.00"
    item = second.call("reminders")["items"][0]
    assert item["paid_other"] is True and item["account"] is None


def test_the_example_in_the_guide_is_a_file_books_brings_in(rig):
    from pathlib import Path
    guide = (Path(__file__).resolve().parents[3] / "docs" / "books.md").read_text("utf-8")
    block = guide.split("### The JSON shape")[1].split("```json")[1].split("```")[0]
    said = bring(rig, json.loads(block))
    assert (said["accounts"], said["transactions"], said["reminders"], said["debts"]) == (
        2, 1, 3, 1)
    assert rig.call("debts")["items"][0]["balance"] == "380.00"
