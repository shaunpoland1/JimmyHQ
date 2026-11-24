"""Reading a source: cleaned text kept, sentences numbered the same way every time, and a
source that cannot be used stopping the piece before anything is spent."""

from __future__ import annotations

import pytest
from posts import sources
from posts_rig import ARTICLE, ARTICLE_TEXT


def test_the_splitter_keeps_abbreviations_decimals_and_initials_inside_one_sentence():
    text = ("Dr. Rivera measured 3.5 cm of rain on Main St. in one hour. J. K. Doe agreed. "
            "Costs rose e.g. for seed. Is that all? Yes!")
    assert sources.split_sentences(text) == [
        "Dr. Rivera measured 3.5 cm of rain on Main St. in one hour.",
        "J. K. Doe agreed.",
        "Costs rose e.g. for seed.",
        "Is that all?",
        "Yes!",
    ]


def test_a_paragraph_break_always_ends_a_sentence():
    assert sources.split_sentences("A heading with no stop\n\nThe first line.") == [
        "A heading with no stop", "The first line."]


def test_a_closing_quotation_mark_stays_with_its_sentence():
    text = "“It works.” She smiled. \"Again?\" he asked."
    assert sources.split_sentences(text) == ["“It works.”", "She smiled.",
                                             "\"Again?\" he asked."]


def test_the_same_text_always_gives_the_same_numbered_sentences():
    cleaned = sources.clean(ARTICLE_TEXT)
    first = sources.split_sentences(cleaned)
    assert first == sources.split_sentences(sources.clean(ARTICLE_TEXT))
    assert sources.numbered(first).splitlines()[1].startswith("[2] The valley council")
    assert len(first) == 10


def test_cleaning_takes_off_markdown_and_keeps_paragraphs_whatever_the_line_endings():
    raw = "## Title\r\n\r\n**Bold** words and [a link](https://example.com/a).\r\n- item one\r\n"
    assert sources.clean(raw) == "Title\n\nBold words and a link. item one"


@pytest.mark.parametrize("given", ["", "news.example.com/a", "ftp://example.com/a",
                                   "javascript:alert(1)", "https://", "https://exa mple.com"])
def test_only_a_whole_web_address_is_taken_as_a_link(given):
    with pytest.raises(sources.SourceProblem, match="Paste the whole link"):
        sources.check_url(given)


def test_an_unreadable_source_spends_nothing(rig):
    page = rig.page()
    piece = rig.core.call("new.link", {"page_id": page["id"], "url": ARTICLE})  # no such page
    rig.advance(3)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stopped_source"
    assert "Nothing was spent" in piece["error"] and "Retry" in piece["error"]
    assert rig.core.asked == [] and rig.core.embedded == []
    assert rig.cards_of("draft") == [] and rig.cards_of("overlap") == []


def test_a_source_too_short_to_check_against_stops_before_any_model_call(rig):
    page = rig.page()
    piece = rig.link(page, text="Sign in to read more.")
    rig.advance()
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stopped_source"
    assert "at least 400" in piece["error"] and rig.core.asked == []


def test_a_stopped_source_can_be_tried_again_once_it_reads(rig):
    page = rig.page()
    piece = rig.core.call("new.link", {"page_id": page["id"], "url": ARTICLE})
    rig.advance()
    rig.core.pages[ARTICLE] = ARTICLE_TEXT
    assert rig.core.call("piece.retry", {"id": piece["id"]})["state"] == "reading"
    rig.advance(2)  # read, then the overlap check
    assert rig.store.piece(piece["id"])["state"] == "writing"


def test_with_web_lookup_switched_off_the_piece_says_so_and_spends_nothing(rig):
    page = rig.page()
    rig.core.web_on = False
    piece = rig.link(page)
    rig.advance()
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stopped_source" and "web lookup switched off" in piece["error"]
    assert rig.core.asked == []


def test_a_long_article_is_read_piece_by_piece_and_kept_whole(make_rig):
    rig = make_rig()
    rig.core.page_chars = 200
    page = rig.page()
    piece = rig.link(page)
    rig.advance()
    piece = rig.store.piece(piece["id"])
    assert len(piece["sentences"]) == 10
    assert piece["source_text"] == sources.clean(ARTICLE_TEXT)


def test_posts_numbers_the_shared_sample_article_as_the_sdk_does(rig):
    from jimmyhq_app.testing import SAMPLE_ARTICLE, SAMPLE_ARTICLE_SENTENCES
    from jimmyhq_app.text import clean, split_sentences

    url = "https://news.example.com/2027/sample"
    rig.core.pages[url] = SAMPLE_ARTICLE
    read = sources.read(rig.app, url, min_chars=50, keep_chars=60_000)
    assert read.sentences == split_sentences(clean(SAMPLE_ARTICLE))
    assert len(read.sentences) == SAMPLE_ARTICLE_SENTENCES
