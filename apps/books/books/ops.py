"""Accounts, categories and the register: every change the owner makes to them.

Each function takes what the page sent (``args``) and either returns plain JSON or
raises ``ValueError`` with a sentence that says nothing was changed and what to do. A
change to a reconciled row's amount, date or account is not refused but answered with
``{"needs_confirm": sentence}``: the page asks, and sends it again with ``confirm``.
"""

from __future__ import annotations

from typing import Any

from books import dates, ledger, money
from books.context import Ctx
from books.csvin import NUMBER_MOST  # a check number, or a reference such as "EFT 4411"
from books.store import Store

KINDS = {"checking": "Checking", "savings": "Savings", "credit_card": "Credit card",
         "cash": "Cash", "loan": "Loan"}
TRANSFER = "Transfer: "
STARTER = (("Groceries", 0), ("Eating out", 0), ("Home", 0), ("Utilities", 0),
           ("Transport", 0), ("Health", 0), ("Insurance", 0), ("Shopping", 0),
           ("Fun", 0), ("Gifts", 0), ("Fees and interest", 0), ("Other spending", 0),
           ("Pay", 1), ("Other income", 1))
NOTHING = "Nothing was changed."


# --- reading what the page sent ---------------------------------------------------------------


def text(args: dict[str, Any], key: str, most: int, what: str, *, need: bool = False) -> str:
    value = args.get(key)
    out = "" if value is None else str(value).strip()
    if need and not out:
        raise ValueError(f"Type {what}. {NOTHING}")
    if len(out) > most:
        raise ValueError(f"{what[:1].upper()}{what[1:]} can be at most {most} characters. "
                         f"{NOTHING}")
    return out


def day_arg(args: dict[str, Any], key: str, what: str = "The date") -> str:
    return dates.parse_day(args.get(key), what).isoformat()


def need_account(store: Store, account_id: Any, *, open_only: bool = False) -> dict[str, Any]:
    acct = store.account(account_id)
    if acct is None:
        raise ValueError(f"That account is not in Books. Choose another. {NOTHING}")
    if open_only and acct["closed"]:
        raise ValueError(f"{acct['name']} is closed. Open it again under Accounts first. "
                         f"{NOTHING}")
    return acct


def need_txn(store: Store, txn_id: Any) -> dict[str, Any]:
    row = store.txn(txn_id)
    if row is None:
        raise ValueError(f"That row is no longer in the register. {NOTHING}")
    return row


def not_before_opening(acct: dict[str, Any], day: str) -> None:
    """Rule ``a_row_is_not_dated_before_its_accounts_opening``: a row dated before the
    day the account's opening balance is counted from would make the balance wrong."""
    if day < str(acct["opening_date"]):
        raise ValueError(f"{acct['name']} starts on {acct['opening_date']}, so a row dated "
                         f"{day} cannot go in it. Nothing was saved. Use a date on or after "
                         f"{acct['opening_date']}, or change the account's opening date and "
                         f"balance under Accounts first.")


def amount_from(args: dict[str, Any], style: str) -> int:
    """Rule ``a_row_has_a_payment_or_a_deposit``: the row's signed cents from its two
    boxes, exactly one of them filled in."""
    paid = str(args.get("payment") or "").strip()
    came = str(args.get("deposit") or "").strip()
    if paid and came:
        raise ValueError(f"Fill in a payment or a deposit, not both. {NOTHING}")
    if not paid and not came:
        raise ValueError(f"Type the amount as a payment or a deposit. {NOTHING}")
    cents = money.parse_cents(paid or came, style)
    if cents < 0:
        raise ValueError(f"Type the amount without a minus sign: money out goes under the "
                         f"payment, money in under the deposit. {NOTHING}")
    return -cents if paid else cents


def confirm(args: dict[str, Any]) -> bool:
    return args.get("confirm") is True


# --- categories ----------------------------------------------------------------------------


def seed(store: Store, now: int) -> None:
    """The starter categories, once, the first time Books opens."""
    if store.get_meta("seeded"):
        return
    with store.tx():
        for name, income in STARTER:
            if store.category_named(name) is None:
                store.insert("category", {"name": name, "income": income, "created": now})
        store.set_meta("seeded", True)


def category_name(args: dict[str, Any], key: str = "name") -> str:
    name = text(args, key, 60, "a name for the category", need=True)
    if name.lower().startswith(TRANSFER.strip().lower()):
        raise ValueError(f"A category cannot start with \"Transfer\": that word is how the "
                         f"register moves money between your accounts. {NOTHING}")
    if name.lower() in ("uncategorised", "uncategorized"):
        raise ValueError(f"Uncategorised is kept by Books for rows with no category. Choose "
                         f"another name. {NOTHING}")
    return name


def add_category(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    name = category_name(args)
    with store.tx():
        if store.category_named(name):
            raise ValueError(f"You already have a category called {name}. {NOTHING}")
        new = store.insert("category", {"name": name, "income": int(bool(args.get("income"))),
                                        "created": ctx.now()})
    return {"id": new, "said": f"{name} is added."}


def rename_category(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    cat = store.category(args.get("id"))
    if cat is None:
        raise ValueError(f"That category is not in Books. {NOTHING}")
    name = category_name(args)
    with store.tx():
        same = store.category_named(name)
        if same and same["id"] != cat["id"]:
            raise ValueError(f"You already have a category called {name}. To put the two "
                             f"together, use Merge. {NOTHING}")
        income = cat["income"] if args.get("income") is None else int(bool(args.get("income")))
        store.update("category", cat["id"], name=name, income=income)
    return {"said": f"Saved as {name}."}


def merge_category(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``merging_moves_every_row``: every row and reminder in one category moves to
    another, and the first is gone."""
    store = ctx.store
    source, into = store.category(args.get("id")), store.category(args.get("into"))
    if source is None or into is None:
        raise ValueError(f"Choose two categories that are in Books. {NOTHING}")
    if source["id"] == into["id"]:
        raise ValueError(f"Choose a different category to merge {source['name']} into. "
                         f"{NOTHING}")
    with store.tx():
        moved = store.run("UPDATE txn SET category_id=? WHERE category_id=?",
                          (into["id"], source["id"]))
        store.run("UPDATE reminder SET category_id=? WHERE category_id=?",
                  (into["id"], source["id"]))
        store.run("DELETE FROM category WHERE id=?", (source["id"],))
    return {"said": f"{source['name']} is merged into {into['name']}: "
                    f"{moved} row{'s' if moved != 1 else ''} moved."}


def resolve_category(ctx: Ctx, name: str) -> tuple[int | None, str]:
    """A category typed in the register: its id, creating it when it is new. Returns the
    id and a sentence when one was made. Must be called inside ``tx``."""
    if not name:
        return None, ""
    found = ctx.store.category_named(name)
    if found:
        return int(found["id"]), ""
    clean = category_name({"name": name})
    new = ctx.store.insert("category", {"name": clean, "income": 0, "created": ctx.now()})
    return new, f"The category {clean} is new, so it was added to your list."


# --- accounts ------------------------------------------------------------------------------


def kind_arg(args: dict[str, Any]) -> str:
    kind = str(args.get("kind") or "")
    if kind not in KINDS:
        raise ValueError(f"Choose what kind of account it is. {NOTHING}")
    return kind


def add_account(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    name = text(args, "name", 60, "a name for the account", need=True)
    kind = kind_arg(args)
    opening = str(args.get("opening") or "").strip() or "0"
    cents = money.from_shown(kind, money.parse_cents(opening, ctx.style()))
    day = (dates.parse_day(args["opening_date"], "The date of the opening balance").isoformat()
           if str(args.get("opening_date") or "").strip() else ctx.today().isoformat())
    with store.tx():
        if store.one("SELECT id FROM account WHERE name=? COLLATE NOCASE", (name,)):
            raise ValueError(f"You already have an account called {name}. {NOTHING}")
        now = ctx.now()
        new = store.insert("account", {"name": name, "kind": kind, "opening_cents": cents,
                                       "opening_date": day, "closed": 0, "created": now,
                                       "updated": now})
    return {"id": new, "said": f"{name} is added."}


def edit_account(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    acct = need_account(store, args.get("id"))
    changes: dict[str, Any] = {}
    if "name" in args:
        name = text(args, "name", 60, "a name for the account", need=True)
        same = store.one("SELECT id FROM account WHERE name=? COLLATE NOCASE", (name,))
        if same and same["id"] != acct["id"]:
            raise ValueError(f"You already have an account called {name}. {NOTHING}")
        changes["name"] = name
    kind = acct["kind"]
    if "kind" in args:
        kind = kind_arg(args)
        if money.owed(kind) != money.owed(acct["kind"]):
            raise ValueError(f"An account you owe on (a card or a loan) cannot become one "
                             f"that holds money, or the other way round, because every "
                             f"balance would turn over. Add a new account instead. {NOTHING}")
        changes["kind"] = kind
    if "opening" in args:
        changes["opening_cents"] = money.from_shown(
            kind, money.parse_cents(str(args.get("opening") or "0"), ctx.style()))
    if "opening_date" in args:
        changes["opening_date"] = day_arg(args, "opening_date",
                                          "The date of the opening balance")
    if "closed" in args:
        changes["closed"] = int(bool(args.get("closed")))
    if not changes:
        return {"said": "Nothing to change."}
    with store.tx():
        store.update("account", acct["id"], **changes)
    return {"said": "Saved."}


def delete_account(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    acct = need_account(store, args.get("id"))
    if store.one("SELECT id FROM txn WHERE account_id=? LIMIT 1", (acct["id"],)):
        raise ValueError(f"{acct['name']} still has rows in its register. Close it instead, "
                         f"which keeps its history. {NOTHING}")
    if store.one("SELECT id FROM reminder WHERE account_id=? OR to_account_id=? LIMIT 1",
                 (acct["id"], acct["id"])):
        raise ValueError(f"A bill or income reminder still uses {acct['name']}. Change or "
                         f"delete the reminder first. {NOTHING}")
    if store.one("SELECT id FROM debt WHERE account_id=? LIMIT 1", (acct["id"],)):
        raise ValueError(f"A debt is still linked to {acct['name']}. Change or delete the "
                         f"debt first. {NOTHING}")
    with store.tx():
        store.run("DELETE FROM reconciliation WHERE account_id=?", (acct["id"],))
        store.run("DELETE FROM import_batch WHERE account_id=?", (acct["id"],))
        store.run("DELETE FROM auto_post WHERE account_id=? OR to_account_id=?",
                  (acct["id"], acct["id"]))
        store.run("DELETE FROM account WHERE id=?", (acct["id"],))
    store.wipe_log()
    return {"said": f"{acct['name']} is deleted."}


def summary(ctx: Ctx, acct: dict[str, Any], rows: list[dict[str, Any]] | None = None
            ) -> dict[str, Any]:
    """One account's balances: today, after every row (dated later too), and cleared,
    as held (``*_cents``) and as the screen shows them (``*_text``)."""
    store, style = ctx.store, ctx.style()
    rows = store.txns(acct["id"]) if rows is None else rows
    opening = int(acct["opening_cents"])
    today = ctx.today().isoformat()
    now_c = ledger.balance_on(rows, opening, today)
    end_c = ledger.balance(rows, opening)
    clear_c = ledger.cleared_balance(rows, opening)
    kind = acct["kind"]

    def told(cents: int) -> dict[str, Any]:
        seen = money.shown(kind, cents)
        return {"cents": cents, "text": money.format_cents(seen, style), "neg": seen < 0}

    return {"id": acct["id"], "name": acct["name"], "kind": kind, "kind_words": KINDS[kind],
            "owed": money.owed(kind), "closed": bool(acct["closed"]),
            "opening_text": money.format_cents(money.shown(kind, opening), style),
            "opening_date": acct["opening_date"],
            "today": told(now_c), "ending": told(end_c), "cleared": told(clear_c),
            "later": end_c != now_c, "rows": len(rows)}


# --- the register ---------------------------------------------------------------------------


def _other_side(store: Store, row: dict[str, Any]) -> dict[str, Any] | None:
    return store.txn(row["transfer_id"]) if row.get("transfer_id") else None


def transfer_target(ctx: Ctx, category: str) -> dict[str, Any] | None:
    """The account named by "Transfer: <account>" typed as a category, else None."""
    if not category.lower().startswith(TRANSFER.strip().lower()):
        return None
    name = category[len(TRANSFER.strip()):].strip()
    acct = ctx.store.one("SELECT * FROM account WHERE name=? COLLATE NOCASE", (name,))
    if acct is None:
        raise ValueError(f"There is no account called {name or 'that'} to move the money to. "
                         f"Choose one from the list. {NOTHING}")
    if acct["closed"]:
        raise ValueError(f"{acct['name']} is closed. Open it again first. {NOTHING}")
    return acct


def _remember_date(store: Store, day: str) -> None:
    store.set_meta("last_date", day)


def insert_pair(ctx: Ctx, out_of: dict[str, Any], into: dict[str, Any], day: str,
                 cents: int, memo: str, payee: str = "") -> tuple[int, int]:
    """Rule ``a_transfer_is_two_rows_that_sum_to_zero``: one row out of ``out_of``, one
    into ``into``, linked. ``cents`` is above zero. Must be called inside ``tx``."""
    store, now = ctx.store, ctx.now()
    base = {"date": day, "payee": payee, "category_id": None, "memo": memo,
            "state": "", "created": now, "updated": now}
    first = store.insert("txn", {**base, "account_id": out_of["id"], "amount_cents": -cents})
    second = store.insert("txn", {**base, "account_id": into["id"], "amount_cents": cents})
    store.update("txn", first, transfer_id=second)
    store.update("txn", second, transfer_id=first)
    return first, second


def add_txn(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``a_row_is_entered_in_one_go``: a new register row, or a transfer when the
    category says "Transfer: <account>"."""
    store = ctx.store
    acct = need_account(store, args.get("account"), open_only=True)
    day = day_arg(args, "date")
    payee = text(args, "payee", 200, "the payee")
    memo = text(args, "memo", 500, "the memo")
    number = text(args, "number", NUMBER_MOST, "the check number")
    category = text(args, "category", 80, "the category")
    cents = amount_from(args, ctx.style())
    target = transfer_target(ctx, category)
    not_before_opening(acct, day)
    with store.tx():
        _remember_date(store, day)
        if target is not None:
            if target["id"] == acct["id"]:
                raise ValueError(f"A transfer moves money to a different account. {NOTHING}")
            not_before_opening(target, day)
            pair = (acct, target) if cents < 0 else (target, acct)
            first, second = insert_pair(ctx, pair[0], pair[1], day, abs(cents), memo, payee)
            mine = first if cents < 0 else second
            if number:
                store.update("txn", mine, number=number)
            return {"id": mine, "said": f"Moved to {target['name']}." if cents < 0
                    else f"Moved from {target['name']}."}
        cat_id, made = resolve_category(ctx, category)
        now = ctx.now()
        new = store.insert("txn", {"account_id": acct["id"], "date": day, "payee": payee,
                                   "category_id": cat_id, "memo": memo, "amount_cents": cents,
                                   "number": number, "state": "", "created": now,
                                   "updated": now})
    return {"id": new, "said": made or "Added."}


def add_transfer(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    out_of = need_account(store, args.get("from"), open_only=True)
    into = need_account(store, args.get("to"), open_only=True)
    if out_of["id"] == into["id"]:
        raise ValueError(f"Choose two different accounts. {NOTHING}")
    day = day_arg(args, "date")
    cents = money.parse_cents(str(args.get("amount") or ""), ctx.style())
    if cents <= 0:
        raise ValueError(f"Type the amount to move, above zero. {NOTHING}")
    memo = text(args, "memo", 500, "the memo")
    not_before_opening(out_of, day)
    not_before_opening(into, day)
    with store.tx():
        _remember_date(store, day)
        first, second = insert_pair(ctx, out_of, into, day, cents, memo)
    return {"ids": [first, second], "said": f"Moved from {out_of['name']} to {into['name']}."}


def _reconciled_warning(rows: list[dict[str, Any]]) -> str:
    return ("This row is reconciled. Changing its amount, date or account means that "
            "reconciliation will no longer balance." if len(rows) == 1 else
            "One side of this transfer is reconciled. Changing its amount, date or account "
            "means that reconciliation will no longer balance.")


def edit_txn(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``a_transfer_stays_one_movement``: a change to one side's amount or date is
    made to the other side too."""
    store, style = ctx.store, ctx.style()
    row = need_txn(store, args.get("id"))
    other = _other_side(store, row)
    changes: dict[str, Any] = {}
    if "date" in args:
        changes["date"] = day_arg(args, "date")
    if "payment" in args or "deposit" in args:
        changes["amount_cents"] = amount_from(args, style)
    if "payee" in args:
        changes["payee"] = text(args, "payee", 200, "the payee")
    if "memo" in args:
        changes["memo"] = text(args, "memo", 500, "the memo")
    if "number" in args:
        changes["number"] = text(args, "number", NUMBER_MOST, "the check number")
    if "account" in args and int(args.get("account") or 0) != row["account_id"]:
        changes["account_id"] = need_account(store, args.get("account"), open_only=True)["id"]
    category = text(args, "category", 80, "the category") if "category" in args else None
    target = transfer_target(ctx, category) if category is not None else None

    touches_money = any(k in changes for k in ("date", "amount_cents", "account_id"))
    if target is not None and other is not None and target["id"] != other["account_id"]:
        touches_money = True
    if category is not None and target is None and other is not None:
        touches_money = True
    sides = [r for r in (row, other) if r is not None and r["state"] == "R"]
    if touches_money and sides and not confirm(args):
        return {"needs_confirm": _reconciled_warning([row] if row["state"] == "R" else sides)}

    accounts = {a["id"]: a for a in store.accounts()}
    final_day = changes.get("date", row["date"])
    final_mine = changes.get("account_id", row["account_id"])
    final_other = target["id"] if target is not None else (
        other["account_id"] if other is not None and category is None else None)
    moved_day = final_day != row["date"]
    if moved_day or final_mine != row["account_id"]:
        not_before_opening(accounts[final_mine], final_day)
    if final_other is not None and (
            moved_day or other is None or final_other != other["account_id"]):
        not_before_opening(accounts[final_other], final_day)

    with store.tx():
        made = ""
        mine_account = changes.get("account_id", row["account_id"])
        if category is not None:
            if target is not None:
                if target["id"] == mine_account:
                    raise ValueError(f"A transfer moves money to a different account. "
                                     f"{NOTHING}")
                changes["category_id"] = None
                if other is None:  # a plain row becomes a transfer
                    cents = changes.get("amount_cents", row["amount_cents"])
                    now = ctx.now()
                    new = store.insert("txn", {
                        "account_id": target["id"], "date": changes.get("date", row["date"]),
                        "payee": "", "category_id": None, "memo": changes.get("memo", row["memo"]),
                        "amount_cents": -cents, "state": "", "transfer_id": row["id"],
                        "created": now, "updated": now})
                    changes["transfer_id"] = new
                elif target["id"] != other["account_id"]:
                    store.update("txn", other["id"], account_id=target["id"])
            else:
                if other is not None:  # a transfer becomes a plain row
                    store.run("DELETE FROM txn WHERE id=?", (other["id"],))
                    changes["transfer_id"] = None
                    other = None
                changes["category_id"], made = resolve_category(ctx, category)
        if other is not None and changes.get("account_id") == other["account_id"]:
            raise ValueError(f"A transfer moves money to a different account. {NOTHING}")
        if changes:
            store.update("txn", row["id"], **changes)
        fresh = store.txn(row["id"]) or row
        if fresh.get("transfer_id"):
            store.update("txn", fresh["transfer_id"], amount_cents=-fresh["amount_cents"],
                         date=fresh["date"])
        if "date" in changes:
            _remember_date(store, changes["date"])
    if category is not None and target is None:
        store.wipe_log()
    return {"said": made or "Saved."}


def delete_txn(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``deleting_a_transfer_deletes_both_sides``."""
    store = ctx.store
    row = need_txn(store, args.get("id"))
    other = _other_side(store, row)
    sides = [r for r in (row, other) if r is not None and r["state"] == "R"]
    if sides and not confirm(args):
        return {"needs_confirm": "This row is reconciled. Deleting it means that "
                                 "reconciliation will no longer balance."}
    with store.tx():
        store.run("DELETE FROM txn WHERE id=?", (row["id"],))
        if other is not None:
            store.run("DELETE FROM txn WHERE id=?", (other["id"],))
    store.wipe_log()
    return {"said": "Both sides of the transfer are deleted." if other else "Deleted."}


def toggle_cleared(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``one_click_clears``: not cleared becomes cleared and back. A reconciled row
    asks first, and then goes back to not cleared."""
    store = ctx.store
    row = need_txn(store, args.get("id"))
    if row["state"] == "R" and not confirm(args):
        return {"needs_confirm": "This row is reconciled. Taking its mark off means that "
                                 "reconciliation will no longer balance."}
    state = "c" if row["state"] == "" else ""
    with store.tx():
        store.update("txn", row["id"], state=state)
    return {"state": state}


# --- what the register shows -----------------------------------------------------------------


def _names(store: Store) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    accounts = {a["id"]: a for a in store.accounts()}
    cats = {c["id"]: c["name"] for c in store.categories()}
    return accounts, cats


def label_row(row: dict[str, Any], accounts: dict[int, dict[str, Any]],
              cats: dict[int, str], others: dict[int, int]) -> tuple[str, str]:
    """A row's payee and category as the register shows them. A transfer's category is
    "Transfer: <the other account>", and with no payee its payee says where it went."""
    if row.get("transfer_id"):
        other = accounts.get(others.get(row["transfer_id"], -1), {})
        name = other.get("name", "another account")
        payee = row["payee"] or (f"Transfer to {name}" if row["amount_cents"] < 0
                                 else f"Transfer from {name}")
        return payee, f"{TRANSFER}{name}"
    return row["payee"], cats.get(row["category_id"], "") if row["category_id"] else ""


def matches(row: dict[str, Any], payee: str, q: str, cents: int | None) -> bool:
    """Rule ``search_finds_payee_memo_number_or_amount``."""
    if cents is not None and abs(int(row["amount_cents"])) == abs(cents):
        return True
    words = q.lower()
    return (words in payee.lower() or words in str(row["memo"]).lower()
            or words in str(row.get("number") or "").lower())


def register(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, style, cfg = ctx.store, ctx.style(), ctx.cfg()
    acct = need_account(store, args.get("account"))
    rows = store.txns(acct["id"])
    accounts, cats = _names(store)
    others = {r["id"]: r["account_id"] for r in store.all(
        "SELECT id, account_id FROM txn WHERE id IN (SELECT transfer_id FROM txn "
        "WHERE account_id=? AND transfer_id IS NOT NULL)", (acct["id"],))}
    kind = acct["kind"]
    q = str(args.get("q") or "").strip()[:100]
    try:
        q_cents = money.parse_cents(q, style) if q else None
    except ValueError:
        q_cents = None
    out = []
    for row, total in reversed(ledger.running(rows, acct["opening_cents"])):
        payee, category = label_row(row, accounts, cats, others)
        if q and not matches(row, payee, q, q_cents):
            continue
        seen = money.shown(kind, total)
        amount = int(row["amount_cents"])
        out.append({
            "id": row["id"], "date": row["date"], "payee": payee, "payee_typed": row["payee"],
            "category": category, "memo": row["memo"], "number": row["number"],
            "payment": money.format_cents(-amount, style) if amount < 0 else "",
            "deposit": money.format_cents(amount, style) if amount >= 0 else "",
            "balance": money.format_cents(seen, style), "neg": seen < 0,
            "state": row["state"], "transfer": bool(row["transfer_id"]),
            "imported": bool(row["import_id"]), "from_reminder": bool(row["reminder_id"])})
    limit = max(1, int(args.get("limit") or cfg.register_rows))
    return {
        "account": summary(ctx, acct, rows),
        "rows": out[:limit], "more": max(0, len(out) - limit), "found": len(out),
        "q": q, "last_date": store.get_meta("last_date") or ctx.today().isoformat(),
        "payees": payees(ctx),
        "categories": [c["name"] for c in store.categories()],
        "transfers": [f"{TRANSFER}{a['name']}" for a in accounts.values()
                      if a["id"] != acct["id"] and not a["closed"]],
    }


def payees(ctx: Ctx, most: int = 2000) -> list[dict[str, Any]]:
    """Rule ``a_payee_fills_in_what_it_had_last_time``: each payee used before, newest
    first, with the category and amount of its last row. Transfers are never offered."""
    store, style = ctx.store, ctx.style()
    cats = {c["id"]: c["name"] for c in store.categories()}
    seen: dict[str, dict[str, Any]] = {}
    for row in store.all("SELECT payee, category_id, amount_cents FROM txn WHERE "
                         "transfer_id IS NULL AND payee != '' ORDER BY date DESC, id DESC "
                         "LIMIT 20000"):
        key = ledger.payee_key(row["payee"])
        if not key or key in seen:
            continue
        amount = int(row["amount_cents"])
        seen[key] = {"name": row["payee"],
                     "category": cats.get(row["category_id"], "") if row["category_id"] else "",
                     "payment": money.format_cents(-amount, style) if amount < 0 else "",
                     "deposit": money.format_cents(amount, style) if amount >= 0 else ""}
        if len(seen) >= most:
            break
    return list(seen.values())
