"""Mining into Memory: off until the owner switches it on, and only ever the article's
own sentences."""

from __future__ import annotations

import pytest
from jimmyhq_app import CallError
from reader import mining
from reader_rig import SOIL, SOIL_TEXT


def suggested(rig, outcomes=("list",)):
    rig.topic(outcomes=list(outcomes))
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()
    rig.advance()
    [article] = rig.articles("new")
    return article


def test_mining_stays_off_until_the_owner_switches_it_on(rig):
    article = suggested(rig, outcomes=["memory"])
    assert rig.store.article(article["id"])["mine_wanted"] == 0
    assert "mining is off" in rig.store.article(article["id"])["note"]
    with pytest.raises(CallError, match="Mining is off, so nothing was mined"):
        rig.core.call("article.choose", {"id": article["id"], "action": "mine"})
    said = rig.core.tool("mine", {"article": article["id"], "sentences": "2"},
                         role="researcher")
    assert not said["ok"] and "nothing was kept for Memory" in said["result"]
    assert rig.store.all("SELECT * FROM fact") == []
    assert rig.core.collect("facts") == {"items": [], "cursor": None}
    assert len(rig.core.asked) == 1  # only the ranker: no model was asked to mine


def test_a_fact_is_only_mined_from_sentences_that_are_in_the_article(make_rig):
    rig = make_rig(settings={"mine_facts": True})
    article = suggested(rig)
    rig.core.call("article.choose", {"id": article["id"], "action": "mine"})
    rig.core.replies.append({"facts": [
        {"sentences": [2, 99], "note": "The headline figure."},
        {"sentences": [42], "note": "A sentence the article does not have."},
        {"sentences": [], "note": "Made up: mulch triples yields."}]})
    rig.advance()
    [fact] = rig.store.all("SELECT * FROM fact")
    assert fact["sentences"] == [2]
    assert fact["text"] == article["sentences"][1]
    assert "triples" not in fact["text"] and fact["by"] == "owner"
    [item] = rig.core.collect("facts")["items"]
    assert item["text"].startswith("A three-year field trial in the valley found")
    assert item["where"] == SOIL and SOIL in item["text"]
    assert "headline figure" not in item["text"]  # the note stays in Reader
    assert item["who"] == "owner" and item["ref"] == f"fact:{fact['id']}"


def test_the_mining_model_sees_the_article_only_as_data(make_rig):
    rig = make_rig(settings={"mine_facts": True})
    article = suggested(rig)
    rig.core.call("article.choose", {"id": article["id"], "action": "mine"})
    rig.core.replies.append({"facts": [{"sentences": [3], "note": "Size of the trial."}]})
    rig.advance()
    asked = rig.core.asked[-1]
    assert "120 plots" not in asked["prompt"]
    assert "[3] The trial covered 120 plots on four farms." in asked["untrusted"][0]


def test_a_topic_that_mines_its_articles_mines_them_once_mining_is_on(make_rig):
    rig = make_rig(settings={"mine_facts": True})
    rig.topic(outcomes=["memory"])
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()
    rig.core.replies.append({"facts": [{"sentences": [5], "note": "Less watering."}]})
    rig.advance()
    rig.advance()
    [fact] = rig.store.all("SELECT * FROM fact")
    assert fact["by"] == "plain"
    assert rig.core.collect("facts")["items"][0]["who"] == "app"


def test_facts_are_numbers_naming_sentences_the_article_has():
    sentences = ["One.", "Two.", "Three."]
    assert mining.facts_from(sentences, [3, 1, 3, 0, 4, True]) == ([1, 3], [0, 4, True])
    assert mining.facts_from([], [1]) == ([], [1])


def test_memory_is_handed_facts_after_its_cursor_and_nothing_once_mining_is_off(make_rig):
    rig = make_rig(settings={"mine_facts": True})
    article = suggested(rig)
    for n in (2, 3):
        rig.core.tool("mine", {"article": article["id"], "sentences": str(n)},
                      role="researcher")
    first = rig.core.collect("facts", limit=1)
    assert len(first["items"]) == 1
    second = rig.core.collect("facts", cursor=first["cursor"])
    assert len(second["items"]) == 1 and second["items"][0]["ref"] != first["items"][0]["ref"]
    rig.core.change_setting("mine_facts", False)
    assert rig.core.collect("facts")["items"] == []


def test_mining_an_article_whose_text_was_deleted_is_refused(make_rig):
    rig = make_rig(settings={"mine_facts": True})
    article = suggested(rig)
    rig.store.set_article(article["id"], text="", sentences=[])
    with pytest.raises(CallError, match="text was deleted"):
        rig.core.call("article.choose", {"id": article["id"], "action": "mine"})
