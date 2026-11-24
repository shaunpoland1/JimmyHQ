"""``jimmyhq_app.claims``: the number and quotation rules every app that checks a draft
against its sources shares, run through the shared cases in ``jimmyhq_app.testing``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from jimmyhq_app import claims  # noqa: E402
from jimmyhq_app.testing import NUMBER_CASES, QUOTE_CASES  # noqa: E402


@pytest.mark.parametrize("written, said, tolerance, expected", NUMBER_CASES)
def test_a_number_matches_its_source_by_the_shared_cases(written, said, tolerance, expected):
    [draft], [source] = claims.find_numbers(written), claims.find_numbers(said)
    assert claims.number_matches(draft, source, tolerance) is expected


@pytest.mark.parametrize("sentence, span, expected", QUOTE_CASES)
def test_a_quotation_is_word_for_word_by_the_shared_cases(sentence, span, expected):
    assert bool(claims.where_quote(span, {("S1", 1): sentence})) is expected


def test_every_number_is_found_however_many_there_are():
    text = " ".join(f"{n}%" for n in range(1, 41))
    assert len(claims.find_numbers(text)) == 40


def test_a_number_is_found_under_whatever_keys_the_app_chooses():
    sentences = {(1, 1): "Nothing here.", (2, 3): "Some 1,240 homes.", (2, 4): "Up to 1,239 homes."}
    [number] = claims.find_numbers("1,240")
    assert claims.where_number(number, sentences, 1.0) == [(2, 3), (2, 4)]


def test_an_attribution_is_heard_near_a_quotation_or_from_a_colon_before_it():
    assert claims.quoted("She said \"we were never told\" twice.") == [
        ("we were never told", True)]
    assert claims.quoted("The sign: \"keep the gate shut\"") == [("keep the gate shut", True)]
    assert claims.quoted("A \"green\" choice.") == []
    assert claims.quoted("They call it \"the quiet street of ours\".") == [
        ("the quiet street of ours", False)]


def test_a_quotation_across_two_sentences_is_found_in_the_whole_text():
    text = "We watered less. It worked well."
    assert claims.where_quote("less. It worked", {1: "We watered less.", 2: "It worked well."}) \
        == []
    assert claims.in_text("less. It worked", text)
