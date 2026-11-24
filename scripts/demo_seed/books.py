"""Sample content for Books: a small household's money, all invented.

Creates four accounts (checking, savings, a credit card and a car loan), about forty
register rows over the last ten weeks (pay, groceries, utilities, fun, a transfer to
savings), a few new categories, bills and income reminders (some overdue, some due soon,
some later, one "about this much", one paid another way), and three debts with a balance
history. Every date is an offset from ``today``. The reminders are not set to post by
themselves, so a fresh install shows overdue bills waiting for the owner.
"""

from __future__ import annotations

from datetime import date, timedelta

SETTINGS: dict[str, object] = {}

ACCOUNTS = (  # name, kind, opening balance, days before today it opens
    ("Everyday Checking", "checking", "900.00", 75),
    ("Rainy Day Savings", "savings", "4,800.00", 75),
    ("Blue Card", "credit_card", "620.00", 75),
    ("Car Loan", "loan", "6,400.00", 75),
)
CATEGORIES = (("Pets", 0), ("Side jobs", 1))

# account, days ago, payee, payment, deposit, category
ROWS = (
    ("Everyday Checking", 72, "Maple Street Market", "84.12", "", "Groceries"),
    ("Everyday Checking", 70, "Riverside Power", "96.40", "", "Utilities"),
    ("Everyday Checking", 66, "Garden Bakery", "12.75", "", "Eating out"),
    ("Everyday Checking", 63, "Bright Paper Co", "", "1,380.00", "Pay"),
    ("Everyday Checking", 61, "Maple Street Market", "92.60", "", "Groceries"),
    ("Everyday Checking", 58, "Hilltop Pharmacy", "23.45", "", "Health"),
    ("Everyday Checking", 55, "Transit Pass Office", "64.00", "", "Transport"),
    ("Everyday Checking", 52, "Pine Hardware", "37.80", "", "Home"),
    ("Everyday Checking", 49, "Maple Street Market", "78.33", "", "Groceries"),
    ("Everyday Checking", 47, "Harbor Cinema", "26.00", "", "Fun"),
    ("Everyday Checking", 43, "Bright Paper Co", "", "1,380.00", "Pay"),
    ("Everyday Checking", 41, "Riverside Power", "102.15", "", "Utilities"),
    ("Everyday Checking", 38, "Paws and Claws Vet", "88.00", "", "Pets"),
    ("Everyday Checking", 35, "Maple Street Market", "101.27", "", "Groceries"),
    ("Everyday Checking", 31, "Neighbor Dog Walking", "", "60.00", "Side jobs"),
    ("Everyday Checking", 28, "Garden Bakery", "9.50", "", "Eating out"),
    ("Everyday Checking", 23, "Bright Paper Co", "", "1,380.00", "Pay"),
    ("Everyday Checking", 21, "Maple Street Market", "69.90", "", "Groceries"),
    ("Everyday Checking", 18, "Corner Books", "31.25", "", "Gifts"),
    ("Everyday Checking", 14, "Hilltop Pharmacy", "15.60", "", "Health"),
    ("Everyday Checking", 11, "Riverside Power", "88.72", "", "Utilities"),
    ("Everyday Checking", 8, "Maple Street Market", "96.04", "", "Groceries"),
    ("Everyday Checking", 5, "Harbor Cinema", "24.00", "", "Fun"),
    ("Everyday Checking", 3, "Bright Paper Co", "", "1,380.00", "Pay"),
    ("Everyday Checking", 1, "Garden Bakery", "11.40", "", "Eating out"),
    ("Rainy Day Savings", 40, "Credit Union Interest", "", "3.12", "Other income"),
    ("Rainy Day Savings", 10, "Credit Union Interest", "", "3.31", "Other income"),
    ("Blue Card", 60, "Online Garden Supply", "74.20", "", "Home"),
    ("Blue Card", 45, "Trailhead Outfitters", "129.99", "", "Shopping"),
    ("Blue Card", 33, "Harbor Cinema", "42.00", "", "Fun"),
    ("Blue Card", 20, "Pine Hardware", "58.35", "", "Home"),
    ("Blue Card", 9, "Garden Bakery", "27.80", "", "Eating out"),
    ("Blue Card", 4, "Paws and Claws Vet", "46.50", "", "Pets"),
)
# from, to, days ago, amount
TRANSFERS = (
    ("Everyday Checking", "Rainy Day Savings", 62, "300.00"),
    ("Everyday Checking", "Rainy Day Savings", 42, "300.00"),
    ("Everyday Checking", "Rainy Day Savings", 22, "300.00"),
    ("Everyday Checking", "Rainy Day Savings", 2, "300.00"),
)
# name, kind, amount, account, days from today for next due, every, category, extra fields
REMINDERS = (
    ("Rent", "bill", "1,150.00", "Everyday Checking", -3, "monthly", "Home", {}),
    ("Electric bill", "bill", "95.00", "Everyday Checking", -1, "monthly", "Utilities",
     {"about": True}),
    ("Internet", "bill", "55.00", "Everyday Checking", 2, "monthly", "Utilities", {}),
    ("Phone plan", "bill", "38.00", "Blue Card", 6, "monthly", "Utilities", {}),
    ("Car insurance", "bill", "112.50", "Everyday Checking", 11, "months", "Insurance",
     {"n": 3}),
    ("Paycheck", "income", "1,380.00", "Everyday Checking", 4, "biweekly", "Pay", {}),
    ("Dog walking", "income", "60.00", "Everyday Checking", 9, "weekly", "Side jobs", {}),
    ("Library fine", "bill", "4.50", "", 1, "once", "Fun", {"paid_other": True}),
    ("Streaming service", "bill", "11.99", "Blue Card", 21, "monthly", "Fun", {}),
    ("Savings top-up", "transfer", "300.00", "Everyday Checking", 14, "biweekly", "",
     {"to_account": "Rainy Day Savings"}),
    ("Water bill", "bill", "48.00", "Everyday Checking", 40, "months", "Utilities", {"n": 2}),
)
# name, kind, balance, rate, minimum, linked account, target days from today
DEBTS = (
    ("Blue Card", "card", "998.84", "21.99", "35.00", "Blue Card", 300),
    ("Car Loan", "loan", "6,400.00", "6.49", "210.00", "Car Loan", 700),
    ("Dental Payment Plan", "other", "900.00", "0", "75.00", "", 150),
)
# debt name, days ago, balance: an older reading beside the opening one
EARLIER_BALANCES = (
    ("Dental Payment Plan", 60, "1,200.00"),
    ("Dental Payment Plan", 30, "1,050.00"),
    ("Car Loan", 45, "6,800.00"),
)


def seed(call, today: str, now_ms: int) -> None:
    """Make the sample household by the calls Books' own page makes."""
    base = date.fromisoformat(today)

    def day(offset: int) -> str:
        return (base + timedelta(days=offset)).isoformat()

    ids: dict[str, int] = {}
    for name, kind, opening, ago in ACCOUNTS:
        ids[name] = call("account.add", name=name, kind=kind, opening=opening,
                         opening_date=day(-ago))["id"]
    for name, income in CATEGORIES:
        call("category.add", name=name, income=bool(income))

    for account, ago, payee, pay, dep, category in ROWS:
        call("txn.add", account=ids[account], date=day(-ago), payee=payee, payment=pay,
             deposit=dep, category=category)
    for source, target, ago, amount in TRANSFERS:
        call("transfer.add", **{"from": ids[source], "to": ids[target], "date": day(-ago),
                                "amount": amount})
    # Some of the older checking rows are marked cleared, as after a statement.
    older = call("register", account=ids["Everyday Checking"])["rows"]
    for row in older:
        if row["date"] < day(-30):
            call("txn.clear", id=row["id"])

    for name, kind, amount, account, due, every, category, more in REMINDERS:
        args = {"name": name, "kind": kind, "amount": amount, "next_due": day(due),
                "every": every, "category": category, "payee": name}
        if account:
            args["account"] = ids[account]
        if "to_account" in more:
            args["to_account"] = ids[more["to_account"]]
        args.update({k: v for k, v in more.items() if k != "to_account"})
        call("reminder.add", **args)

    debt_ids: dict[str, int] = {}
    for name, kind, balance, rate, minimum, linked, target in DEBTS:
        args = {"name": name, "kind": kind, "balance": balance, "rate": rate,
                "minimum": minimum, "target_date": day(target), "on": day(-3)}
        if linked:
            args["account"] = ids[linked]
        debt_ids[name] = call("debt.add", **args)["id"]
    for name, ago, balance in EARLIER_BALANCES:
        call("debt.balance", id=debt_ids[name], balance=balance, on=day(-ago))
