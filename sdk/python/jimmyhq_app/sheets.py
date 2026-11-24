"""Text for a spreadsheet: a cell that cannot run as a formula, and the way back.

A CSV cell that starts with ``=``, ``+``, ``-`` or ``@`` (or a tab or a line break) is run as
a formula by a spreadsheet that opens the file, and a payee, memo or donor name is text from
outside. ``guard`` puts an apostrophe in front of such a cell, which a spreadsheet takes as
"this is text" and does not show; ``unguard`` takes it off again when the app reads its own
file back, and only where ``guard`` could have put it, so reading a file written by ``guard``
gives back exactly what was written and a plain ``'Tis`` is never touched.

    write.writerow([date, guard(payee), guard(memo), plain_amount])
    payee = unguard(cell)

Amounts are not text: write a number as a number (``-12.50``), never through ``guard``.
"""

from __future__ import annotations

__all__ = ["FORMULA_STARTS", "guard", "needs_guard", "unguard"]

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n")
_MARK = "'"


def needs_guard(text: str) -> bool:
    """Whether a spreadsheet could read ``text`` as a formula: its first character, past any
    apostrophes already there, is one of ``FORMULA_STARTS``."""
    return text.lstrip(_MARK)[:1] in FORMULA_STARTS


def guard(value: object) -> str:
    """``value`` as a cell of text a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return _MARK + text if needs_guard(text) else text


def unguard(text: str) -> str:
    """What ``guard`` was given: the apostrophe is taken off only when ``guard`` would have
    put one there."""
    return text[1:] if text.startswith(_MARK) and needs_guard(text[1:]) else text
