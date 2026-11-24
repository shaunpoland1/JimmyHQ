"""Money as whole cents: what the owner types is read exactly, or refused with a sentence."""

from __future__ import annotations

import pytest
from books.money import format_cents, from_shown, parse_cents, plain, shown


@pytest.mark.parametrize(("typed", "cents"), [
    ("1,234.50", 123450), ("12", 1200), ("-5.25", -525), ("(5.25)", -525),
    ("0.5", 50), (".75", 75), ("1234.5", 123450), ("  42.00 ", 4200), ("+3", 300),
    ("$19.99", 1999), ("-$7.10", -710), ("1,000,000", 100_000_000), ("0", 0),
])
def test_typed_money_is_read_exactly_as_whole_cents(typed, cents):
    assert parse_cents(typed) == cents
    assert isinstance(parse_cents(typed), int)


@pytest.mark.parametrize("typed", [
    "", "   ", "abc", "12.345", "1.2.3", "12,34", "1,23,456", "--5", "-(5)", "(5",
    "5-", "12a", "1e5", "$", "-", "1 000", "99999999999999",
])
def test_anything_unclear_is_refused_with_a_sentence(typed):
    with pytest.raises(ValueError) as refused:
        parse_cents(typed)
    assert str(refused.value).endswith(".") and len(str(refused.value)) > 20


def test_a_float_or_a_true_is_never_taken_as_money():
    for value in (12.5, True, None, [1]):
        with pytest.raises(ValueError):
            parse_cents(value)  # type: ignore[arg-type]


def test_the_comma_style_reads_and_writes_the_other_way_round():
    assert parse_cents("1.234,50", "comma") == 123450
    assert parse_cents("12,5", "comma") == 1250
    with pytest.raises(ValueError):
        parse_cents("1,234.50", "comma")
    assert format_cents(-123450, "comma") == "-1.234,50"


def test_money_is_written_back_the_same_way():
    assert format_cents(123450) == "1,234.50"
    assert format_cents(-5) == "-0.05"
    assert format_cents(0) == "0.00"
    assert format_cents(100_000_000) == "1,000,000.00"
    for cents in (0, 1, -1, 99, 123456789, -42):
        assert parse_cents(format_cents(cents)) == cents
    assert plain(-123450) == "-1234.50"


def test_an_owed_balance_reads_as_a_plain_number():
    assert shown("credit_card", -50000) == 50000 and shown("loan", -1) == 1
    assert shown("checking", -50000) == -50000
    assert from_shown("credit_card", shown("credit_card", -321)) == -321
