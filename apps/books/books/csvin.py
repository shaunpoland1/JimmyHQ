"""Reading a bank's CSV file: which column is which, and each line as a register row.

A ``Mapping`` names the column (by its place, from 0) for each part of a row. The amount
is either one signed column (``amount``, with ``flip`` for a card's file where a purchase
is written above zero) or two columns (``payment`` and ``deposit``). ``guess`` reads the
header; the owner can change any of it before anything is saved.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from typing import Any

from jimmyhq_app.sheets import unguard

from books import dates, money

NUMBER_MOST = 20  # a check number, or a short reference: longer is cut (and counted)
FIELDS = ("date", "payee", "amount", "payment", "deposit", "memo", "category", "number")

_NAMES = {
    "date": ("date", "posted date", "post date", "posting date", "transaction date",
             "trans date", "booking date", "value date", "posted", "date posted", "posting",
             "booked", "effective date", "settlement date", "transaction"),
    "payee": ("payee", "description", "name", "merchant", "details", "narrative",
              "transaction description", "counterparty"),
    "amount": ("amount", "value", "transaction amount", "amount (usd)", "net amount"),
    "payment": ("payment", "debit", "withdrawal", "withdrawals", "money out", "paid out",
                "debit amount", "outflow", "spent", "charge", "charges"),
    "deposit": ("deposit", "credit", "deposits", "money in", "paid in", "credit amount",
                "inflow", "received"),
    "memo": ("memo", "notes", "note", "reference", "comment", "extended description"),
    "category": ("category", "categories"),
    "number": ("number", "check number", "cheque number", "check no", "check no.",
               "cheque no", "check #", "cheque #", "check", "cheque", "chk no", "chk #",
               "chk", "num", "no.", "ref no", "reference number"),
}


@dataclass
class Mapping:
    date: int | None = None
    payee: int | None = None
    amount: int | None = None
    payment: int | None = None
    deposit: int | None = None
    memo: int | None = None
    category: int | None = None
    number: int | None = None
    flip: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_table(text: str) -> list[list[str]]:
    """The file's lines as cells, with a leading byte-order mark and blank lines left out."""
    text = str(text or "").lstrip("﻿")
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text, newline=""), dialect)]
    return [[c.strip() for c in row] for row in rows if any(c.strip() for c in row)]


def guess(header: list[str]) -> Mapping:
    """Rule ``the_mapping_is_guessed_from_the_header``: each part's column, by the header's
    words. A file with both one amount column and two columns uses the one."""
    found: dict[str, int] = {}
    names = [h.strip().lower() for h in header]
    for field in FIELDS:
        for i, name in enumerate(names):
            if i in found.values():
                continue
            if name in _NAMES[field]:
                found[field] = i
                break
    if "amount" in found:
        found.pop("payment", None)
        found.pop("deposit", None)
    return Mapping(**found)


def mapping_from(data: Any, width: int) -> Mapping:
    """A mapping the page sent, checked against the file's width."""
    if not isinstance(data, dict):
        raise ValueError("Say which column holds each part of a line.")
    out: dict[str, Any] = {}
    for field in FIELDS:
        value = data.get(field)
        if value is None or value == "":
            continue
        if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).isdigit():
            raise ValueError(f"The column for {field} is not one of the file's columns.")
        if not 0 <= int(value) < width:
            raise ValueError(f"The column for {field} is not one of the file's columns.")
        out[field] = int(value)
    return Mapping(**out, flip=bool(data.get("flip")))


def mapping_problem(m: Mapping) -> str:
    if m.date is None:
        return "Choose the column that holds the date."
    if m.amount is None and m.payment is None and m.deposit is None:
        return "Choose the column that holds the amount, or the columns for money out and in."
    if m.amount is not None and (m.payment is not None or m.deposit is not None):
        return "Use one signed amount column, or the two columns for money out and in, not both."
    return ""


def cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]


def text_cell(row: list[str], index: int | None) -> str:
    """A cell of text, as Books' own export wrote it: the apostrophe it puts in front of a
    cell a spreadsheet could run as a formula is taken off again, only where it could have
    been put, so exporting and importing again changes nothing."""
    return unguard(cell(row, index))


def amount_of(row: list[str], m: Mapping, style: str) -> int:
    """One line's amount in cents: money in above zero."""
    if m.amount is not None:
        cents = money.parse_cents(cell(row, m.amount), style)
        return -cents if m.flip else cents
    out = cell(row, m.payment)
    came = cell(row, m.deposit)
    paid = abs(money.parse_cents(out, style)) if out else 0
    got = abs(money.parse_cents(came, style)) if came else 0
    if not out and not came:
        raise ValueError("This line has no amount.")
    return got - paid


def date_values(rows: list[list[str]], m: Mapping) -> list[str]:
    return [cell(r, m.date) for r in rows]


def parse_lines(rows: list[list[str]], m: Mapping, fmt: str, style: str) -> list[dict[str, Any]]:
    """Each line as ``{line, date, payee, amount_cents, memo, category, problem}``. A line
    that cannot be read keeps its place with ``problem`` saying why."""
    out = []
    for n, row in enumerate(rows, start=2):  # line 1 is the header
        number = text_cell(row, m.number)
        item: dict[str, Any] = {"line": n, "payee": text_cell(row, m.payee)[:200],
                                "memo": text_cell(row, m.memo)[:500],
                                "category": text_cell(row, m.category)[:80],
                                "number": number[:NUMBER_MOST],
                                "number_cut": len(number) > NUMBER_MOST,
                                "problem": ""}
        try:
            item["date"] = dates.parse_file_date(cell(row, m.date), fmt).isoformat()
            item["amount_cents"] = amount_of(row, m, style)
        except ValueError as error:
            item.update(date=item.get("date", ""), amount_cents=0,
                        problem=f"Line {n}: {error}")
        out.append(item)
    return out
