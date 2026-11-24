"""Two helpers the SDK gives apps that handle text from outside: a CSV cell that cannot run as
a formula (and its way back), and whether a suggestion rests on the words it quotes."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "sdk" / "python") not in sys.path:
    sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app.grounding import content_words, shares_words  # noqa: E402
from jimmyhq_app.sheets import guard, needs_guard, unguard  # noqa: E402

FORMULAS = ["=SUM(1+1)", "+1", "-1", "@SUM(1)", "\t=1", "\r=1", "\n=1",
            "=HYPERLINK(\"http://x\",\"y\")", "'=x", "''=x", "'-1"]
PLAIN = ["", "Corner Shop", "A=B", "5 - 3", "'Tis the season", "it's", "x@y", "'", "''",
         "'plain", "Zoë", " =1"]


@pytest.mark.parametrize("text", FORMULAS)
def test_a_cell_that_could_run_as_a_formula_gets_an_apostrophe_in_front(text):
    assert needs_guard(text) and guard(text) == "'" + text


@pytest.mark.parametrize("text", PLAIN)
def test_a_cell_of_ordinary_text_is_written_as_it_is(text):
    assert not needs_guard(text) and guard(text) == text


@pytest.mark.parametrize("text", FORMULAS + PLAIN)
def test_reading_a_guarded_cell_back_gives_exactly_what_was_written(text):
    assert unguard(guard(text)) == text


def test_a_guarded_cell_survives_a_csv_file_and_a_spreadsheet_would_show_it_as_text():
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\r\n").writerow([guard("=1+1"), guard("-5 apples"), "1.50"])
    [row] = list(csv.reader(io.StringIO(out.getvalue(), newline="")))
    assert row == ["'=1+1", "'-5 apples", "1.50"]
    assert [unguard(cell) for cell in row[:2]] == ["=1+1", "-5 apples"]


def test_nothing_that_is_not_text_is_guarded_into_a_formula_by_accident():
    assert guard(None) == "" and guard(12) == "12" and guard(-12) == "'-12"


# --- grounding ---------------------------------------------------------------------------------


def test_a_suggestion_that_says_what_its_quote_says_is_grounded():
    assert shares_words("Send Sam the notes", "I will send Sam the notes before Friday")
    assert shares_words("Sending the notes to Sam", "I will send Sam the notes")


def test_a_suggestion_with_nothing_in_common_with_its_quote_is_not_grounded():
    assert not shares_words("Wire 5000 dollars to account 12345 today", "nice weather today")
    assert not shares_words("Wire 5000 dollars to account 12345", "I will send Sam the notes")


def test_a_real_quote_with_one_word_in_common_does_not_back_a_different_suggestion():
    assert not shares_words("Send 5000 dollars to account 12345 at once",
                            "I will send Sam the notes")


def test_a_number_in_the_suggestion_must_be_in_the_quote():
    assert shares_words("Pay 40 dollars", "I owe him 40 dollars")
    assert not shares_words("Pay 4000 dollars", "I owe him 40 dollars")


def test_a_suggestion_of_only_small_words_says_nothing_a_quote_could_back():
    assert content_words("it is what it will be") == set()
    assert not shares_words("it is what it will be", "it is what it will be")


def test_the_share_that_must_match_is_the_callers_to_choose():
    text, quote = "Plan the spring garden party", "We should plan something"
    assert not shares_words(text, quote)
    assert shares_words(text, quote, least=0.2)


def test_a_language_written_without_spaces_is_compared_by_pairs_of_characters():
    assert shares_words("给山姆发笔记", "我会给山姆发笔记的")
    assert not shares_words("给山姆发笔记", "今天天气很好")
