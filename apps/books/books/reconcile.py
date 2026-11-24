"""Reconciling an account against a statement.

The owner types the statement's end date and ending balance. Books lists every row not
yet reconciled up to that date; each tick clears or uncleares one. The cleared balance is
the opening balance, every reconciled row, and every cleared row up to the end date.
Finish is allowed only when the statement balance less the cleared balance is exactly
zero: then the cleared rows up to that date become reconciled, together with the record
of the reconciliation, in one transaction.
"""

from __future__ import annotations

from typing import Any

from books import dates, ledger, money
from books.context import Ctx
from books.ops import KINDS, NOTHING, label_row, need_account, need_txn


def _inputs(ctx: Ctx, args: dict[str, Any]) -> tuple[dict[str, Any], str, int]:
    acct = need_account(ctx.store, args.get("account"))
    through = dates.parse_day(args.get("through"), "The statement's end date").isoformat()
    typed = str(args.get("statement") or "").strip()
    if not typed:
        raise ValueError("Type the statement's ending balance.")
    statement = money.from_shown(acct["kind"], money.parse_cents(typed, ctx.style()))
    return acct, through, statement


def view(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, style = ctx.store, ctx.style()
    acct, through, statement = _inputs(ctx, args)
    rows = store.txns(acct["id"])
    cleared = ledger.cleared_balance(rows, acct["opening_cents"], through)
    diff = ledger.reconcile_difference(statement, cleared)
    kind = acct["kind"]
    accounts = {a["id"]: a for a in store.accounts()}
    cats = {c["id"]: c["name"] for c in store.categories()}
    others = {r["id"]: r["account_id"] for r in store.all(
        "SELECT id, account_id FROM txn WHERE id IN (SELECT transfer_id FROM txn "
        "WHERE account_id=? AND transfer_id IS NOT NULL)", (acct["id"],))}
    listed = []
    for row in ledger.to_reconcile(rows, through):
        payee, category = label_row(row, accounts, cats, others)
        amount = int(row["amount_cents"])
        listed.append({"id": row["id"], "date": row["date"], "payee": payee,
                       "category": category,
                       "payment": money.format_cents(-amount, style) if amount < 0 else "",
                       "deposit": money.format_cents(amount, style) if amount >= 0 else "",
                       "ticked": row["state"] == "c"})

    def told(cents: int) -> dict[str, Any]:
        seen = money.shown(kind, cents)
        return {"text": money.format_cents(seen, style), "neg": seen < 0}

    return {"account": acct["id"], "name": acct["name"], "kind_words": KINDS[kind],
            "owed": money.owed(kind), "through": through, "rows": listed,
            "cleared": told(cleared), "statement": told(statement),
            "difference": {"text": money.format_cents(money.shown(kind, diff), style),
                           "cents": diff, "zero": diff == 0},
            "can_finish": diff == 0, "history": history(ctx, acct["id"])}


def tick(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Clear or unclear one listed row, and the reconcile as it now stands."""
    store = ctx.store
    acct, through, _ = _inputs(ctx, args)
    row = need_txn(store, args.get("id"))
    if row["account_id"] != acct["id"] or row["state"] == "R" or row["date"] > through:
        raise ValueError(f"That row is not one of this reconcile's. {NOTHING}")
    with store.tx():
        store.update("txn", row["id"], state="" if row["state"] == "c" else "c")
    return view(ctx, args)


def finish(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``reconcile_finishes_only_at_zero``."""
    store = ctx.store
    with store.tx():
        acct, through, statement = _inputs(ctx, args)
        rows = store.txns(acct["id"])
        diff = ledger.reconcile_difference(
            statement, ledger.cleared_balance(rows, acct["opening_cents"], through))
        if diff != 0:
            shown = money.format_cents(money.shown(acct["kind"], diff), ctx.style())
            raise ValueError(f"The difference is {shown}, not zero, so the reconcile was not "
                             f"finished. Tick the rows on your statement until it is zero.")
        locked = store.run("UPDATE txn SET state='R', updated=? WHERE account_id=? AND "
                           "state='c' AND date<=?", (ctx.now(), acct["id"], through))
        store.insert("reconciliation", {"account_id": acct["id"], "through": through,
                                        "statement_cents": statement, "row_count": locked,
                                        "done_ms": ctx.now()})
    return {"said": f"{acct['name']} is reconciled to {through}: {locked} row"
                    f"{'s' if locked != 1 else ''} locked."}


def history(ctx: Ctx, account_id: int) -> list[dict[str, Any]]:
    acct = ctx.store.account(account_id) or {"kind": "checking"}
    return [{"through": r["through"], "rows": r["row_count"], "done_ms": r["done_ms"],
             "statement": money.format_cents(money.shown(acct["kind"], r["statement_cents"]),
                                             ctx.style())}
            for r in ctx.store.all("SELECT * FROM reconciliation WHERE account_id=? "
                                   "ORDER BY through DESC, id DESC", (account_id,))]


def start(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """What the reconcile form starts from: the last statement's date and balance."""
    acct = need_account(ctx.store, args.get("account"))
    past = history(ctx, acct["id"])
    return {"account": acct["id"], "name": acct["name"], "owed": money.owed(acct["kind"]),
            "history": past, "last": past[0] if past else None}
