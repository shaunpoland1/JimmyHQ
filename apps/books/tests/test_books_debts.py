"""Debts: what is owed, balances kept with their dates, and a rough payoff order."""

from __future__ import annotations

from datetime import date

import pytest
from books import debts
from books_rig import TODAY
from jimmyhq_app import CallError


def add(rig, name="Example Card", **more):
    return rig.call("debt.add", {"name": name, **more})["id"]


def board(rig):
    return rig.call("debts")


def one(rig, debt_id):
    return next(i for i in board(rig)["items"] if i["id"] == debt_id)


# --- the arithmetic --------------------------------------------------------------------------


def test_a_months_interest_is_the_yearly_rate_over_twelve_to_the_nearest_cent():
    assert debts.monthly_interest(120_000, 1200) == 1200  # 1,200.00 at 12% is 12.00 a month
    assert debts.monthly_interest(100_000, 1999) == 1666  # 1,000.00 at 19.99%: 16.658
    assert debts.monthly_interest(50, 1200) == 1  # half a cent rounds up
    assert debts.monthly_interest(100_000, 0) == 0


def test_a_debt_with_no_interest_is_paid_off_in_balance_over_payment_months():
    out = debts.plan([{"id": 1, "balance": 100_000, "rate": 0, "minimum": 10_000}],
                     "smallest", 0, date(2027, 1, 15))
    assert out["by_id"][1] == {"status": "ok", "months": 10, "interest_cents": 0,
                               "payoff_month": "2027-11"}


def test_extra_money_pays_a_debt_off_sooner_and_costs_less_interest():
    items = [{"id": 1, "balance": 200_000, "rate": 1800, "minimum": 5_000}]
    slow = debts.plan(items, "smallest", 0, date(2027, 1, 15))["by_id"][1]
    fast = debts.plan(items, "smallest", 10_000, date(2027, 1, 15))["by_id"][1]
    assert fast["months"] < slow["months"] and fast["interest_cents"] < slow["interest_cents"]


def test_smallest_first_sends_the_extra_to_the_smallest_balance_and_highest_rate_to_the_dearest():
    items = [{"id": 1, "balance": 500_000, "rate": 2400, "minimum": 10_000},
             {"id": 2, "balance": 100_000, "rate": 600, "minimum": 5_000}]
    small = debts.plan(items, "smallest", 20_000, date(2027, 1, 15))
    dear = debts.plan(items, "rate", 20_000, date(2027, 1, 15))
    assert small["order"] == [2, 1] and dear["order"] == [1, 2]
    assert small["by_id"][2]["months"] < dear["by_id"][2]["months"]
    assert dear["by_id"][1]["months"] < small["by_id"][1]["months"]


def test_when_one_debt_is_paid_off_its_minimum_moves_on_to_the_next():
    items = [{"id": 1, "balance": 10_000, "rate": 0, "minimum": 10_000},
             {"id": 2, "balance": 100_000, "rate": 0, "minimum": 10_000}]
    out = debts.plan(items, "smallest", 0, date(2027, 1, 15))["by_id"]
    assert out[1]["months"] == 1
    assert out[2]["months"] == 6  # 10,000 once, then 20,000 a month: 10 + 5 x 20 = 110 > 100


def test_a_payment_that_does_not_cover_the_interest_is_never_paid_off():
    out = debts.plan([{"id": 1, "balance": 1_000_000, "rate": 2400, "minimum": 1_000}],
                     "smallest", 0, date(2027, 1, 15))
    assert out["by_id"][1] == {"status": "never"}


def test_a_debt_with_no_balance_known_is_not_in_the_plan_and_is_not_zero():
    out = debts.plan([{"id": 1, "balance": None, "rate": 1000, "minimum": 5_000},
                      {"id": 2, "balance": 0, "rate": 1000, "minimum": 5_000},
                      {"id": 3, "balance": 9_000, "rate": None, "minimum": 5_000}],
                     "smallest", 0, date(2027, 1, 15))
    assert [out["by_id"][i]["status"] for i in (1, 2, 3)] == ["unknown", "paid", "needs_rate"]
    assert out["order"] == []


# --- the tab ---------------------------------------------------------------------------------


def test_a_debt_added_with_no_balance_is_shown_as_not_known_and_never_as_zero(rig):
    debt = add(rig, rate="19.99", minimum="25")
    item = one(rig, debt)
    assert item["known"] is False and item["balance"] == "" and item["paid"] is False
    assert item["payoff"] == "Balance not known"
    seen = board(rig)
    assert seen["total"]["unknown"] == ["Example Card"] and seen["steps"] == []
    assert rig.call("debt.add", {"name": "Other", "balance": "0"})["said"] == "Other is added."
    assert one(rig, rig.store.one("SELECT id FROM debt WHERE name='Other'")["id"])["paid"] is True


def test_the_total_owed_adds_every_known_balance_of_an_open_debt(rig):
    add(rig, "Example Card", balance="1,200.50", rate="20", minimum="30")
    add(rig, "Example Loan", balance="800", rate="5", minimum="40")
    add(rig, "Mystery")
    shut = add(rig, "Old Card", balance="999")
    rig.call("debt.edit", {"id": shut, "name": "Old Card", "closed": True})
    seen = board(rig)
    assert seen["total"]["text"] == "2,000.50"
    assert seen["total"]["unknown"] == ["Mystery"] and seen["minimums"] == "70.00"


def test_a_balance_update_keeps_a_dated_history_newest_first(rig):
    debt = add(rig, balance="900", rate="10", minimum="30", on="2027-01-02")
    rig.call("debt.balance", {"id": debt, "balance": "850", "on": "2027-01-05"})
    rig.call("debt.balance", {"id": debt, "balance": "800"})
    item = one(rig, debt)
    assert item["balance"] == "800.00" and item["as_of"] == TODAY
    assert [(h["on"], h["text"]) for h in item["history"]] == [
        (TODAY, "800.00"), ("2027-01-05", "850.00"), ("2027-01-02", "900.00")]


def test_two_balances_on_one_day_keep_the_later_one(rig):
    debt = add(rig, balance="900")
    rig.call("debt.balance", {"id": debt, "balance": "875"})
    item = one(rig, debt)
    assert [h["text"] for h in item["history"]] == ["875.00"]


def test_the_latest_dated_balance_is_the_current_one_whatever_order_they_came_in(rig):
    debt = add(rig)
    rig.call("debt.balance", {"id": debt, "balance": "700", "on": "2027-01-10"})
    rig.call("debt.balance", {"id": debt, "balance": "900", "on": "2027-01-02"})
    item = one(rig, debt)
    assert item["balance"] == "700.00" and item["as_of"] == "2027-01-10"
    assert [h["on"] for h in item["history"]] == ["2027-01-10", "2027-01-02"]


def test_a_balance_cannot_be_dated_after_today_or_be_below_zero(rig):
    debt = add(rig)
    with pytest.raises(CallError, match="after today.*Nothing was changed"):
        rig.call("debt.balance", {"id": debt, "balance": "5", "on": "2027-02-01"})
    with pytest.raises(CallError, match="no minus sign.*Nothing was changed"):
        rig.call("debt.balance", {"id": debt, "balance": "-5"})
    with pytest.raises(CallError, match="Type an amount"):
        rig.call("debt.balance", {"id": debt, "balance": ""})
    assert one(rig, debt)["known"] is False


def test_a_balance_of_zero_means_paid_off_and_is_an_answer(rig):
    debt = add(rig, balance="300", rate="10", minimum="50")
    said = rig.call("debt.balance", {"id": debt, "balance": "0"})["said"]
    assert "paid off" in said
    item = one(rig, debt)
    assert item["paid"] is True and item["payoff"] == "Paid off" and item["known"] is True
    assert board(rig)["total"]["text"] == "0.00" and board(rig)["total"]["unknown"] == []


def test_the_payoff_order_lists_each_debt_with_about_when_it_will_be_paid(rig):
    big = add(rig, "Example Loan", balance="5000", rate="6", minimum="100")
    small = add(rig, "Example Card", balance="400", rate="22", minimum="25")
    seen = board(rig)
    assert [s["id"] for s in seen["steps"]] == [small, big]  # the setting starts at smallest
    assert seen["order_words"] == "Smallest balance first"
    assert seen["steps"][0]["payoff_month"] and seen["steps"][0]["months"] > 0
    assert one(rig, small)["payoff"].startswith("About 2028-")
    assert seen["debt_free"] >= seen["steps"][1]["payoff_month"]
    assert one(rig, big)["interest"]


def test_the_order_setting_and_the_extra_each_month_setting_change_the_plan(make_rig):
    def run(**settings):
        rig = make_rig(settings=settings)
        add(rig, "Example Loan", balance="5000", rate="24", minimum="100")
        add(rig, "Example Card", balance="400", rate="5", minimum="25")
        return rig.call("debts")
    smallest = run()
    rate = run(debt_order="rate")
    assert [s["name"] for s in smallest["steps"]] == ["Example Card", "Example Loan"]
    assert [s["name"] for s in rate["steps"]] == ["Example Loan", "Example Card"]
    assert rate["order_words"] == "Highest rate first"
    plain = run()["steps"][1]["months"]
    extra = run(debt_extra="500")
    assert extra["extra"] == "500.00" and extra["steps"][1]["months"] < plain


def test_an_extra_each_month_that_is_not_an_amount_is_said_and_counts_as_nothing(make_rig):
    rig = make_rig(settings={"debt_extra": "lots"})
    add(rig, balance="500", rate="10", minimum="50")
    seen = rig.call("debts")
    assert "not an amount" in seen["problem"] and seen["extra"] == ""
    assert seen["steps"][0]["months"] > 0


def test_a_debt_with_a_target_date_says_whether_the_plan_meets_it(rig):
    soon = add(rig, "Quick", balance="300", rate="0", minimum="100", target_date="2027-12-31")
    late = add(rig, "Slow", balance="9000", rate="0", minimum="100", target_date="2027-06-30")
    assert one(rig, soon)["standing"] == "on_track" and one(rig, late)["standing"] == "behind"
    assert one(rig, add(rig, "No date", balance="10", rate="0", minimum="10"))["standing"] is None


def test_a_debt_that_cannot_be_paid_at_its_minimum_says_so(rig):
    debt = add(rig, balance="10000", rate="24", minimum="50")
    item = one(rig, debt)
    assert item["payoff"] == "Not paid off at these payments"
    assert board(rig)["stuck"] == ["Example Card"] and board(rig)["debt_free"] == ""


def test_a_debt_with_no_rate_is_listed_but_needs_a_rate_for_the_plan(rig):
    debt = add(rig, balance="500", minimum="50")
    assert one(rig, debt)["payoff"] == "Needs a rate" and one(rig, debt)["rate"] == ""
    assert board(rig)["steps"] == []


def test_the_rate_and_minimum_are_read_exactly_or_refused(rig):
    with pytest.raises(CallError, match="percent from 0 to 100.*Nothing was changed"):
        add(rig, rate="150")
    with pytest.raises(CallError, match="not a percentage"):
        add(rig, rate="19.999")
    with pytest.raises(CallError, match="minus sign"):
        add(rig, minimum="-5")
    assert one(rig, add(rig, rate="19.99%", minimum="25.50"))["rate"] == "19.99"
    assert board(rig)["items"][0]["minimum"] == "25.50"


def test_two_debts_cannot_share_a_name_and_a_debt_needs_one(rig):
    add(rig)
    with pytest.raises(CallError, match="already have a debt called"):
        add(rig, "example card")
    with pytest.raises(CallError, match="Type a name"):
        add(rig, "")


def test_a_debt_can_be_changed_closed_and_opened_again(rig):
    debt = add(rig, balance="300", rate="10", minimum="30")
    rig.call("debt.edit", {"id": debt, "name": "Example Card", "rate": "12", "minimum": "40",
                           "note": "Paid on the 3rd", "closed": True})
    item = one(rig, debt)
    assert (item["closed"], item["rate"], item["minimum"], item["note"]) == (
        True, "12.00", "40.00", "Paid on the 3rd")
    assert board(rig)["total"]["text"] == "0.00" and board(rig)["steps"] == []
    rig.call("debt.edit", {"id": debt, "name": "Example Card", "closed": False})
    assert board(rig)["total"]["text"] == "300.00"


def test_deleting_a_debt_deletes_its_history_and_leaves_the_register_alone(rig):
    bank = rig.account()
    rig.add(bank, TODAY, payment="5")
    debt = add(rig, balance="300")
    rig.call("debt.balance", {"id": debt, "balance": "250", "on": "2027-01-10"})
    rig.call("debt.delete", {"id": debt})
    assert board(rig)["items"] == []
    assert rig.store.one("SELECT count(*) AS n FROM debt_balance")["n"] == 0
    assert len(rig.rows(bank)) == 1


def test_a_debt_linked_to_a_card_can_take_its_balance_from_the_account(rig):
    card = rig.account("Example Card Account", "credit_card", opening="400")
    rig.add(card, TODAY, "Cafe", payment="50.00")  # a charge: 450.00 is owed now
    debt = add(rig, "Example Card", account=card)
    rig.call("debt.balance", {"id": debt, "from_account": True})
    owed = next(a for a in rig.call("overview")["accounts"] if a["id"] == card)["today"]["text"]
    assert owed == "450.00" and one(rig, debt)["balance"] == "450.00"
    assert one(rig, debt)["account_name"] == "Example Card Account"


def test_only_a_debt_linked_to_a_card_or_loan_can_take_its_balance_from_the_account(rig):
    bank = rig.account()
    debt = add(rig, "Example Card", account=bank)
    assert one(rig, debt)["can_take"] is False
    with pytest.raises(CallError, match="Type the balance instead"):
        rig.call("debt.balance", {"id": debt, "from_account": True})
    with pytest.raises(CallError, match="Type the balance instead"):
        rig.call("debt.balance", {"id": add(rig, "Loose"), "from_account": True})


def test_an_account_with_a_debt_linked_to_it_cannot_be_deleted(rig):
    bank = rig.account()
    add(rig, account=bank)
    with pytest.raises(CallError, match="A debt is still linked to Example Bank"):
        rig.call("account.delete", {"id": bank})


def test_amounts_are_shown_in_the_owners_number_style(make_rig):
    rig = make_rig(settings={"number_style": "comma"})
    add(rig, balance="1.234,50", rate="19,99", minimum="25,00")
    item = rig.call("debts")["items"][0]
    assert (item["balance"], item["rate"], item["minimum"]) == ("1.234,50", "19,99", "25,00")


def test_debts_stay_on_this_computer_nothing_on_the_record_and_no_model_or_network(rig):
    debt = add(rig, balance="300", rate="10", minimum="30")
    rig.call("debt.balance", {"id": debt, "balance": "250"})
    rig.call("debt.edit", {"id": debt, "name": "Example Card", "closed": True})
    board(rig)
    rig.call("debt.delete", {"id": debt})
    outside = ("tape.print", "tape.read", "model.ask", "model.embed", "net.fetch", "web.search",
               "web.read", "memory.recall", "tasks.create", "vault.put", "feed.read")
    assert not [name for name in rig.asked if name in outside]
    assert rig.core.events == [] and rig.core.fetched == [] and rig.core.asked == []


def test_a_deleted_debt_is_overwritten_in_the_file_not_only_unlinked(rig):
    debt = add(rig, "Distinctive Lender Name", balance="300", note="a distinctive note text")
    rig.call("debt.delete", {"id": debt})
    rig.ctx.close()
    for path in rig.ctx.app.data_dir.glob("books.db*"):
        raw = path.read_bytes()
        assert b"Distinctive Lender Name" not in raw and b"a distinctive note text" not in raw
