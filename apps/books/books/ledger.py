"""The arithmetic of a register, as small pure functions over plain rows.

A row is a dict with at least ``id``, ``date`` (``YYYY-MM-DD``) and ``amount_cents``
(money in is above zero, money out below), and for clearing ``state``: ``""`` not
cleared, ``"c"`` cleared, ``"R"`` reconciled. Nothing here reads the store or the clock.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

CLEARED = ("c", "R")


def order_key(row: dict[str, Any]) -> tuple[str, int]:
    """Rule ``rows_run_by_date_then_entry_order``: a register's order."""
    return (str(row["date"]), int(row["id"]))


def running(rows: Iterable[dict[str, Any]], opening: int) -> list[tuple[dict[str, Any], int]]:
    """Rule ``each_row_carries_the_balance_after_it``: the rows in register order, each
    with the balance once it is counted. Worked out afresh from the opening balance every
    time, so an edit, a row put in the past or a deletion can never leave one stale."""
    total = int(opening)
    out = []
    for row in sorted(rows, key=order_key):
        total += int(row["amount_cents"])
        out.append((row, total))
    return out


def balance_on(rows: Iterable[dict[str, Any]], opening: int, day: str) -> int:
    """The balance at the end of ``day``: rows dated later are not counted yet."""
    return int(opening) + sum(int(r["amount_cents"]) for r in rows if str(r["date"]) <= day)


def balance(rows: Iterable[dict[str, Any]], opening: int) -> int:
    return int(opening) + sum(int(r["amount_cents"]) for r in rows)


def cleared_balance(rows: Iterable[dict[str, Any]], opening: int,
                    through: str | None = None) -> int:
    """Rule ``the_cleared_balance_counts_what_the_bank_has_seen``: the opening balance and
    every reconciled row, and every cleared row dated up to ``through`` (all of them when
    ``through`` is None)."""
    total = int(opening)
    for r in rows:
        if r.get("state") == "R" or (r.get("state") == "c" and
                                     (through is None or str(r["date"]) <= through)):
            total += int(r["amount_cents"])
    return total


def reconcile_difference(statement: int, cleared: int) -> int:
    """Rule ``reconcile_finishes_only_at_zero``: what is left to explain. Finishing is
    allowed only when this is exactly 0."""
    return int(statement) - int(cleared)


def to_reconcile(rows: Iterable[dict[str, Any]], through: str) -> list[dict[str, Any]]:
    """The rows a reconcile up to ``through`` lists: every one not yet reconciled, dated
    up to that day, newest first (the ones being ticked off a statement are recent)."""
    found = [r for r in rows if r.get("state") != "R" and str(r["date"]) <= through]
    return sorted(found, key=order_key, reverse=True)


# --- payees and duplicates ---------------------------------------------------------------------


def payee_key(name: str) -> str:
    """Rule ``one_payee_however_it_was_typed``: "Corner Shop", "corner-shop" and
    "CORNER SHOP " are one payee: lower case, letters and digits only."""
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def dup_key(account_id: int, day: str, amount: int, payee: str) -> tuple[int, str, int, str]:
    return (int(account_id), str(day), int(amount), payee_key(payee))


def split_duplicates(have: Iterable[tuple], incoming: list[tuple]) -> list[bool]:
    """Rule ``an_import_skips_what_the_register_already_has``: for each incoming key, True
    when it is a duplicate. A key the register already holds twice skips two copies in
    the file and no more, so two real coffees on one day at one price both arrive the
    first time and neither arrives twice."""
    left = Counter(have)
    out = []
    for key in incoming:
        if left[key] > 0:
            left[key] -= 1
            out.append(True)
        else:
            out.append(False)
    return out


# --- spending --------------------------------------------------------------------------------


def month_bounds(month: str) -> tuple[str, str]:
    """``2026-02`` as its first and last day."""
    if not re.fullmatch(r"\d{4}-\d{2}", str(month or "")):
        raise ValueError("The month must look like 2026-03.")
    y, m = int(month[:4]), int(month[5:])
    if not 1 <= m <= 12:
        raise ValueError("The month must look like 2026-03.")
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def previous_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    return f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"


UNCATEGORISED = 0


def totals_by_category(rows: Iterable[dict[str, Any]],
                       income_ids: set[int]) -> tuple[dict[int, int], dict[int, int]]:
    """Rule ``spending_leaves_out_transfers_and_keeps_income_apart``: (spending, income),
    each by category id, in cents above zero for money spent or earned.

    A transfer (a row with ``transfer_id``) is the owner's own money moving and counts in
    neither. A row in an income category counts as income (a refund in a spending
    category lowers that category's spending). A row with no category is
    ``UNCATEGORISED``: spending when money went out, income when it came in."""
    spend: dict[int, int] = {}
    earn: dict[int, int] = {}
    for r in rows:
        if r.get("transfer_id"):
            continue
        amount = int(r["amount_cents"])
        cat = r.get("category_id")
        if cat is None:
            if amount < 0:
                spend[UNCATEGORISED] = spend.get(UNCATEGORISED, 0) - amount
            else:
                earn[UNCATEGORISED] = earn.get(UNCATEGORISED, 0) + amount
        elif int(cat) in income_ids:
            earn[int(cat)] = earn.get(int(cat), 0) + amount
        else:
            spend[int(cat)] = spend.get(int(cat), 0) - amount
    return spend, earn


# --- looking ahead ---------------------------------------------------------------------------


def look_ahead(start_balance: int, today: date, days: int,
               events: Iterable[tuple[date, int, str]]) -> dict[str, Any]:
    """Rule ``the_forward_look_finds_the_lowest_point``: the balance at the end of each day
    from ``today`` for ``days`` more days, from ``start_balance`` plus each event
    ``(day, cents, name)``. An event dated before today (a reminder overdue) counts today;
    one after the window is left out. The lowest point is the first day the balance is
    at its lowest."""
    end = today + timedelta(days=days)
    by_day: dict[date, list[tuple[int, str]]] = {}
    for day, cents, name in events:
        when = max(day, today)
        if when <= end:
            by_day.setdefault(when, []).append((int(cents), name))
    out = []
    total = int(start_balance)
    low_day, low = today, None
    cur = today
    while cur <= end:
        happening = by_day.get(cur, [])
        total += sum(c for c, _ in happening)
        out.append({"date": cur.isoformat(), "balance": total,
                    "events": [{"name": n, "cents": c} for c, n in happening]})
        if low is None or total < low:
            low_day, low = cur, total
        cur += timedelta(days=1)
    lowest = total if low is None else low
    return {"days": out, "low": {"date": low_day.isoformat(), "balance": lowest}}
