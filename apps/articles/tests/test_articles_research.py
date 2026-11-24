"""Coverage and research in plain mode: phrasings, reading, ranking, too few sources, and
the research summary that cites every statement."""

from __future__ import annotations

from articles import config, rank, research
from articles.config import Config
from articles_rig import (
    COOK_URL,
    FORUM_URL,
    NEWS_URL,
    PAGES,
    SOIL_URL,
    SURVEY,
    SURVEY_URL,
    phrasings,
    summary,
    text_of,
)
from jimmyhq_app import word_runs


def cfg(**more) -> Config:
    from articles_rig import TEST_SETTINGS

    return Config({**config.defaults(), **TEST_SETTINGS, **more})


def page(url: str, sentences: list[str]) -> dict:
    from articles.sources import host_of

    return {"url": url, "final_url": url, "host": host_of(url), "title": url,
            "text": text_of(sentences), "sentences": sentences}


def researched(rig, urls=None, pages=None, *, replies=None):
    site = rig.site()
    rig.web(urls, pages)
    piece = rig.topic(site)
    rig.core.replies += replies if replies is not None else [phrasings()]
    rig.advance(3)
    return rig.store.piece(piece["id"])


# --- coverage ---------------------------------------------------------------------------------


def test_a_topic_close_to_a_recent_article_raises_a_card_before_anything_is_spent(rig):
    first = rig.to_card()
    site = rig.store.site(first["site_id"])
    searched, asked = len(rig.core.searched), len(rig.core.asked)
    again = rig.topic(site)  # the same topic as before
    rig.advance(1)
    held = rig.store.piece(again["id"])
    assert held["state"] == "held_coverage" and held["coverage"]["method"] == "embeddings"
    [card] = rig.cards_of("coverage")
    assert [a["id"] for a in card["actions"]] == ["write_anyway", "drop"]
    assert "by meaning" in card["title"] and "Nothing has been spent" in card["body"]
    assert (len(rig.core.searched), len(rig.core.asked)) == (searched, asked)
    rig.core.decide(card["card_id"], "write_anyway")
    assert rig.store.piece(again["id"])["state"] == "searching"


def test_without_an_embeddings_model_coverage_compares_by_words_and_says_so(rig):
    first = rig.to_card()

    def refuse(text):
        from jimmyhq_app import CallError
        raise CallError("unavailable", "No embeddings model is set up.")
    rig.core.embedder = refuse
    again = rig.topic(rig.store.site(first["site_id"]))
    rig.advance(1)
    found = rig.store.piece(again["id"])["coverage"]
    assert found["method"] == "words" and "embeddings model did not answer" in found["note"]
    assert found["close"] is True


def test_a_new_topic_unlike_the_sites_articles_goes_on_to_research(rig):
    first = rig.to_card()
    other = rig.topic(rig.store.site(first["site_id"]), topic="Keeping bees on a city roof")
    rig.advance(1)
    piece = rig.store.piece(other["id"])
    assert piece["state"] == "searching" and piece["coverage"]["close"] is False


# --- searching ------------------------------------------------------------------------------


def test_the_topic_is_searched_in_a_few_phrasings_with_no_address_or_repeat():
    got = research.take_phrasings({"phrasings": ["garden plots", "Garden plots",
                                                 "see https://x.example.com", 7, "ab",
                                                 "plot costs", "plot yields", "more"]}, 3)
    assert got == ["garden plots", "plot costs", "plot yields"]


def test_when_the_model_gives_no_phrasings_the_topic_is_searched_as_written(rig):
    site = rig.site()
    rig.web()
    piece = rig.topic(site, angle="for renters")
    rig.advance(2)
    assert rig.core.searched == ["Do community garden plots save families money?",
                                 "Do community garden plots save families money? for renters"]
    [line] = [t for t in rig.store.turns(piece["id"]) if t["step"] == "searching"]
    assert "Searched the topic as written" in line["gloss"]


def test_a_page_found_by_two_searches_is_read_once(rig):
    piece = researched(rig)
    assert len(piece["candidates"]) == len(PAGES)
    assert rig.core.read.count(SURVEY_URL) == 1


# --- reading and ranking --------------------------------------------------------------------


def test_each_source_is_numbered_and_says_why_it_was_kept(rig):
    piece = researched(rig)
    kept = rig.store.sources(piece["id"])
    assert [s["n"] for s in kept] == [1, 2, 3, 4]
    assert kept[0]["url"] == SURVEY_URL and kept[0]["why"].startswith("Kept: a primary source")
    assert kept[0]["sentences"] == SURVEY
    assert kept[3]["url"] == FORUM_URL and kept[3]["weak"] == 1
    assert "though weak" in kept[3]["why"]


def test_two_copies_of_the_same_story_count_once(rig):
    piece = researched(rig)
    hosts = [s["host"] for s in rig.store.sources(piece["id"])]
    assert "news.example.com" not in hosts
    [left] = [s for s in rig.store.dropped_sources(piece["id"]) if s["url"] == NEWS_URL]
    assert "the same story as survey.example.com" in left["why"]


def test_unreadable_and_too_short_pages_are_left_out_with_a_sentence(rig):
    short = "https://short.example.com/a"
    gone = "https://gone.example.com/b"
    pages = {**PAGES, short: ["Too short to use."]}
    piece = researched(rig, [gone, short, *PAGES], pages)
    left = {s["url"]: s["why"] for s in rig.store.dropped_sources(piece["id"])}
    assert "gave only" in left[short] and "a source needs at least 200" in left[short]
    assert "could not be read" in left[gone]


def test_primary_and_substantial_sources_rank_before_weak_and_short_ones():
    pages = [page(FORUM_URL, PAGES[FORUM_URL]), page(COOK_URL, PAGES[COOK_URL]),
             page(SURVEY_URL, SURVEY), page(SOIL_URL, PAGES[SOIL_URL])]
    ranked = rank.rank(pages, cfg())
    assert [r.page["url"] for r in ranked if r.kept] == [SURVEY_URL, COOK_URL, SOIL_URL,
                                                         FORUM_URL]


def test_the_ranking_keeps_at_most_the_sources_asked_for_and_says_so():
    pages = [page(SURVEY_URL, SURVEY), page(SOIL_URL, PAGES[SOIL_URL]),
             page(COOK_URL, PAGES[COOK_URL])]
    ranked = rank.rank(pages, cfg(max_sources=2))
    assert [r.kept for r in ranked] == [True, True, False]
    assert "2 better sources were kept first" in ranked[2].why


def test_a_host_ending_the_owner_prefers_counts_as_primary():
    assert rank.is_primary("stats.example.gov", cfg(primary_hosts=[]))
    assert not rank.is_primary("stats.example.gov", cfg(primary_hosts=[], primary_endings=[]))
    assert rank.is_weak("a.forum.example.com", cfg())


def test_the_same_story_rule_reads_shared_word_runs():
    survey, news = word_runs(text_of(SURVEY)), word_runs(text_of(PAGES[NEWS_URL]))
    assert rank.same_story(survey, news, cfg())
    assert not rank.same_story(survey, word_runs(text_of(PAGES[SOIL_URL])), cfg())


def test_too_few_readable_sources_stops_the_piece_before_any_writing(rig):
    piece = researched(rig, [SURVEY_URL, NEWS_URL, "https://gone.example.com/x"])
    assert piece["state"] == "stopped_research"
    assert "Only 1 good source could be used" in piece["error"]
    assert "Press Retry" in piece["error"] and "Nothing was written" in piece["error"]
    asked = len(rig.core.asked)
    rig.advance(3)
    assert len(rig.core.asked) == asked  # nothing is asked to write
    assert rig.store.revisions(piece["id"]) == []
    rig.core.call("piece.retry", {"id": piece["id"]})
    assert rig.store.piece(piece["id"])["state"] == "searching"


def test_when_the_web_cannot_be_searched_the_piece_stops_with_a_sentence(rig):
    rig.core.web_on = False
    piece = researched(rig)
    assert piece["state"] == "stopped_research"
    assert "web could not be searched" in piece["error"]


# --- the research summary -------------------------------------------------------------------


def test_every_statement_of_the_summary_cites_a_real_source_sentence(rig):
    piece = researched(rig, replies=[phrasings(), summary([
        {"text": "Plot holders spent 18% less.", "cites": [{"source": 1, "sentence": 1}]},
        {"text": "No citation at all.", "cites": []},
        {"text": "A made-up citation.", "cites": [{"source": 9, "sentence": 1}]},
        {"text": "Read https://made-up.example.net now.",
         "cites": [{"source": 2, "sentence": 2}]}])])
    rig.advance(1)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "outlining"
    assert [s["cites"] for s in piece["summary"]] == [[[1, 1]], [[2, 2]]]
    assert "made-up" not in piece["summary"][1]["text"]
    [line] = [t for t in rig.store.turns(piece["id"]) if t["step"] == "summarising"]
    assert "2 set aside for citing no real sentence" in line["gloss"]


def test_a_summary_that_cites_nothing_real_fails_its_step(rig):
    piece = researched(rig, replies=[phrasings(), summary([
        {"text": "Nothing cited.", "cites": []}])])
    rig.advance(1)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stalled" and "cited no sentence" in piece["error"]


def test_web_text_reaches_the_models_only_as_untrusted_data(rig):
    rig.to_card()
    for asked in rig.core.asked:
        assert "garden plot spent 18%" not in asked["prompt"]
        assert "garden plot spent 18%" not in (asked.get("system") or "")
    summary_call = rig.core.asked[1]
    assert any("Source S1:" in block and "[1] A city survey" in block
               for block in summary_call["untrusted"])
