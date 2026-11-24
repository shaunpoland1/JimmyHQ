"""What happens to an article: its topic's outcomes, the owner's choices, topic health,
the Bench card, and how long its text is kept."""

from __future__ import annotations

import pytest
from jimmyhq_app import CallError
from reader import articles, cards, topics
from reader_rig import BEES, BEES_TEXT, DAY, SOIL, SOIL_TEXT, TOMATO, TOMATO_TEXT


def found(rig, *pages, outcomes=None, picks=None):
    topic = rig.topic(outcomes=outcomes or ["list"])
    rig.results("soil water mulch", *pages)
    rig.pick(*(picks or range(1, len(pages) + 1)))
    rig.search()
    rig.advance()
    return topic


def test_an_article_for_a_list_only_topic_waits_in_triage_and_files_no_card(rig):
    found(rig, (SOIL, SOIL_TEXT))
    [article] = rig.articles("new")
    assert article["outcomes"] == ["list"] and article["pending"] == 0
    assert rig.cards_of() == []
    assert rig.core.call("overview")["triage"][0]["id"] == article["id"]


def test_an_article_for_a_bench_topic_goes_on_the_bench_with_its_why_source_and_excerpt(rig):
    found(rig, (SOIL, SOIL_TEXT), outcomes=["bench"])
    [card] = rig.cards_of()
    [article] = rig.articles("new")
    assert card["subject"] == f"app.reader:article:{article['id']}"
    assert card["title"] == "To read: Soil that holds water through a dry summer"
    assert card["body"].startswith(article["why"])
    assert "Source: news.example.com" in card["body"]
    assert "41% more water" in card["body"] and SOIL in card["body"]
    assert [a["id"] for a in card["actions"]] == ["keep", "later", "skip", "send"]
    assert all(a["closes"] for a in card["actions"])


def test_no_more_cards_go_on_the_bench_in_a_day_than_the_setting_allows(make_rig):
    rig = make_rig(settings={"cards_per_day": 1})
    found(rig, (SOIL, SOIL_TEXT), (BEES, BEES_TEXT), outcomes=["bench"])
    assert len(rig.cards_of()) == 1
    waiting = [a for a in rig.articles("new") if not a["card_id"]]
    assert waiting[0]["note"] == "Today's Bench cards are used; it waits in Triage."
    assert cards.cards_left(rig.ctx) == 0
    rig.clock.advance(DAY)
    assert cards.cards_left(rig.ctx) == 1


def test_keeping_from_the_bench_is_the_owners_vote_and_closes_the_card(rig):
    found(rig, (SOIL, SOIL_TEXT), outcomes=["bench"])
    [card] = rig.cards_of()
    rig.core.decide(card["card_id"], "keep")
    [article] = rig.articles("kept")
    assert article["decided"] == rig.clock.now
    assert any(e["kind"] == "app.reader.article.decided" and e["payload"] == {
        "article": article["id"], "decision": "keep"} for e in rig.core.events)


def test_choosing_on_the_screen_withdraws_the_articles_card(rig):
    found(rig, (SOIL, SOIL_TEXT), outcomes=["bench"])
    [card] = rig.cards_of()
    [article] = rig.articles("new")
    rig.core.call("article.choose", {"id": article["id"], "action": "later"})
    assert rig.core.cards[card["card_id"]]["withdrawn"]
    assert rig.store.article(article["id"])["state"] == "later"


def test_a_card_that_is_no_longer_the_articles_own_changes_nothing(rig):
    found(rig, (SOIL, SOIL_TEXT), outcomes=["bench"])
    [card] = rig.cards_of()
    [article] = rig.articles("new")
    rig.store.set_article(article["id"], card_id="another")
    said = rig.core.decide(card["card_id"], "skip")
    assert "no longer" in said and rig.store.article(article["id"])["state"] == "new"


def test_skipping_an_article_counts_against_its_topic_and_keeping_counts_for_it(rig):
    topic = found(rig, (SOIL, SOIL_TEXT), (BEES, BEES_TEXT), (TOMATO, TOMATO_TEXT))
    first, second, third = rig.articles("new")
    rig.core.call("article.choose", {"id": first["id"], "action": "keep"})
    rig.core.call("article.choose", {"id": second["id"], "action": "skip"})
    rig.core.call("article.choose", {"id": third["id"], "action": "send"})
    said = topics.health(rig.ctx, rig.store.topic(topic["id"]))
    assert (said["found"], said["kept"], said["skipped"], said["waiting"]) == (3, 2, 1, 0)
    assert said["last_found_ms"] is not None and said["quiet_days"] == 0


def test_a_topic_that_has_found_nothing_for_the_set_days_is_quiet(rig):
    topic = rig.topic()
    assert not topics.health(rig.ctx, topic)["quiet"]
    rig.clock.advance(14 * DAY)
    said = topics.health(rig.ctx, rig.store.topic(topic["id"]))
    assert said["quiet"] and said["quiet_days"] == 14
    assert topics.is_quiet(0, 0, 1) is False and topics.is_quiet(DAY, 0, 1) is True


def test_send_on_offers_the_article_to_other_apps_in_the_fixed_shape(rig):
    found(rig, (SOIL, SOIL_TEXT))
    [article] = rig.articles("new")
    rig.core.call("article.choose", {"id": article["id"], "action": "send"})
    [offer] = rig.core.call("offered.list")
    assert offer == {"offer": 1, "article": article["id"], "url": SOIL,
                     "title": "Soil that holds water through a dry summer",
                     "topic": "Garden soil", "why": article["why"],
                     "text_ref": f"reader:article:{article['id']}", "when_ms": rig.clock.now}
    [printed] = [e for e in rig.core.events if e["kind"] == "app.reader.article.offered"]
    assert printed["payload"] == offer


def test_an_article_for_an_offer_topic_is_offered_once_however_often_it_is_asked(rig):
    found(rig, (SOIL, SOIL_TEXT), outcomes=["offer"])
    [article] = rig.articles("new")
    rig.core.call("article.choose", {"id": article["id"], "action": "send"})
    assert len(rig.core.call("offered.list")) == 1
    assert len([e for e in rig.core.events if e["kind"] == "app.reader.article.offered"]) == 1


def test_kept_text_is_deleted_after_the_set_days_unless_the_owner_kept_the_article(make_rig):
    rig = make_rig(settings={"keep_text_days": 10})
    found(rig, (SOIL, SOIL_TEXT), (BEES, BEES_TEXT), (TOMATO, TOMATO_TEXT))
    kept, later, waiting = rig.articles("new")
    rig.core.call("article.choose", {"id": kept["id"], "action": "keep"})
    rig.core.call("article.choose", {"id": later["id"], "action": "later"})
    rig.clock.advance(9 * DAY)
    rig.advance()
    assert all(a["text"] for a in rig.articles() if a["state"] != "passed")
    rig.clock.advance(DAY)
    rig.advance()
    still = rig.store.article(kept["id"])
    assert "41% more water" in still["text"] and still["sentences"]
    for gone in (later, waiting):
        row = rig.store.article(gone["id"])
        assert row["text"] == "" and row["sentences"] == [] and row["text_deleted"]
        assert row["url"] and row["why"] and row["runs"]  # still recognised


def test_an_article_whose_text_was_deleted_is_still_not_suggested_again(make_rig):
    rig = make_rig(settings={"keep_text_days": 1})
    found(rig, (SOIL, SOIL_TEXT))
    rig.clock.advance(2 * DAY)
    rig.advance()
    rig.results("soil water mulch", ("https://copies.example.com/soil", SOIL_TEXT))
    rig.search()
    [dropped] = rig.articles("dropped")
    assert dropped["said"].startswith("The same story as article 1")


def test_text_is_kept_only_for_articles_the_owner_did_not_keep():
    old = {"state": "later", "text": "x", "created": 0}
    assert articles.text_expired(old, 3 * DAY, 3)
    assert not articles.text_expired(old, 3 * DAY - 1, 3)
    assert not articles.text_expired({**old, "state": "kept"}, 99 * DAY, 3)
    assert not articles.text_expired({**old, "state": "sent"}, 99 * DAY, 3)
    assert not articles.text_expired({**old, "text": ""}, 99 * DAY, 3)


def test_an_unknown_choice_changes_nothing_and_says_what_to_choose(rig):
    found(rig, (SOIL, SOIL_TEXT))
    [article] = rig.articles("new")
    with pytest.raises(CallError, match="Nothing was changed. Choose keep"):
        rig.core.call("article.choose", {"id": article["id"], "action": "publish"})
    assert rig.store.article(article["id"])["state"] == "new"
