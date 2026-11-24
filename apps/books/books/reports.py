"""Spending by category for a month, beside the month before, with income kept apart."""

from __future__ import annotations

from typing import Any

from books import ledger, money
from books.context import Ctx
from books.ops import NOTHING, label_row

UNCATEGORISED_WORDS = "Uncategorised"


def _rows_between(ctx: Ctx, first: str, last: str) -> list[dict[str, Any]]:
    return ctx.store.all("SELECT * FROM txn WHERE date >= ? AND date <= ? ORDER BY date, id",
                         (first, last))


def spending(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, style = ctx.store, ctx.style()
    month = str(args.get("month") or ctx.today().isoformat()[:7])
    first, last = ledger.month_bounds(month)
    before = ledger.previous_month(month)
    p_first, p_last = ledger.month_bounds(before)
    cats = store.categories()
    income_ids = {c["id"] for c in cats if c["income"]}
    spend, earn = ledger.totals_by_category(_rows_between(ctx, first, last), income_ids)
    p_spend, p_earn = ledger.totals_by_category(_rows_between(ctx, p_first, p_last), income_ids)

    def lines(now: dict[int, int], then: dict[int, int], income: bool) -> list[dict[str, Any]]:
        out = []
        for c in cats:
            if bool(c["income"]) != income:
                continue
            a, b = now.get(c["id"], 0), then.get(c["id"], 0)
            if a or b:
                out.append({"id": c["id"], "name": c["name"], "cents": a,
                            "text": money.format_cents(a, style),
                            "before": money.format_cents(b, style)})
        a, b = now.get(ledger.UNCATEGORISED, 0), then.get(ledger.UNCATEGORISED, 0)
        if a or b:  # Rule ``uncategorised_is_shown_when_not_zero``
            out.append({"id": ledger.UNCATEGORISED, "name": UNCATEGORISED_WORDS, "cents": a,
                        "text": money.format_cents(a, style),
                        "before": money.format_cents(b, style)})
        return sorted(out, key=lambda r: (-r["cents"], r["name"].lower()))

    def total(values: dict[int, int]) -> str:
        return money.format_cents(sum(values.values()), style)

    return {"month": month, "before": before,
            "spending": lines(spend, p_spend, False), "income": lines(earn, p_earn, True),
            "spent": total(spend), "spent_before": total(p_spend),
            "earned": total(earn), "earned_before": total(p_earn)}


def category_rows(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The rows behind one line of the spending table."""
    store, style = ctx.store, ctx.style()
    month = str(args.get("month") or "")
    first, last = ledger.month_bounds(month)
    try:
        cat = int(args.get("category"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"Choose a category. {NOTHING}") from None
    income = str(args.get("side") or "spending") == "income"
    if cat == ledger.UNCATEGORISED:
        sign = ">=" if income else "<"
        rows = store.all(f"SELECT * FROM txn WHERE date>=? AND date<=? AND category_id IS NULL "  # noqa: S608
                         f"AND transfer_id IS NULL AND amount_cents {sign} 0 ORDER BY date, id",
                         (first, last))
    else:
        rows = store.all("SELECT * FROM txn WHERE date>=? AND date<=? AND category_id=? "
                         "AND transfer_id IS NULL ORDER BY date, id", (first, last, cat))
    accounts = {a["id"]: a for a in store.accounts()}
    cats = {c["id"]: c["name"] for c in store.categories()}
    return {"rows": [{"id": r["id"], "date": r["date"],
                      "account": accounts.get(r["account_id"], {}).get("name", ""),
                      "account_id": r["account_id"],
                      "payee": label_row(r, accounts, cats, {})[0], "memo": r["memo"],
                      "amount": money.format_cents(r["amount_cents"], style)} for r in rows]}
