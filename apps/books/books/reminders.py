"""Bills and income reminders: what is due, posting one into the register, skipping one.

A reminder keeps its next due date (``next_due``) and its schedule (``every`` with
``day``, ``day2`` and ``n``; see ``dates.Rule``). Posting writes the register row and
moves the reminder on in one transaction, and only for the due date the page showed: a
second press of Post for the same date finds the reminder already moved and changes
nothing. A reminder that comes once has no next date after it is posted or skipped.

A reminder is a ``bill`` (money out), ``income`` (money in) or a ``transfer`` between two
of the owner's accounts. A bill or income may be "paid another way" (cash, a card Books
does not track): it has no account, posting it only moves it to its next date, and it adds
no register row and changes no balance. A transfer, when posted, makes the linked pair of
rows that a transfer always is.

A reminder may be marked ``auto``: "this happens by itself" (a direct debit, a salary). The
scheduled job then posts it on or after its due date (``auto.py``). It cannot also be
"about this much": the amount of one that is not sure is not something to post.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from books import dates, ledger, money
from books.context import Ctx
from books.ops import (
    NOTHING,
    day_arg,
    insert_pair,
    need_account,
    not_before_opening,
    resolve_category,
    text,
)
from books.store import Store

KINDS = ("bill", "income", "transfer")
OTHER_WAY = "Paid another way"
NOTE_MOST = 500


def rule_of(rem: dict[str, Any]) -> dates.Rule:
    return dates.Rule(rem["every"], day=int(rem["day"]), day2=int(rem["day2"]), n=int(rem["n"]))


def pays_itself(rem: dict[str, Any], enabled: bool) -> bool:
    """Whether this reminder will be posted without the owner: it is marked, its amount is
    sure, the owner has not switched the whole thing off, and it is not stopped."""
    return bool(enabled and rem["auto"] and not rem["about"] and not rem["auto_said"])


def signed(rem: dict[str, Any], account_id: int | None = None) -> int:
    """A reminder's amount as a register row: a bill is money out. A transfer is money out
    of its account and money in to its other one, as ``account_id`` (when given) sees it."""
    cents = abs(int(rem["amount_cents"]))
    if rem["kind"] == "transfer":
        return cents if account_id is not None and account_id == rem["to_account_id"] else -cents
    return -cents if rem["kind"] == "bill" else cents


def _fields(ctx: Ctx, args: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any]:
    store = ctx.store
    name = text(args, "name", 80, "a name, such as Water bill", need=True)
    kind = str(args.get("kind") or "bill")
    if kind not in KINDS:
        raise ValueError(f"Choose whether it is a bill, income or a transfer. {NOTHING}")
    cents = money.parse_cents(str(args.get("amount") or ""), ctx.style())
    if cents < 0:
        raise ValueError(f"Type the amount without a minus sign: a bill is always money out. "
                         f"{NOTHING}")
    other_way = bool(args.get("paid_other"))
    about, auto = bool(args.get("about")), bool(args.get("auto"))
    if about and auto:
        raise ValueError(f"A reminder whose amount is only about this much cannot happen by "
                         f"itself, because Books would not know what to post. Untick one of "
                         f"the two, or type the exact amount. {NOTHING}")
    acct: dict[str, Any] | None = None
    into: dict[str, Any] | None = None
    if kind == "transfer":
        if other_way:
            raise ValueError(f"A transfer is between two of your accounts, so it cannot be paid "
                             f"another way. {NOTHING}")
        acct = need_account(store, args.get("account"), open_only=True)
        into = need_account(store, args.get("to_account"), open_only=True)
        if acct["id"] == into["id"]:
            raise ValueError(f"A transfer moves money to a different account. Choose two "
                             f"accounts. {NOTHING}")
    elif not other_way:
        acct = need_account(store, args.get("account"), open_only=True)
    first = dates.parse_day(args.get("next_due"), "The next due date")
    every = str(args.get("every") or "")
    day2 = args.get("day2")
    n = args.get("n")
    try:
        day2_n = int(day2) if day2 not in (None, "") else 0
        n_n = int(n) if n not in (None, "") else 1
    except (TypeError, ValueError):
        raise ValueError(f"The days and months must be whole numbers. {NOTHING}") from None
    if every == "twice_monthly" and not day2_n:
        raise ValueError(f"Twice a month needs its second day of the month. {NOTHING}")
    rule = dates.rule_from(every, first, day2=day2_n, n=n_n)
    problem = dates.rule_problem(rule)
    if problem:
        raise ValueError(f"{problem} {NOTHING}")
    if every == "twice_monthly" and first.day not in (rule.day, rule.day2):
        raise ValueError(f"The next due date should be on one of its two days. {NOTHING}")
    if old is not None and old["next_due"] == first.isoformat() and old["every"] == every and (
            every != "twice_monthly" or day2_n in (old["day"], old["day2"])) and (
            every != "months" or n_n == old["n"]):
        # Unchanged date and schedule: keep its own day (a 31st shown on the 30th).
        rule = rule_of(old)
    return {"name": name, "payee": text(args, "payee", 200, "the payee"),
            "amount_cents": cents, "about": int(about), "auto": int(auto),
            "note": text(args, "note", NOTE_MOST, "the note"),
            "account_id": acct["id"] if acct else None,
            "to_account_id": into["id"] if into else None, "paid_other": int(other_way),
            "kind": kind, "every": every, "day": rule.day,
            "day2": rule.day2, "n": rule.n, "next_due": first.isoformat(),
            "category": "" if kind == "transfer" else text(args, "category", 80,
                                                           "the category")}


def add(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    values = _fields(ctx, args, None)
    with store.tx():
        values["category_id"], made = resolve_category(ctx, values.pop("category"))
        now = ctx.now()
        new = store.insert("reminder", {**values, "created": now, "updated": now})
    return {"id": new, "said": made or f"{values['name']} is added."}


def edit(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    rem = need_reminder(store, args.get("id"))
    values = _fields(ctx, args, rem)
    if not values["auto"]:
        values.update(auto_held=0, auto_said="")  # nothing is stopped that was never to run
    with store.tx():
        values["category_id"], made = resolve_category(ctx, values.pop("category"))
        store.update("reminder", rem["id"], **values)
    moved = rem["next_due"] != values["next_due"]
    return {"said": made or "Saved.", "card_id": rem["card_id"] if moved else None}


def delete(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    rem = need_reminder(store, args.get("id"))
    with store.tx():
        store.run("UPDATE txn SET reminder_id=NULL, posted_due=NULL WHERE reminder_id=?",
                  (rem["id"],))
        store.run("DELETE FROM auto_post WHERE reminder_id=?", (rem["id"],))
        store.run("DELETE FROM reminder WHERE id=?", (rem["id"],))
    store.wipe_log()
    return {"said": f"{rem['name']} is deleted. Rows it posted stay in the register.",
            "card_id": rem["card_id"]}


def need_reminder(store: Store, reminder_id: Any) -> dict[str, Any]:
    rem = store.reminder(reminder_id)
    if rem is None:
        raise ValueError(f"That reminder is not in Books. {NOTHING}")
    return rem


def _same_due(rem: dict[str, Any], args: dict[str, Any], doing: str) -> str:
    due = str(args.get("due") or "")
    if rem["next_due"] is None or rem["next_due"] != due:
        raise ValueError(f"{rem['name']} for {due or 'that date'} was already posted or "
                         f"skipped, so nothing was {doing} again.")
    return due


def post(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``posting_a_bill_twice_changes_nothing_the_second_time``: the register row
    and the next due date, together, for the due date the page showed."""
    store = ctx.store
    with store.tx():
        rem = need_reminder(store, args.get("id"))
        due = _same_due(rem, args, "posted")
        if rem["paid_other"]:
            # Rule ``a_reminder_paid_another_way_adds_no_row``: marked paid and moved on.
            nxt = dates.next_due(rule_of(rem), date.fromisoformat(due))
            store.update("reminder", rem["id"], next_due=nxt.isoformat() if nxt else None)
            return {"id": None, "card_id": rem["card_id"],
                    "said": f"{rem['name']} is marked paid. No row was added to any register."
                            + (f" Next due {nxt.isoformat()}." if nxt else "")}
        acct = need_account(store, rem["account_id"], open_only=True)
        day = day_arg(args, "date") if args.get("date") else due
        not_before_opening(acct, day)
        cents = signed(rem)
        if str(args.get("amount") or "").strip():
            typed = money.parse_cents(str(args["amount"]), ctx.style())
            if typed < 0:
                raise ValueError(f"Type the amount without a minus sign. {NOTHING}")
            cents = -typed if rem["kind"] != "income" else typed
        if rem["kind"] == "transfer":
            return _post_transfer(ctx, rem, acct, due, day, abs(cents))
        now = ctx.now()
        new = store.insert("txn", {
            "account_id": acct["id"], "date": day, "payee": rem["payee"] or rem["name"],
            "category_id": rem["category_id"], "memo": "", "amount_cents": cents,
            "state": "", "reminder_id": rem["id"], "posted_due": due,
            "created": now, "updated": now})
        nxt = dates.next_due(rule_of(rem), date.fromisoformat(due))
        store.update("reminder", rem["id"], next_due=nxt.isoformat() if nxt else None)
    return {"id": new, "card_id": rem["card_id"],
            "said": f"{rem['name']} is in {acct['name']}'s register." + (
                f" Next due {nxt.isoformat()}." if nxt else "")}


def _post_transfer(ctx: Ctx, rem: dict[str, Any], out_of: dict[str, Any], due: str, day: str,
                   cents: int) -> dict[str, Any]:
    """Rule ``posting_a_transfer_reminder_makes_the_linked_pair``: one row out of its
    account and one into the other, linked, and the reminder moves on. Called inside
    ``tx``. Only the outgoing row keeps the reminder's id (a due date is posted once)."""
    store = ctx.store
    into = need_account(store, rem["to_account_id"], open_only=True)
    if cents <= 0:
        raise ValueError(f"Type the amount to move, above zero. {NOTHING}")
    not_before_opening(into, day)
    first, second = insert_pair(ctx, out_of, into, day, cents, "", rem["payee"])
    store.update("txn", first, reminder_id=rem["id"], posted_due=due)
    nxt = dates.next_due(rule_of(rem), date.fromisoformat(due))
    store.update("reminder", rem["id"], next_due=nxt.isoformat() if nxt else None)
    return {"id": first, "ids": [first, second], "card_id": rem["card_id"],
            "said": f"{rem['name']} moved money from {out_of['name']} to {into['name']}."
                    + (f" Next due {nxt.isoformat()}." if nxt else "")}


def skip(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    with store.tx():
        rem = need_reminder(store, args.get("id"))
        due = _same_due(rem, args, "skipped")
        nxt = dates.next_due(rule_of(rem), date.fromisoformat(due))
        store.update("reminder", rem["id"], next_due=nxt.isoformat() if nxt else None)
    return {"card_id": rem["card_id"],
            "said": f"Skipped {rem['name']} for {due}." + (
                f" Next due {nxt.isoformat()}." if nxt else "")}


def due_now(store: Store, today: date) -> int:
    """Rule ``the_sidebar_counts_what_is_due_today_or_overdue``."""
    row = store.one("SELECT count(*) AS n FROM reminder WHERE next_due IS NOT NULL "
                    "AND next_due <= ?", (today.isoformat(),))
    return int(row["n"]) if row else 0


def standing(due: str, today: date, window: int) -> str:
    """``overdue``, ``today``, ``soon`` (within ``window`` days) or ``later``."""
    day = date.fromisoformat(due)
    if day < today:
        return "overdue"
    if day == today:
        return "today"
    return "soon" if day <= today + timedelta(days=window) else "later"


def listing(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, style, cfg = ctx.store, ctx.style(), ctx.cfg()
    today = ctx.today()
    window = int(cfg.due_days)
    switched_on = bool(cfg.auto_post)
    accounts = {a["id"]: a for a in store.accounts()}
    cats = {c["id"]: c["name"] for c in store.categories()}
    items, done = [], []
    for rem in store.reminders():
        acct = accounts.get(rem["account_id"], {})
        into = accounts.get(rem["to_account_id"], {})
        item = {
            "id": rem["id"], "name": rem["name"], "payee": rem["payee"], "kind": rem["kind"],
            "amount": money.format_cents(abs(rem["amount_cents"]), style),
            "about": bool(rem["about"]), "account": rem["account_id"],
            "auto": bool(rem["auto"]), "note": rem["note"], "auto_said": rem["auto_said"],
            "pays_itself": pays_itself(rem, switched_on),
            "paid_other": bool(rem["paid_other"]), "to_account": rem["to_account_id"],
            "to_account_name": into.get("name", ""),
            "account_name": OTHER_WAY if rem["paid_other"] else acct.get("name", ""),
            "category": cats.get(rem["category_id"], "")
            if rem["category_id"] else "", "every": rem["every"],
            "every_words": dates.EVERY_WORDS.get(rem["every"], ""), "day": rem["day"],
            "day2": rem["day2"], "n": rem["n"], "next_due": rem["next_due"],
            "standing": standing(rem["next_due"], today, window) if rem["next_due"] else "done"}
        (items if rem["next_due"] else done).append(item)
    return {"today": today.isoformat(), "window": window, "items": items, "done": done,
            "due_now": due_now(store, today), "auto_on": switched_on}


def events_for(store: Store, account_id: int, today: date, end: date
               ) -> list[tuple[date, int, str]]:
    """Every reminder date for one account from now to ``end``, as ``(day, cents, name)``.
    An overdue one is counted on its own date, which the forward look moves to today."""
    out = []
    for rem in store.all("SELECT * FROM reminder WHERE (account_id=? OR to_account_id=?) "
                         "AND next_due IS NOT NULL", (account_id, account_id)):
        first = date.fromisoformat(rem["next_due"])
        for day in dates.occurrences(rule_of(rem), first, min(first, today), end):
            out.append((day, signed(rem, account_id), rem["name"]))
    return out


def other_events_for(store: Store, today: date, end: date) -> list[tuple[date, int, str]]:
    """Rule ``a_reminder_paid_another_way_is_listed_but_moves_no_balance``: every date a
    reminder paid another way is due from now to ``end``, as ``(day, cents, name)``. They
    belong to no account, so the forward look lists them and never adds them in."""
    out = []
    for rem in store.all("SELECT * FROM reminder WHERE paid_other=1 AND next_due IS NOT NULL"):
        first = date.fromisoformat(rem["next_due"])
        for day in dates.occurrences(rule_of(rem), first, min(first, today), end):
            out.append((day, signed(rem), rem["name"]))
    return out


def ahead(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The forward look for one account: today's balance, plus rows dated later, plus the
    reminders due, day by day."""
    store, style = ctx.store, ctx.style()
    acct = need_account(store, args.get("account"))
    days = int(args.get("days") or ctx.cfg().ahead_days)
    days = max(1, min(days, 400))
    today = ctx.today()
    end = today + timedelta(days=days)
    rows = store.txns(acct["id"])
    start = ledger.balance_on(rows, acct["opening_cents"], today.isoformat())
    events = events_for(store, acct["id"], today, end)
    events += [(date.fromisoformat(r["date"]), int(r["amount_cents"]),
                r["payee"] or "A row you entered") for r in rows
               if today.isoformat() < r["date"] <= end.isoformat()]
    look = ledger.look_ahead(start, today, days, events)
    kind = acct["kind"]

    def told(cents: int) -> dict[str, Any]:
        seen = money.shown(kind, cents)
        return {"text": money.format_cents(seen, style), "neg": seen < 0}

    # Rule ``a_reminder_paid_another_way_is_listed_once_in_its_own_group``: it belongs to
    # no account, so it is not repeated under each one's days.
    other = [{"date": max(day, today).isoformat(), "due": day.isoformat(), "name": name,
              "text": money.format_cents(abs(cents), style)}
             for day, cents, name in other_events_for(store, today, end)]
    other.sort(key=lambda e: (e["date"], e["name"]))
    # For a card or a loan the lowest balance held is the most owed: the same point.
    return {"account": acct["id"], "name": acct["name"], "owed": money.owed(kind),
            "days": [{"date": d["date"], **told(d["balance"]),
                      "events": [{"name": e["name"], "text": money.format_cents(
                          e["cents"], style)} for e in d["events"]]}
                     for d in look["days"]],
            "other": other,
            "low": {"date": look["low"]["date"], **told(look["low"]["balance"])},
            "start": told(start)}
