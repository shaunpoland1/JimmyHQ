"""Bringing a bank's CSV file into one account.

The page hands the file over in pieces (``upload.start``, then ``upload.part``), because
one call from a page can carry only so much. ``preview`` reads it with a mapping (the one
sent, else the one remembered for this account and header, else a guess from the header)
and says what would be added, what is a duplicate and what cannot be read; nothing is
saved. ``commit`` does the same reading and saves the new rows as one import, in one
transaction, and remembers the mapping. ``undo`` takes out exactly the rows that import
added.
"""

from __future__ import annotations

import secrets
from typing import Any

from books import csvin, dates, ledger, money
from books.context import Ctx
from books.ops import NOTHING, need_account, resolve_category

PART_CHARS = 200_000
MB = 1_000_000
DAY_MS = 24 * 60 * 60 * 1000
PREVIEW_ROWS = 10


# --- the file, in pieces ------------------------------------------------------------------------


def most_bytes(ctx: Ctx) -> int:
    """Rule ``a_file_may_be_as_large_as_the_owners_setting``: the largest file a page may
    hand over, from the setting ``import_mb``."""
    return max(1, int(ctx.cfg().import_mb)) * MB


def too_large(ctx: Ctx, size: int) -> ValueError:
    return ValueError(f"That file is larger than {most_bytes(ctx) // MB} MB, the most Books "
                      f"is set to bring in. Use a shorter period, or raise \"Largest file to "
                      f"bring in (MB)\" in Books' settings. {NOTHING}")


def upload_start(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    with store.tx():
        store.run("DELETE FROM upload WHERE created < ?", (ctx.now() - DAY_MS,))
    store.wipe_log()
    return {"token": secrets.token_hex(12), "part_chars": PART_CHARS,
            "max_bytes": most_bytes(ctx)}


def upload_part(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    token = str(args.get("token") or "")
    part = args.get("part")
    piece = args.get("text")
    limit = most_bytes(ctx)
    if len(token) != 24 or not isinstance(part, int) or isinstance(part, bool) or not (
            0 <= part <= limit // PART_CHARS + 1) or not isinstance(piece, str) or len(
            piece) > PART_CHARS:
        raise ValueError(f"That piece of the file did not arrive whole. Choose the file again. "
                         f"{NOTHING}")
    held = store.one("SELECT coalesce(sum(length(text)), 0) AS n FROM upload WHERE token=? "
                     "AND part != ?", (token, part))
    if int(held["n"] if held else 0) + len(piece) > limit:
        forget_upload(ctx, token)  # what arrived of a file that is too large is not kept
        raise too_large(ctx, limit)
    with store.tx():
        store.run("INSERT INTO upload (token, part, text, created) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(token, part) DO UPDATE SET text=excluded.text",
                  (token, part, piece, ctx.now()))
    return {"part": part}


def uploaded(ctx: Ctx, token: str) -> str:
    parts = ctx.store.all("SELECT part, text FROM upload WHERE token=? ORDER BY part",
                          (str(token or ""),))
    if not parts or [p["part"] for p in parts] != list(range(len(parts))):
        raise ValueError(f"The file did not arrive. Choose it again. {NOTHING}")
    return "".join(p["text"] for p in parts)


def forget_upload(ctx: Ctx, token: str) -> None:
    with ctx.store.tx():
        ctx.store.run("DELETE FROM upload WHERE token=?", (str(token or ""),))
    ctx.store.wipe_log()


# --- reading it --------------------------------------------------------------------------------


def _read(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, style = ctx.store, ctx.style()
    acct = need_account(store, args.get("account"), open_only=True)
    table = csvin.read_table(uploaded(ctx, args.get("token")))
    if len(table) < 2:
        raise ValueError(f"The file has no lines under its header. Choose a CSV file your "
                         f"bank made. {NOTHING}")
    header, lines = table[0], table[1:]
    remembered = store.get_meta(f"mapping:{acct['id']}") or {}
    if isinstance(args.get("mapping"), dict):
        mapping = csvin.mapping_from(args["mapping"], len(header))
        how = "yours"
    elif remembered.get("header") == header:
        mapping = csvin.mapping_from(remembered.get("mapping"), len(header))
        how = "remembered"
    else:
        mapping = csvin.guess(header)
        how = "guessed"
    problem = csvin.mapping_problem(mapping)
    detected = (dates.detect_format(csvin.date_values(lines, mapping))
                if mapping.date is not None else None)
    fmt = str(args.get("date_format") or "")
    if fmt not in dates.CSV_FORMATS:
        fmt = detected if detected in dates.CSV_FORMATS else ""
    if not problem and detected is None:
        problem = "The date column does not hold dates Books can read. Choose another column."
    elif not problem and not fmt:
        problem = "Every date in this file reads two ways. Say whether 3/4 is 4 March or 3 April."
    parsed = csvin.parse_lines(lines, mapping, fmt, style) if not problem else []
    good = [p for p in parsed if not p["problem"]]
    opening = str(acct["opening_date"])
    early = [g for g in good if g["date"] < opening]
    for item in early:
        item["before_opening"] = True
    good = [g for g in good if g["date"] >= opening]
    have = [ledger.dup_key(acct["id"], r["date"], r["amount_cents"], r["payee"])
            for r in store.txns(acct["id"])]
    dup = ledger.split_duplicates(
        have, [ledger.dup_key(acct["id"], r["date"], r["amount_cents"], r["payee"]) for r in good])
    for item, is_dup in zip(good, dup, strict=True):
        item["duplicate"] = is_dup
    return {"account": acct, "header": header, "mapping": mapping, "how": how,
            "detected": detected, "date_format": fmt, "problem": problem, "parsed": parsed,
            "new": [g for g in good if not g["duplicate"]],
            "duplicates": sum(dup), "before_opening": len(early),
            "unread": [p for p in parsed if p["problem"]]}


def preview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    style = ctx.style()
    got = _read(ctx, args)
    shown = []
    for item in got["parsed"][:PREVIEW_ROWS]:
        cents = int(item.get("amount_cents") or 0)
        shown.append({"line": item["line"], "date": item["date"], "payee": item["payee"],
                      "memo": item["memo"], "category": item["category"],
                      "number": item.get("number") or "",
                      "payment": money.format_cents(-cents, style) if cents < 0 else "",
                      "deposit": money.format_cents(cents, style) if cents >= 0 and not
                      item["problem"] else "", "problem": item["problem"],
                      "duplicate": bool(item.get("duplicate")),
                      "before_opening": bool(item.get("before_opening"))})
    return {"header": got["header"], "mapping": got["mapping"].as_dict(), "how": got["how"],
            "detected": got["detected"], "date_format": got["date_format"],
            "problem": got["problem"], "rows": shown, "lines": len(got["parsed"]),
            "new": len(got["new"]), "duplicates": got["duplicates"],
            "before_opening": got["before_opening"],
            "numbers_cut": sum(1 for g in got["new"] if g.get("number_cut")),
            "unread": [u["problem"] for u in got["unread"][:20]],
            "unread_count": len(got["unread"])}


def commit(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``an_import_is_saved_whole``: every new row, the record of the import and the
    remembered mapping, in one transaction."""
    store = ctx.store
    got = _read(ctx, args)
    if got["problem"]:
        raise ValueError(f"{got['problem']} {NOTHING}")
    acct = got["account"]
    cleared = args.get("cleared", True) is not False
    made: list[str] = []
    with store.tx():
        now = ctx.now()
        batch = store.insert("import_batch", {
            "account_id": acct["id"], "name": str(args.get("name") or "")[:120],
            "added": len(got["new"]), "skipped": got["duplicates"],
            "unread": len(got["unread"]), "created": now})
        for item in got["new"]:
            cat_id = None
            if item["category"]:
                try:
                    cat_id, note = resolve_category(ctx, item["category"])
                except ValueError:
                    cat_id, note = None, ""
                if note:
                    made.append(item["category"])
            store.insert("txn", {
                "account_id": acct["id"], "date": item["date"], "payee": item["payee"],
                "category_id": cat_id, "memo": item["memo"], "number": item["number"],
                "amount_cents": item["amount_cents"], "state": "c" if cleared else "",
                "import_id": batch, "created": now, "updated": now})
        store.set_meta(f"mapping:{acct['id']}", {"header": got["header"],
                                                 "mapping": got["mapping"].as_dict()})
        store.run("DELETE FROM upload WHERE token=?", (str(args.get("token") or ""),))
    store.wipe_log()
    said = (f"{len(got['new'])} row{'s' if len(got['new']) != 1 else ''} added to "
            f"{acct['name']}. {got['duplicates']} already there and skipped.")
    if got["before_opening"]:
        said += (f" {got['before_opening']} dated before the account's opening date "
                 f"({acct['opening_date']}) and {'was' if got['before_opening'] == 1 else 'were'}"
                 f" left out.")
    if got["unread"]:
        one = len(got["unread"]) == 1
        said += (f" {len(got['unread'])} could not be read and "
                 f"{'was' if one else 'were'} left out.")
    cut = sum(1 for g in got["new"] if g.get("number_cut"))
    if cut:
        said += (f" {cut} check number{' was' if cut == 1 else 's were'} longer than "
                 f"{csvin.NUMBER_MOST} characters and {'was' if cut == 1 else 'were'} cut "
                 f"to {csvin.NUMBER_MOST}.")
    if made:
        said += f" New categories: {', '.join(sorted(set(made))[:10])}."
    return {"id": batch, "added": len(got["new"]), "skipped": got["duplicates"],
            "unread": len(got["unread"]), "before_opening": got["before_opening"],
            "numbers_cut": cut, "said": said}


def batches(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    acct = need_account(ctx.store, args.get("account"))
    return {"imports": [{"id": b["id"], "name": b["name"], "added": b["added"],
                         "skipped": b["skipped"], "created": b["created"],
                         "undone": b["undone"]}
                        for b in ctx.store.all("SELECT * FROM import_batch WHERE account_id=? "
                                               "ORDER BY id DESC LIMIT 50", (acct["id"],))]}


def undo(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``undoing_an_import_takes_out_exactly_what_it_added``."""
    store = ctx.store
    batch = store.one("SELECT * FROM import_batch WHERE id=?", (int(args.get("id") or 0),))
    if batch is None or batch["undone"]:
        raise ValueError(f"That import is not there to undo. {NOTHING}")
    locked = store.one("SELECT count(*) AS n FROM txn WHERE import_id=? AND state='R'",
                       (batch["id"],))
    if locked and locked["n"] and args.get("confirm") is not True:
        return {"needs_confirm": f"{locked['n']} of these rows are reconciled. Taking them "
                                 "out means that reconciliation will no longer balance."}
    with store.tx():
        gone = store.run("DELETE FROM txn WHERE import_id=?", (batch["id"],))
        store.update("import_batch", batch["id"], undone=ctx.now())
    store.wipe_log()
    return {"removed": gone, "said": f"The import is undone: {gone} row"
                                     f"{'s' if gone != 1 else ''} taken out."}
