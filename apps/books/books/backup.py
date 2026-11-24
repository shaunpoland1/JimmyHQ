"""Files out and in: a register as CSV, and everything as one JSON file.

A file is handed to the page in pieces (``export.part``), because one answer can carry
only so much; the page joins them and saves the file with ``hq.ui.save``. The JSON shape
is written down in ``docs/books.md``. "Import everything" fills an empty Books, in one
transaction, and checks every item first: one bad item and nothing is saved.
"""

from __future__ import annotations

import csv
import io
import json
import secrets
from typing import Any

from jimmyhq_app.sheets import guard

from books import dates, importing, money
from books.context import Ctx
from books.debts import KINDS as DEBT_KINDS
from books.debts import MOST_RATE
from books.ops import KINDS, NOTHING, label_row, need_account, seed
from books.reminders import KINDS as REMINDER_KINDS
from books.reminders import NOTE_MOST
from books.store import Store

FORMAT = "jimmyhq-books"
VERSION = 2
READS = (1, 2)  # the versions this Books reads: version 1 has none of the version 2 fields
PART_CHARS = 400_000
STATES = ("", "c", "R")
MOST_MS = 10**14  # a time in unix milliseconds: the year 5138; anything larger is not a time
MOST_NUMBER = 10**12  # an id, a count or a day: nothing real is larger


# --- CSV ---------------------------------------------------------------------------------------


def csv_text(ctx: Ctx, account_id: Any = None) -> tuple[str, str]:
    """One account's register, or every account's, as CSV. Amounts are written plainly
    (-1234.50) whatever the owner's style, for a spreadsheet to read."""
    store = ctx.store
    accounts = {a["id"]: a for a in store.accounts()}
    cats = {c["id"]: c["name"] for c in store.categories()}
    if account_id not in (None, ""):
        chosen = [need_account(store, account_id)]
    else:
        chosen = list(accounts.values())
    out = io.StringIO(newline="")
    write = csv.writer(out, lineterminator="\r\n")
    write.writerow(["Account", "Date", "Payee", "Category", "Memo", "Payment", "Deposit",
                    "Cleared", "Number"])
    for acct in chosen:
        rows = store.txns(acct["id"])
        others = {r["id"]: r["account_id"] for r in store.all(
            "SELECT id, account_id FROM txn WHERE id IN (SELECT transfer_id FROM txn "
            "WHERE account_id=? AND transfer_id IS NOT NULL)", (acct["id"],))}
        for row in rows:
            payee, category = label_row(row, accounts, cats, others)
            cents = int(row["amount_cents"])
            # Every cell of text goes through ``guard``: a payee or memo from a bank or a
            # person must not run as a formula where the file is opened. Amounts are
            # numbers, and are written as they are.
            write.writerow([guard(acct["name"]), row["date"], guard(payee), guard(category),
                            guard(row["memo"]),
                            money.plain(-cents) if cents < 0 else "",
                            money.plain(cents) if cents >= 0 else "",
                            {"c": "cleared", "R": "reconciled"}.get(row["state"], ""),
                            guard(row["number"])])
    name = f"books-{chosen[0]['name']}" if len(chosen) == 1 and account_id else "books-all"
    return out.getvalue(), name


# --- JSON --------------------------------------------------------------------------------------


def everything(ctx: Ctx) -> dict[str, Any]:
    store = ctx.store
    return {
        "format": FORMAT, "version": VERSION, "exported_ms": ctx.now(),
        "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"],
                      "opening_cents": a["opening_cents"], "opening_date": a["opening_date"],
                      "closed": bool(a["closed"])} for a in store.all(
                          "SELECT * FROM account ORDER BY id")],
        "categories": [{"id": c["id"], "name": c["name"], "income": bool(c["income"])}
                       for c in store.all("SELECT * FROM category ORDER BY id")],
        "transactions": [{"id": t["id"], "account": t["account_id"], "date": t["date"],
                          "payee": t["payee"], "category": t["category_id"],
                          "memo": t["memo"], "number": t["number"],
                          "amount_cents": t["amount_cents"], "state": t["state"],
                          "transfer": t["transfer_id"]}
                         for t in store.all("SELECT * FROM txn ORDER BY id")],
        "reminders": [{"id": r["id"], "name": r["name"], "payee": r["payee"],
                       "amount_cents": r["amount_cents"], "about": bool(r["about"]),
                       "account": r["account_id"], "to_account": r["to_account_id"],
                       "paid_other": bool(r["paid_other"]), "auto": bool(r["auto"]),
                       "category": r["category_id"], "kind": r["kind"],
                       "every": r["every"], "day": r["day"], "day2": r["day2"],
                       "n": r["n"], "next_due": r["next_due"], "note": r["note"]}
                      for r in store.all("SELECT * FROM reminder ORDER BY id")],
        "debts": debts_out(store),
        "reconciliations": [{"account": r["account_id"], "through": r["through"],
                             "statement_cents": r["statement_cents"], "rows": r["row_count"],
                             "done_ms": r["done_ms"]}
                            for r in store.all("SELECT * FROM reconciliation ORDER BY id")],
    }


def debts_out(store: Store) -> list[dict[str, Any]]:
    history: dict[int, list[dict[str, Any]]] = {}
    for h in store.all("SELECT * FROM debt_balance ORDER BY debt_id, on_date"):
        history.setdefault(h["debt_id"], []).append(
            {"on": h["on_date"], "balance_cents": h["balance_cents"]})
    return [{"id": d["id"], "name": d["name"], "kind": d["kind"],
             "rate_percent": None if d["rate_hundredths"] is None else d["rate_hundredths"] / 100,
             "min_payment_cents": d["min_cents"], "target_date": d["target_date"],
             "account": d["account_id"], "note": d["note"], "closed": bool(d["closed"]),
             "balance_history": history.get(d["id"], [])}
            for d in store.all("SELECT * FROM debt ORDER BY id")]


def export_part(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """A piece of a file to save. Part 0 makes the file and a token; later parts name it."""
    kept: dict[str, dict[str, Any]] = ctx.__dict__.setdefault("exports", {})
    part = int(args.get("part") or 0)
    if part == 0:
        what = str(args.get("what") or "")
        if what == "csv":
            text, name = csv_text(ctx, args.get("account"))
            kind = "text/csv"
        elif what == "json":
            data = everything(ctx)
            # One file, whatever its size: the page saves it whole, and packs it tighter
            # itself if the owner's own limit (Settings, Apps) is smaller than it is.
            text = json.dumps(data, ensure_ascii=False, indent=1)
            name, kind = "books-everything", "application/json"
        else:
            raise ValueError(f"Choose what to export. {NOTHING}")
        token = secrets.token_hex(8)
        while len(kept) >= 3:
            kept.pop(next(iter(kept)))
        kept[token] = {"text": text, "name": name, "type": kind}
    else:
        token = str(args.get("token") or "")
        if token not in kept:
            raise ValueError(f"That export is no longer ready. Press Export again. {NOTHING}")
    made = kept[token]
    parts = max(1, -(-len(made["text"]) // PART_CHARS))
    piece = made["text"][part * PART_CHARS:(part + 1) * PART_CHARS]
    if part >= parts - 1:
        kept.pop(token, None)
    return {"token": token, "part": part, "parts": parts, "text": piece,
            "name": made["name"], "type": made["type"],
            "bytes": len(made["text"].encode("utf-8"))}


# --- bringing it in ------------------------------------------------------------------------------


def _bad(what: str) -> ValueError:
    return ValueError(f"The file could not be read: {what}. {NOTHING}")


def _int(item: dict[str, Any], key: str, where: str, *, none: bool = False,
         most: int = MOST_NUMBER) -> int | None:
    """A whole number no larger than ``most`` either way. A number past the caps an amount
    typed in is held to is a slip, and one past SQLite's own limit would break the file's
    own import, so it is refused here with the rest of the file."""
    value = item.get(key)
    if value is None and none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(f"{where} has no whole number for \"{key}\"")
    if abs(value) > most:
        raise _bad(f"{where} has a \"{key}\" larger than Books keeps")
    return value


def _cents(item: dict[str, Any], key: str, where: str, *, none: bool = False) -> int | None:
    """An amount in whole cents, below the ceiling an amount typed in is held to."""
    return _int(item, key, where, none=none, most=money.MOST_CENTS - 1)


def _int_or(item: dict[str, Any], key: str, where: str, default: int, *,
            most: int = MOST_NUMBER) -> int:
    """A whole number that may be left out (or null), and is refused when it is not one."""
    if item.get(key) is None:
        return default
    return _int(item, key, where, most=most) or 0


def _str(item: dict[str, Any], key: str, where: str, most: int, *, need: bool = False) -> str:
    value = item.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > most or (need and not value.strip()):
        raise _bad(f"{where} has no proper \"{key}\"")
    return value.strip()


def _day(item: dict[str, Any], key: str, where: str, *, none: bool = False) -> str | None:
    value = item.get(key)
    if value is None and none:
        return None
    try:
        return dates.parse_day(value).isoformat()
    except ValueError:
        raise _bad(f"{where} has no date like 2026-03-31 for \"{key}\"") from None


def _flag(item: dict[str, Any], key: str, where: str) -> bool:
    value = item.get(key, False)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise _bad(f"{where} has \"{key}\" that is not true or false")
    return value


def _percent(item: dict[str, Any], where: str) -> int | None:
    """``rate_percent`` (a number with at most two decimals, 0 to 100, or null) as
    hundredths of a percent."""
    value = item.get("rate_percent")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _bad(f"{where} has no number for \"rate_percent\"")
    hundredths = round(value * 100)
    if abs(value * 100 - hundredths) > 1e-6 or not 0 <= hundredths <= MOST_RATE:
        raise _bad(f"{where} has a \"rate_percent\" that is not from 0 to 100 with at most "
                   f"two decimals")
    return hundredths


def _list(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise _bad(f"\"{key}\" is not a list of items")
    return value


def is_empty(store: Store) -> bool:
    return store.one("SELECT id FROM account LIMIT 1") is None


def import_everything(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``import_everything_is_whole_or_nothing``."""
    store = ctx.store
    if not is_empty(store):
        raise ValueError("Import everything fills an empty Books, and yours already has "
                         "accounts. Export what you have first if you want to keep it, then "
                         f"use Erase everything. {NOTHING}")
    raw = importing.uploaded(ctx, args.get("token"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise _bad("it is not JSON") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise _bad(f"it does not say \"format\": \"{FORMAT}\"")
    if data.get("version") not in READS or isinstance(data.get("version"), bool):
        raise _bad(f"it is version {data.get('version')!r}, and this Books reads versions "
                   f"{' and '.join(str(v) for v in READS)}")
    now = ctx.now()
    counts = {}
    with store.tx():
        store.run("DELETE FROM category")
        acc_ids: dict[int, int] = {}
        for item in _list(data, "accounts"):
            where = f"account {item.get('id')!r}"
            kind = _str(item, "kind", where, 20)
            if kind not in KINDS:
                raise _bad(f"{where} has a kind Books does not know")
            name = _str(item, "name", where, 60, need=True)
            if store.one("SELECT id FROM account WHERE name=? COLLATE NOCASE", (name,)):
                raise _bad(f"two accounts are called {name}")
            old_id = _int(item, "id", where)
            if old_id in acc_ids:
                raise _bad(f"two accounts have the id {old_id}")
            acc_ids[old_id or 0] = store.insert("account", {
                "name": name, "kind": kind, "opening_cents": _cents(item, "opening_cents", where),
                "opening_date": _day(item, "opening_date", where),
                "closed": int(_flag(item, "closed", where)), "created": now, "updated": now})
        cat_ids: dict[int, int] = {}
        for item in _list(data, "categories"):
            where = f"category {item.get('id')!r}"
            name = _str(item, "name", where, 60, need=True)
            if store.category_named(name):
                raise _bad(f"two categories are called {name}")
            old_id = _int(item, "id", where)
            if old_id in cat_ids:
                raise _bad(f"two categories have the id {old_id}")
            cat_ids[old_id or 0] = store.insert("category", {
                "name": name, "income": int(_flag(item, "income", where)), "created": now})

        def account_of(item: dict[str, Any], where: str) -> int:
            got = _int(item, "account", where)
            if got not in acc_ids:
                raise _bad(f"{where} names an account that is not in the file")
            return acc_ids[got]  # type: ignore[index]

        def account_or_none(item: dict[str, Any], key: str, where: str) -> int | None:
            got = _int(item, key, where, none=True)
            if got is None:
                return None
            if got not in acc_ids:
                raise _bad(f"{where} names an account (\"{key}\") that is not in the file")
            return acc_ids[got]

        def category_of(item: dict[str, Any], where: str) -> int | None:
            got = _int(item, "category", where, none=True)
            if got is None:
                return None
            if got not in cat_ids:
                raise _bad(f"{where} names a category that is not in the file")
            return cat_ids[got]

        txn_ids: dict[int, int] = {}
        links: dict[int, int] = {}
        for item in _list(data, "transactions"):
            where = f"transaction {item.get('id')!r}"
            state = item.get("state", "")
            if state not in STATES:
                raise _bad(f"{where} has a cleared mark Books does not know")
            old = _int(item, "id", where) or 0
            if old in txn_ids:
                raise _bad(f"two transactions have the id {old}")
            txn_ids[old] = store.insert("txn", {
                "account_id": account_of(item, where), "date": _day(item, "date", where),
                "payee": _str(item, "payee", where, 200), "category_id": category_of(item, where),
                "memo": _str(item, "memo", where, 500), "number": _str(item, "number", where, 20),
                "amount_cents": _cents(item, "amount_cents", where), "state": state,
                "created": now, "updated": now})
            other = _int(item, "transfer", where, none=True)
            if other is not None:
                links[old] = other
        for old, other in links.items():
            if links.get(other) != old or other not in txn_ids:
                raise _bad(f"transaction {old} is a transfer whose other side is missing")
            mine, theirs = store.txn(txn_ids[old]), store.txn(txn_ids[other])
            if (mine is None or theirs is None or mine["account_id"] == theirs["account_id"]
                    or mine["amount_cents"] + theirs["amount_cents"] != 0):
                raise _bad(f"transaction {old} is a transfer that is not two rows in two "
                           f"accounts that sum to zero")
            store.update("txn", txn_ids[old], transfer_id=txn_ids[other])
        for item in _list(data, "reminders"):
            where = f"reminder {item.get('id')!r}"
            kind, every = item.get("kind"), item.get("every")
            if kind not in REMINDER_KINDS or every not in dates.EVERY:
                raise _bad(f"{where} has a kind or a schedule Books does not know")
            rule = dates.Rule(every, day=_int_or(item, "day", where, 1),
                              day2=_int_or(item, "day2", where, 15),
                              n=_int_or(item, "n", where, 1))
            if dates.rule_problem(rule):
                raise _bad(f"{where}: {dates.rule_problem(rule)}")
            paid_other = _flag(item, "paid_other", where)
            about, auto = _flag(item, "about", where), _flag(item, "auto", where)
            if about and auto:
                raise _bad(f"{where} has \"about\" and \"auto\" both true: a reminder whose "
                           f"amount is only about right cannot happen by itself")
            mine, into = account_or_none(item, "account", where), account_or_none(
                item, "to_account", where)
            if kind == "transfer":
                if mine is None or into is None or mine == into or paid_other:
                    raise _bad(f"{where} is a transfer, so it needs two different accounts "
                               f"(\"account\" and \"to_account\") and is not paid another way")
                if item.get("category") is not None:
                    raise _bad(f"{where} is a transfer, so its \"category\" must be null")
            else:
                if into is not None:
                    raise _bad(f"{where} has a \"to_account\" but is not a transfer")
                if paid_other != (mine is None):
                    raise _bad(f"{where} must have an \"account\" unless \"paid_other\" is "
                               f"true, and none when it is")
            store.insert("reminder", {
                "name": _str(item, "name", where, 80, need=True),
                "payee": _str(item, "payee", where, 200),
                "amount_cents": abs(_cents(item, "amount_cents", where) or 0),
                "about": int(about), "auto": int(auto),
                "note": _str(item, "note", where, NOTE_MOST), "account_id": mine,
                "to_account_id": into, "paid_other": int(paid_other),
                "category_id": category_of(item, where), "kind": kind, "every": every,
                "day": rule.day, "day2": rule.day2, "n": rule.n,
                "next_due": _day(item, "next_due", where, none=True),
                "created": now, "updated": now})
        for item in _list(data, "debts"):
            where = f"debt {item.get('id')!r}"
            name = _str(item, "name", where, 80, need=True)
            if store.one("SELECT id FROM debt WHERE name=? COLLATE NOCASE", (name,)):
                raise _bad(f"two debts are called {name}")
            minimum = 0 if item.get("min_payment_cents") is None else _cents(
                item, "min_payment_cents", where)
            if minimum is None or minimum < 0:
                raise _bad(f"{where} has a \"min_payment_cents\" below zero")
            kind = item.get("kind", "other")
            if kind is None:
                kind = "other"
            if kind not in DEBT_KINDS:
                raise _bad(f"{where} has a \"kind\" that is not one of "
                           f"{', '.join(DEBT_KINDS)}")
            new = store.insert("debt", {
                "kind": kind, "name": name, "rate_hundredths": _percent(item, where),
                "min_cents": minimum,
                "target_date": _day(item, "target_date", where, none=True),
                "account_id": account_or_none(item, "account", where),
                "note": _str(item, "note", where, 500), "closed": int(_flag(item, "closed", where)),
                "created": now, "updated": now})
            seen_days: set[str] = set()
            history = item.get("balance_history", [])
            if history is None:
                history = []
            if not isinstance(history, list) or not all(isinstance(h, dict) for h in history):
                raise _bad(f"{where} has a \"balance_history\" that is not a list of items")
            for h in history:
                day = _day(h, "on", f"{where} balance")
                cents = _cents(h, "balance_cents", f"{where} balance")
                if cents is None or cents < 0:
                    raise _bad(f"{where} has a balance below zero: type what is owed, as a "
                               f"plain number")
                if day in seen_days:
                    raise _bad(f"{where} has two balances for {day}")
                seen_days.add(str(day))
                store.insert("debt_balance", {"debt_id": new, "on_date": day,
                                              "balance_cents": cents, "created": now})
        for item in _list(data, "reconciliations"):
            where = "a reconciliation"
            store.insert("reconciliation", {
                "account_id": account_of(item, where), "through": _day(item, "through", where),
                "statement_cents": _cents(item, "statement_cents", where),
                "row_count": _int_or(item, "rows", where, 0),
                "done_ms": _int_or(item, "done_ms", where, now, most=MOST_MS)})
        store.set_meta("seeded", True)
        store.run("DELETE FROM upload WHERE token=?", (str(args.get("token") or ""),))
        counts = {k: len(_list(data, k)) for k in ("accounts", "transactions", "reminders",
                                                    "debts")}
    store.wipe_log()
    def many(n: int, one: str) -> str:
        return f"{n} {one}" if n == 1 else f"{n} {one}s"

    tail = (f", {many(counts['reminders'], 'reminder')} and {many(counts['debts'], 'debt')}."
            if counts["debts"] else f" and {many(counts['reminders'], 'reminder')}.")
    return {"said": f"Brought in {many(counts['accounts'], 'account')}, "
                    f"{many(counts['transactions'], 'row')}{tail}", **counts}


def erase_everything(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Every record in Books, deleted for good. The page asks first."""
    store = ctx.store
    if args.get("confirm") is not True:
        raise ValueError(f"Erasing needs your yes on the page. {NOTHING}")
    cards = [r["card_id"] for r in store.all("SELECT card_id FROM reminder WHERE card_id "
                                             "IS NOT NULL")]
    with store.tx():
        for table in ("txn", "reminder", "reconciliation", "import_batch", "upload",
                      "debt_balance", "debt", "auto_post", "account", "category", "meta"):
            store.run(f"DELETE FROM {table}")  # noqa: S608 - a fixed list of names
    store.wipe_log()
    store.conn.execute("VACUUM")
    seed(store, ctx.now())
    return {"said": "Everything in Books is erased. The starter categories are back.",
            "cards": cards}
