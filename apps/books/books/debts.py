"""Debts: what is owed, each balance with its dated history, and a rough payoff order.

A debt has a name, a rate (hundredths of a percent, or not known), a minimum payment in
cents, an optional target date, an optional linked account and a note. Its balance is the
latest of the dated balances the owner has noted; a debt with none has a balance that is
**not known**, which is never the same as zero (zero is "paid off", and an answer).

``plan`` is arithmetic over whole cents and nothing else: month by month every debt grows
by its monthly interest and is paid its minimum, and the extra money (the setting
``debt_extra``, and the minimums of debts already paid off) goes to the first debt that is
left, in the order the owner chose. It says roughly when each is paid off. It is not
advice and it is not a promise: rates and payments change.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from books import dates, ledger, money
from books.context import Ctx
from books.ops import NOTHING, day_arg, need_account, text
from books.store import Store

MOST_MONTHS = 600  # fifty years: past this the answer is "not at these payments"
MOST_RATE = 10_000  # 100.00 percent, in hundredths
ORDERS = {"smallest": "Smallest balance first", "rate": "Highest rate first"}
KINDS = {"card": "Credit card", "loan": "Loan", "mortgage": "Mortgage",
         "heloc": "Home equity line of credit", "other": "Other"}


# --- the arithmetic (pure) ------------------------------------------------------------------


def monthly_interest(balance: int, rate_hundredths: int) -> int:
    """Rule ``a_months_interest_is_rounded_to_the_nearest_cent``: a balance's interest for
    one month at a yearly rate in hundredths of a percent (1999 is 19.99%)."""
    return (2 * balance * rate_hundredths + 120_000) // 240_000


def order_of(items: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    """Rule ``the_payoff_order_follows_the_setting``: the debts in the order the extra
    money reaches them. Ties go to the debt added first."""
    if how == "rate":
        return sorted(items, key=lambda d: (-int(d["rate"]), int(d["balance"]), int(d["id"])))
    return sorted(items, key=lambda d: (int(d["balance"]), -int(d["rate"]), int(d["id"])))


def plan(debts: list[dict[str, Any]], how: str, extra: int, today: date) -> dict[str, Any]:
    """Rule ``the_payoff_plan_never_calls_an_unknown_balance_zero``.

    ``debts`` are ``{id, balance (cents or None), rate (hundredths or None), minimum}``.
    Returns ``{"order": [ids in the order the extra goes], "by_id": {id: {...}}}`` where
    each entry has ``status`` (``paid``, ``unknown``, ``needs_rate``, ``never`` or ``ok``)
    and, for ``ok``, ``months`` (from now; the first payment is next month),
    ``payoff_month`` (``YYYY-MM``) and ``interest_cents``."""
    by_id: dict[int, dict[str, Any]] = {}
    live: list[dict[str, Any]] = []
    for d in debts:
        if d["balance"] is None:
            by_id[d["id"]] = {"status": "unknown"}
        elif int(d["balance"]) <= 0:
            by_id[d["id"]] = {"status": "paid", "months": 0}
        elif d["rate"] is None:
            by_id[d["id"]] = {"status": "needs_rate"}
        else:
            live.append({"id": d["id"], "balance": int(d["balance"]), "rate": int(d["rate"]),
                         "min": max(0, int(d["minimum"]))})
    ordered = order_of(live, how)
    left = {d["id"]: d["balance"] for d in ordered}
    paid_interest = {d["id"]: 0 for d in ordered}
    freed = 0  # minimums of debts already paid off: they move on to the next one
    first_of_month = today.replace(day=1)
    for month in range(1, MOST_MONTHS + 1):
        if not left:
            break
        for d in ordered:
            if d["id"] in left:
                charge = monthly_interest(left[d["id"]], d["rate"])
                left[d["id"]] += charge
                paid_interest[d["id"]] += charge
        pool = max(0, int(extra)) + freed
        for d in ordered:
            if d["id"] in left:
                pay = min(d["min"], left[d["id"]])
                left[d["id"]] -= pay
                pool += d["min"] - pay
        for d in ordered:
            if pool <= 0:
                break
            if d["id"] in left:
                pay = min(pool, left[d["id"]])
                left[d["id"]] -= pay
                pool -= pay
        for d in ordered:
            if d["id"] in left and left[d["id"]] <= 0:
                del left[d["id"]]
                freed += d["min"]
                by_id[d["id"]] = {
                    "status": "ok", "months": month, "interest_cents": paid_interest[d["id"]],
                    "payoff_month": dates.add_months(first_of_month, month).isoformat()[:7]}
    for d in ordered:
        if d["id"] in left:
            by_id[d["id"]] = {"status": "never"}
    return {"order": [d["id"] for d in ordered], "by_id": by_id}


# --- reading what the page sent -----------------------------------------------------------------


def rate_arg(args: dict[str, Any], style: str) -> int | None:
    """The rate typed as a percent ("19.99", "19.99%"), as hundredths; empty is not known."""
    raw = str(args.get("rate") if args.get("rate") is not None else "").strip().rstrip("%").strip()
    if not raw:
        return None
    try:
        hundredths = money.parse_cents(raw, style)
    except ValueError:
        example = "19,99" if style == "comma" else "19.99"
        raise ValueError(f"\"{raw}\" is not a percentage. Type the yearly rate as a number "
                         f"from 0 to 100, such as {example}. {NOTHING}") from None
    if not 0 <= hundredths <= MOST_RATE:
        raise ValueError(f"The rate must be a percent from 0 to 100, such as 19.99. {NOTHING}")
    return hundredths


def minimum_arg(args: dict[str, Any], style: str) -> int:
    raw = str(args.get("minimum") if args.get("minimum") is not None else "").strip()
    if not raw:
        return 0
    cents = money.parse_cents(raw, style)
    if cents < 0:
        raise ValueError(f"Type the minimum payment without a minus sign. {NOTHING}")
    return cents


def balance_arg(args: dict[str, Any], style: str) -> int:
    cents = money.parse_cents(str(args.get("balance") or ""), style)
    if cents < 0:
        raise ValueError(f"Type what you owe as a plain number, with no minus sign. Zero "
                         f"means it is paid off. {NOTHING}")
    return cents


def need_debt(store: Store, debt_id: Any) -> dict[str, Any]:
    debt = store.debt(debt_id)
    if debt is None:
        raise ValueError(f"That debt is not in Books. {NOTHING}")
    return debt


def _linked(store: Store, args: dict[str, Any]) -> int | None:
    if args.get("account") in (None, "", 0):
        return None
    return int(need_account(store, args.get("account"))["id"])


def kind_arg(args: dict[str, Any], old: dict[str, Any] | None = None) -> str:
    """The kind of debt: what was sent, else what it was (a debt changed by a caller that
    does not say), else ``other``."""
    given = args.get("kind")
    kind = str(given) if given not in (None, "") else (old["kind"] if old else "other")
    if kind not in KINDS:
        raise ValueError(f"Choose what kind of debt it is: card, loan, mortgage, heloc or "
                         f"other. {NOTHING}")
    return kind


def passed_words(ctx: Ctx, target: str | None) -> str:
    """A sentence for a target date that is already in the past (allowed, but said), else
    an empty one."""
    if target and target < ctx.today().isoformat():
        return f" The date to pay it off by ({target}) has passed."
    return ""


def _fields(ctx: Ctx, args: dict[str, Any], old: dict[str, Any] | None = None
            ) -> dict[str, Any]:
    style = ctx.style()
    target = str(args.get("target_date") or "").strip()
    return {"kind": kind_arg(args, old),
            "name": text(args, "name", 80, "a name for the debt, such as Example Card",
                         need=True),
            "rate_hundredths": rate_arg(args, style), "min_cents": minimum_arg(args, style),
            "target_date": dates.parse_day(target, "The target date").isoformat() if target
            else None, "account_id": _linked(ctx.store, args),
            "note": text(args, "note", 500, "the note")}


def _note_balance(ctx: Ctx, debt_id: int, day: str, cents: int) -> None:
    """One balance per day: noting a second on the same day replaces the first. Must be
    called inside ``tx``."""
    ctx.store.run("INSERT INTO debt_balance (debt_id, on_date, balance_cents, created) "
                  "VALUES (?, ?, ?, ?) ON CONFLICT(debt_id, on_date) DO UPDATE SET "
                  "balance_cents=excluded.balance_cents, created=excluded.created",
                  (debt_id, day, cents, ctx.now()))


def _balance_day(ctx: Ctx, args: dict[str, Any]) -> str:
    day = day_arg(args, "on", "The date of the balance") if args.get("on") else \
        ctx.today().isoformat()
    if day > ctx.today().isoformat():
        raise ValueError(f"A balance cannot be dated after today ({ctx.today().isoformat()}). "
                         f"{NOTHING}")
    return day


# --- changes ------------------------------------------------------------------------------------


def add(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    values = _fields(ctx, args)
    first = str(args.get("balance") or "").strip()
    cents = balance_arg(args, ctx.style()) if first else None
    day = _balance_day(ctx, args) if first else ""
    with store.tx():
        if store.one("SELECT id FROM debt WHERE name=? COLLATE NOCASE", (values["name"],)):
            raise ValueError(f"You already have a debt called {values['name']}. {NOTHING}")
        now = ctx.now()
        new = store.insert("debt", {**values, "closed": 0, "created": now, "updated": now})
        if cents is not None:
            _note_balance(ctx, new, day, cents)
    return {"id": new, "said": f"{values['name']} is added."
            + ("" if cents is not None else " Its balance is not known yet.")
            + passed_words(ctx, values["target_date"]),
            "target_passed": bool(passed_words(ctx, values["target_date"]))}


def edit(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    debt = need_debt(store, args.get("id"))
    values = _fields(ctx, args, debt)
    if "closed" in args:
        values["closed"] = int(bool(args.get("closed")))
    with store.tx():
        same = store.one("SELECT id FROM debt WHERE name=? COLLATE NOCASE", (values["name"],))
        if same and same["id"] != debt["id"]:
            raise ValueError(f"You already have a debt called {values['name']}. {NOTHING}")
        values["updated"] = ctx.now()
        sets = ", ".join(f"{k}=?" for k in values)
        store.run(f"UPDATE debt SET {sets} WHERE id=?", [*values.values(), debt["id"]])  # noqa: S608
    return {"said": "Saved." + passed_words(ctx, values["target_date"]),
            "target_passed": bool(passed_words(ctx, values["target_date"]))}


def set_balance(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``a_balance_update_keeps_the_history``: a new dated balance beside the old
    ones. ``from_account`` takes it from the debt's linked card or loan."""
    store = ctx.store
    debt = need_debt(store, args.get("id"))
    day = _balance_day(ctx, args)
    if args.get("from_account") is True:
        acct = store.account(debt["account_id"]) if debt["account_id"] else None
        if acct is None or not money.owed(acct["kind"]):
            raise ValueError(f"Only a debt linked to a credit card or a loan account can take "
                             f"its balance from the account. Type the balance instead. "
                             f"{NOTHING}")
        cents = max(0, -acct_today(ctx, acct))  # held below zero when something is owed
    else:
        cents = balance_arg(args, ctx.style())
    with store.tx():
        _note_balance(ctx, debt["id"], day, cents)
        store.run("UPDATE debt SET updated=? WHERE id=?", (ctx.now(), debt["id"]))
    return {"said": f"{debt['name']} is {money.format_cents(cents, ctx.style())} on {day}."
            if cents else f"{debt['name']} is paid off as of {day}."}


def acct_today(ctx: Ctx, acct: dict[str, Any]) -> int:
    """An account's balance today as held (below zero when something is owed)."""
    return ledger.balance_on(ctx.store.txns(acct["id"]), acct["opening_cents"],
                             ctx.today().isoformat())


def delete(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    debt = need_debt(store, args.get("id"))
    with store.tx():
        store.run("DELETE FROM debt_balance WHERE debt_id=?", (debt["id"],))
        store.run("DELETE FROM debt WHERE id=?", (debt["id"],))
    store.wipe_log()
    return {"said": f"{debt['name']} and its balance history are deleted."}


# --- what the page shows --------------------------------------------------------------------------


def current(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The latest dated balance, or None when none was ever noted (not known)."""
    return history[-1] if history else None


def extra_cents(ctx: Ctx) -> tuple[int, str]:
    """The setting ``debt_extra`` as cents, and a sentence when it cannot be read (then 0)."""
    raw = str(ctx.cfg().debt_extra or "0").strip() or "0"
    try:
        cents = money.parse_cents(raw, ctx.style())
    except ValueError:
        return 0, (f"\"Extra each month for debts\" in Books' settings says {raw!r}, which is "
                   "not an amount, so the payoff view used 0.")
    if cents < 0:
        return 0, ("\"Extra each month for debts\" in Books' settings is below zero, so the "
                   "payoff view used 0.")
    return cents, ""


def listing(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, style, cfg = ctx.store, ctx.style(), ctx.cfg()
    today = ctx.today()
    how = "rate" if str(cfg.debt_order) == "rate" else "smallest"
    extra, problem = extra_cents(ctx)
    accounts = {a["id"]: a for a in store.accounts()}
    rows = store.all("SELECT * FROM debt ORDER BY closed, name COLLATE NOCASE, id")
    history = {}
    for h in store.all("SELECT * FROM debt_balance ORDER BY debt_id, on_date"):
        history.setdefault(h["debt_id"], []).append(h)
    open_rows = [d for d in rows if not d["closed"]]
    made = plan([{"id": d["id"], "balance": (current(history.get(d["id"], [])) or {}).get(
        "balance_cents"), "rate": d["rate_hundredths"], "minimum": d["min_cents"]}
        for d in open_rows], how, extra, today)

    def said(cents: int) -> str:
        return money.format_cents(cents, style)

    items = []
    for d in rows:
        hist = history.get(d["id"], [])
        now = current(hist)
        out = made["by_id"].get(d["id"])
        words, standing = payoff_words(out), None
        if out and out["status"] == "ok" and d["target_date"]:
            standing = "on_track" if out["payoff_month"] <= d["target_date"][:7] else "behind"
        acct = accounts.get(d["account_id"]) if d["account_id"] else None
        items.append({
            "id": d["id"], "name": d["name"], "closed": bool(d["closed"]), "note": d["note"],
            "kind": d["kind"], "kind_words": KINDS[d["kind"]],
            "known": now is not None,
            "balance": said(now["balance_cents"]) if now else "", "as_of": now["on_date"]
            if now else "", "paid": bool(now and now["balance_cents"] <= 0),
            "rate": f"{d['rate_hundredths'] / 100:.2f}".replace(".", "," if style == "comma"
                                                                else ".")
            if d["rate_hundredths"] is not None else "",
            "minimum": said(d["min_cents"]) if d["min_cents"] else "",
            "target_date": d["target_date"] or "", "standing": standing,
            "account": d["account_id"], "account_name": acct["name"] if acct else "",
            "can_take": bool(acct and money.owed(acct["kind"])),
            "payoff": words, "payoff_month": (out or {}).get("payoff_month", ""),
            "months": (out or {}).get("months"),
            "interest": said(out["interest_cents"]) if out and out["status"] == "ok" else "",
            "history": [{"on": h["on_date"], "text": said(h["balance_cents"])}
                        for h in reversed(hist)]})
    known = [d for d in open_rows if current(history.get(d["id"], []))]
    total = sum(int(current(history[d["id"]])["balance_cents"]) for d in known)  # type: ignore[index]
    unknown = [d["name"] for d in open_rows if d not in known]
    by_id = {i["id"]: i for i in items}
    steps = []
    for n, debt_id in enumerate(made["order"], start=1):
        i = by_id[debt_id]
        steps.append({"n": n, "id": debt_id, "name": i["name"], "balance": i["balance"],
                      "payoff": i["payoff"], "payoff_month": i["payoff_month"],
                      "months": i["months"], "interest": i["interest"]})
    ends = [e["months"] for e in made["by_id"].values() if e["status"] == "ok"]
    stuck = [by_id[k]["name"] for k, e in made["by_id"].items() if e["status"] == "never"]
    free = (dates.add_months(today.replace(day=1), max(ends)).isoformat()[:7]
            if ends and not stuck else "")
    groups = []
    for kind, words in KINDS.items():
        mine = [d for d in open_rows if d["kind"] == kind]
        if mine:
            owed = sum(int(current(history[d["id"]])["balance_cents"])  # type: ignore[index]
                       for d in mine if d in known)
            groups.append({"kind": kind, "words": words, "count": len(mine),
                           "total": said(owed),
                           "unknown": sum(1 for d in mine if d not in known)})
    return {"today": today.isoformat(), "items": items,
            "kinds": [{"id": k, "words": w} for k, w in KINDS.items()], "groups": groups,
            "total": {"text": said(total), "unknown": unknown},
            "minimums": said(sum(int(d["min_cents"]) for d in open_rows)),
            "order": how, "order_words": ORDERS[how], "extra": said(extra) if extra else "",
            "problem": problem, "steps": steps, "debt_free": free, "stuck": stuck,
            "has_open": bool(open_rows)}


def payoff_words(out: dict[str, Any] | None) -> str:
    """What the payoff column says for one debt."""
    if out is None:
        return ""
    status = out["status"]
    if status == "unknown":
        return "Balance not known"
    if status == "paid":
        return "Paid off"
    if status == "needs_rate":
        return "Needs a rate"
    if status == "never":
        return "Not paid off at these payments"
    months = int(out["months"])
    return f"About {out['payoff_month']} ({months} month{'s' if months != 1 else ''})"
