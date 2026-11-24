"""Money as whole cents, never floats: reading what the owner types, and writing it back.

``parse_cents`` is the one place typed money is read. It takes "1,234.50", "12", "-5.25"
and "(5.25)" (a bank's way of writing a negative), and refuses anything it cannot read
for certain with a sentence that says what to type instead. ``style`` is the owner's
number style: ``point`` writes 1,234.50 and ``comma`` writes 1.234,50.

Accounts of the kinds people owe on (a credit card, a loan) hold their balance the other
way round: a charge takes the balance further below zero, and the screen shows the amount
owed as a plain number. ``shown`` and ``from_shown`` turn one into the other.
"""

from __future__ import annotations

import re

STYLES = ("point", "comma")
OWED_KINDS = ("credit_card", "loan")
MOST_CENTS = 10**13  # a hundred billion: anything larger is a typing slip

_SYMBOLS = "$€£"


def _marks(style: str) -> tuple[str, str]:
    """The thousands mark and the decimal mark for a style."""
    return (".", ",") if style == "comma" else (",", ".")


def parse_cents(text: object, style: str = "point") -> int:
    """Rule ``typed_money_is_read_exactly``: what the owner typed, as whole cents.

    Raises ``ValueError`` with a sentence for anything unclear: no digits, letters, more
    than two places after the decimal mark, thousands marks in the wrong places, a sign
    both ways, or an amount too large to be meant."""
    if isinstance(text, bool) or not isinstance(text, str | int):
        raise ValueError("Type an amount, such as 12.50.")
    raw = str(text).strip()
    thousands, decimal = _marks(style)
    example = "1,234.50" if style != "comma" else "1.234,50"
    if not raw:
        raise ValueError("Type an amount, such as 12.50.")
    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative, raw = True, raw[1:-1].strip()
    if raw[:1] in "+-":
        if negative:
            raise ValueError(f"\"{text}\" has a minus sign and brackets. Use one of them.")
        negative, raw = raw[0] == "-", raw[1:].strip()
    if raw[:1] and raw[0] in _SYMBOLS:
        raw = raw[1:].strip()
    if raw[:1] in "+-(":
        raise ValueError(f"\"{text}\" is not an amount Books can read for certain. "
                         f"Type it like {example} or -5.25.")
    if not raw or not re.fullmatch(r"[\d.,]+", raw) or not any(c.isdigit() for c in raw):
        raise ValueError(f"\"{text}\" is not an amount. Type digits, such as {example}.")
    whole, _, part = raw.partition(decimal)
    if decimal in part:
        raise ValueError(f"\"{text}\" has more than one {'comma' if decimal == ',' else 'point'}"
                         f" for the decimals. Type it like {example}.")
    if thousands in part:
        raise ValueError(f"\"{text}\" is not an amount Books can read for certain. "
                         f"Type it like {example}.")
    if len(part) > 2:
        raise ValueError(f"\"{text}\" has more than two places after the decimal mark, so it "
                         f"is not clear what you meant. Type it like {example}.")
    if part and not part.isdigit():
        raise ValueError(f"\"{text}\" is not an amount. Type it like {example}.")
    if thousands in whole:
        groups = whole.split(thousands)
        if not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])
                and all(g.isdigit() for g in groups)):
            raise ValueError(f"\"{text}\" has its thousands marks in odd places, so it is not "
                             f"clear what you meant. Type it like {example}, or with no marks.")
        whole = "".join(groups)
    if whole and not whole.isdigit():
        raise ValueError(f"\"{text}\" is not an amount. Type it like {example}.")
    cents = int(whole or "0") * 100 + int((part + "00")[:2])
    if cents >= MOST_CENTS:
        raise ValueError(f"\"{text}\" is far larger than an amount Books keeps. Check it.")
    return -cents if negative else cents


def format_cents(cents: int, style: str = "point", *, grouped: bool = True) -> str:
    """Rule ``money_is_written_back_the_same_way``: whole cents as the owner reads them,
    "-1,234.50" (or "-1.234,50"). ``grouped=False`` leaves out the thousands marks."""
    thousands, decimal = _marks(style)
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(int(cents)), 100)
    digits = f"{whole:,}".replace(",", thousands) if grouped else str(whole)
    return f"{sign}{digits}{decimal}{part:02d}"


def plain(cents: int) -> str:
    """Cents for a file another program reads: "-1234.50", whatever the owner's style."""
    return format_cents(cents, "point", grouped=False)


def owed(kind: str) -> bool:
    return kind in OWED_KINDS


def shown(kind: str, cents: int) -> int:
    """Rule ``an_owed_balance_reads_as_a_plain_number``: the balance as the screen shows it.
    For a card or a loan that is the amount owed, so -500.00 held reads as 500.00."""
    return -cents if owed(kind) else cents


def from_shown(kind: str, cents: int) -> int:
    """The other way: a balance the owner typed as the screen shows it, as held."""
    return -cents if owed(kind) else cents
