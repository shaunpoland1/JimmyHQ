"""Books stays off the record: nothing on the Tape, no model, no network, and a Bench card
that says a bill's name and due date and never an amount or an account."""

from __future__ import annotations

import json

from books_rig import DAY, TODAY

OUTSIDE = ("tape.print", "tape.read", "model.ask", "model.embed", "net.fetch", "web.search",
           "web.read", "memory.recall", "tasks.create", "vault.put", "feed.read")


def everything_once(rig) -> None:
    """A full run of the main operations, as the owner would make them."""
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="10")
    row = rig.add(bank, "2027-01-03", "Corner Shop", payment="12.50", category="Groceries")
    rig.call("txn.edit", {"id": row, "memo": "milk"})
    tr = rig.call("transfer.add", {"from": bank, "to": save, "date": TODAY, "amount": "100"})
    rig.call("txn.clear", {"id": tr["ids"][1]})
    rig.call("txn.edit", {"id": tr["ids"][0], "payment": "90", "deposit": ""})
    rem = rig.call("reminder.add", {"name": "Water bill", "payee": "City Water",
                                    "amount": "45.67", "account": bank, "kind": "bill",
                                    "every": "monthly", "next_due": "2027-01-16"})["id"]
    rig.core.run_job("due")
    rig.call("reminder.post", {"id": rem, "due": "2027-01-16"})
    rig.call("reminder.skip", {"id": rem, "due": "2027-02-16"})
    rig.call("ahead", {"account": bank})
    rig.call("reconcile.tick", {"account": bank, "through": TODAY, "statement": "987.50",
                                "id": row})
    rig.call("reconcile.finish", {"account": bank, "through": TODAY, "statement": "987.50"})
    rig.call("spending", {"month": "2027-01"})
    token = rig.upload("Date,Description,Amount\r\n2027-01-04,Hardware Barn,-30\r\n")
    done = rig.call("import.commit", {"account": bank, "token": token})
    rig.call("import.undo", {"id": done["id"]})
    rig.call("export.part", {"what": "csv", "part": 0})
    rig.call("export.part", {"what": "json", "part": 0})
    rig.call("txn.delete", {"id": tr["ids"][1]})
    rig.call("overview")


def test_nothing_is_ever_written_to_the_tape_or_sent_anywhere(rig):
    everything_once(rig)
    assert rig.asked, "the spy saw the app's calls"
    assert not [name for name in rig.asked if name in OUTSIDE]
    assert rig.core.events == []
    assert rig.core.asked == [] and rig.core.fetched == [] and rig.core.tasks == []


def test_a_bench_card_carries_the_bills_name_and_date_and_no_amount_or_account(rig):
    bank = rig.account()
    rig.call("reminder.add", {"name": "Water bill", "payee": "City Water",
                              "amount": "45.67", "about": True, "account": bank,
                              "kind": "bill", "category": "Utilities", "every": "monthly",
                              "next_due": "2027-01-17"})
    cards = rig.cards()
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "Water bill is due on 17 January."
    said = json.dumps(card)
    for secret in ("45.67", "45,67", "4567", "Example Bank", "City Water", "Utilities"):
        assert secret not in said
    assert card["subject"].startswith("app.books:reminder:")


def test_a_card_comes_once_for_a_due_date_and_goes_when_the_bill_is_posted(rig):
    bank = rig.account()
    rem = rig.call("reminder.add", {"name": "Water bill", "amount": "45", "account": bank,
                                    "kind": "bill", "every": "monthly",
                                    "next_due": "2027-01-16"})["id"]
    rig.core.run_job("due")
    rig.core.run_job("due")
    assert len(rig.cards()) == 1
    rig.call("reminder.post", {"id": rem, "due": "2027-01-16"})
    assert rig.cards() == []


def test_a_bill_further_off_than_the_setting_gets_no_card_until_it_is_near(rig):
    bank = rig.account()
    rig.call("reminder.add", {"name": "Insurance", "amount": "300", "account": bank,
                              "kind": "bill", "every": "yearly", "next_due": "2027-01-25"})
    assert rig.cards() == []
    rig.clock.advance(7 * DAY)
    rig.core.run_job("due")
    assert [c["title"] for c in rig.cards()] == ["Insurance is due on 25 January."]


def test_income_never_gets_a_card(rig):
    bank = rig.account()
    rig.call("reminder.add", {"name": "Pay", "amount": "900", "account": bank,
                              "kind": "income", "every": "biweekly", "next_due": TODAY})
    rig.core.run_job("due")
    assert rig.cards() == []


def test_the_owner_can_switch_the_cards_off_and_open_ones_go(rig):
    bank = rig.account()
    rig.call("reminder.add", {"name": "Water bill", "amount": "45", "account": bank,
                              "kind": "bill", "every": "monthly", "next_due": TODAY})
    assert len(rig.cards()) == 1
    rig.core.change_setting("tell_bills", False)
    assert rig.cards() == []
    rig.core.run_job("due")
    assert rig.cards() == []


def test_closing_a_card_on_the_bench_is_heard(rig):
    bank = rig.account()
    rem = rig.call("reminder.add", {"name": "Water bill", "amount": "45", "account": bank,
                                    "kind": "bill", "every": "monthly",
                                    "next_due": TODAY})["id"]
    card = rig.cards()[0]
    rig.core.decide(card["card_id"], "ok")
    assert rig.store.reminder(rem)["card_id"] is None
    rig.core.run_job("due")
    assert len(rig.core.cards) == 1  # told once for that due date, not again


def test_a_deleted_row_is_overwritten_in_the_file_not_only_unlinked(rig):
    bank = rig.account()
    row = rig.add(bank, TODAY, "Quietly Named Payee", payment="1")
    raw = b"".join(p.read_bytes() for p in rig.ctx.app.data_dir.glob("books.db*"))
    assert b"Quietly Named Payee" in raw
    rig.call("txn.delete", {"id": row})
    raw = b"".join(p.read_bytes() for p in rig.ctx.app.data_dir.glob("books.db*"))
    assert b"Quietly Named Payee" not in raw
