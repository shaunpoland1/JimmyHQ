"""Send on can be taken back: the offer leaves Reader's list, the record says so in the
kind other apps can read, and a later choice never leaves an offer standing unsaid."""

from __future__ import annotations

import sqlite3

import pytest
from jimmyhq_app import CallError
from reader import articles, kinds
from reader.store import Store
from reader_rig import BEES, BEES_TEXT, SOIL, SOIL_TEXT, Clock


def found(rig, *pages, outcomes=("list",)):
    rig.topic(outcomes=list(outcomes))
    rig.results("soil water mulch", *pages)
    rig.pick(*range(1, len(pages) + 1))
    rig.search()
    rig.advance()


def printed(rig, kind: str) -> list[dict]:
    return [e for e in rig.core.events if e["kind"] == f"app.reader.{kind}"]


def sent_on(rig) -> dict:
    found(rig, (SOIL, SOIL_TEXT))
    [article] = rig.articles("new")
    rig.core.call("article.choose", {"id": article["id"], "action": "send"})
    return article


def test_an_offer_taken_back_leaves_the_list_and_the_article_waits_in_triage_again(rig):
    article = sent_on(rig)
    assert len(rig.core.call("offered.list")) == 1
    row = rig.core.call("offer.withdraw", {"id": article["id"]})
    assert row["state"] == "new" and row["offered"] == 0 and row["back_in_triage"] is True
    assert rig.core.call("offered.list") == []
    overview = rig.core.call("overview")
    assert overview["offered"] == 0
    assert [a["id"] for a in overview["triage"]] == [article["id"]]


def test_taking_an_offer_back_is_printed_with_the_offers_own_number_and_address(rig):
    article = sent_on(rig)
    [offered] = printed(rig, "article.offered")
    rig.clock.advance(5_000)
    rig.core.call("offer.withdraw", {"id": article["id"]})
    [withdrawn] = printed(rig, "offer.withdrawn")
    assert set(withdrawn["payload"]) == set(kinds.WITHDRAWN_EXAMPLE)
    assert withdrawn["payload"]["offer"] == offered["payload"]["offer"]
    assert withdrawn["payload"]["url"] == offered["payload"]["url"] == SOIL
    assert withdrawn["payload"]["when_ms"] == rig.clock.now
    assert len(printed(rig, "article.offered")) == 1  # what was printed is not rewritten


def test_taking_back_what_is_not_offered_changes_nothing_and_says_so(rig):
    found(rig, (SOIL, SOIL_TEXT))
    [article] = rig.articles("new")
    with pytest.raises(CallError) as refused:
        rig.core.call("offer.withdraw", {"id": article["id"]})
    assert "Nothing was changed" in refused.value.message
    assert printed(rig, "offer.withdrawn") == []
    assert rig.store.article(article["id"])["state"] == "new"


def test_an_offer_cannot_be_taken_back_twice(rig):
    article = sent_on(rig)
    rig.core.call("offer.withdraw", {"id": article["id"]})
    with pytest.raises(CallError):
        rig.core.call("offer.withdraw", {"id": article["id"]})
    assert len(printed(rig, "offer.withdrawn")) == 1


@pytest.mark.parametrize("action", ["skip", "keep", "later"])
def test_another_choice_on_an_article_the_owner_sent_on_takes_the_offer_back(rig, action):
    article = sent_on(rig)
    row = rig.core.call("article.choose", {"id": article["id"], "action": action})
    assert row["offer_change"] == "withdrawn" and row["offered"] == 0
    assert row["state"] == articles.STATE_OF[action]
    assert rig.core.call("offered.list") == []
    assert len(printed(rig, "offer.withdrawn")) == 1


def test_a_choice_on_an_article_its_topic_offers_leaves_the_offer_and_says_it_stays(rig):
    found(rig, (SOIL, SOIL_TEXT), outcomes=("list", "offer"))
    [article] = rig.articles("new")
    assert article["offered"] == 1
    row = rig.core.call("article.choose", {"id": article["id"], "action": "skip"})
    assert row["offer_change"] == "stays" and row["offered"] == 1
    assert len(rig.core.call("offered.list")) == 1
    assert printed(rig, "offer.withdrawn") == []


def test_an_offer_its_topic_made_can_still_be_taken_back_and_the_choice_is_kept(rig):
    found(rig, (SOIL, SOIL_TEXT), outcomes=("list", "offer"))
    [article] = rig.articles("new")
    rig.core.call("article.choose", {"id": article["id"], "action": "keep"})
    row = rig.core.call("offer.withdraw", {"id": article["id"]})
    assert row["state"] == "kept" and row["back_in_triage"] is False and row["offered"] == 0
    assert rig.core.call("offered.list") == []


def test_a_choice_on_an_article_never_offered_says_nothing_about_offers(rig):
    found(rig, (SOIL, SOIL_TEXT))
    [article] = rig.articles("new")
    row = rig.core.call("article.choose", {"id": article["id"], "action": "keep"})
    assert row["offer_change"] == ""


def test_sending_on_again_after_taking_back_offers_it_afresh_under_the_same_number(rig):
    article = sent_on(rig)
    rig.core.call("offer.withdraw", {"id": article["id"]})
    rig.clock.advance(60_000)
    rig.core.call("article.choose", {"id": article["id"], "action": "send"})
    first, second = printed(rig, "article.offered")
    assert second["payload"]["offer"] == first["payload"]["offer"]
    assert second["payload"]["when_ms"] == rig.clock.now > first["payload"]["when_ms"]
    [listed] = rig.core.call("offered.list")
    assert listed["article"] == article["id"]
    assert rig.store.article(article["id"])["offered"] == 1


def test_taking_one_offer_back_leaves_the_others(rig):
    found(rig, (SOIL, SOIL_TEXT), (BEES, BEES_TEXT))
    first, second = rig.articles("new")
    for one in (first, second):
        rig.core.call("article.choose", {"id": one["id"], "action": "send"})
    rig.core.call("offer.withdraw", {"id": first["id"]})
    assert [o["article"] for o in rig.core.call("offered.list")] == [second["id"]]


def test_only_a_changed_mind_about_the_owners_own_send_on_takes_an_offer_back():
    plain, offering = {"outcomes": ["list"]}, {"outcomes": ["list", "offer"]}
    assert articles.choice_takes_back("sent", "skip", plain)
    assert articles.choice_takes_back("sent", "keep", None)
    assert not articles.choice_takes_back("sent", "send", plain)
    assert not articles.choice_takes_back("sent", "skip", offering)
    assert not articles.choice_takes_back("new", "skip", plain)
    assert not articles.choice_takes_back("kept", "skip", plain)


def test_a_database_made_before_offers_could_be_taken_back_still_opens(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE offer (id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL "
                 "UNIQUE, url TEXT NOT NULL, title TEXT NOT NULL, topic TEXT NOT NULL, "
                 "why TEXT NOT NULL, text_ref TEXT NOT NULL, created INTEGER NOT NULL)")
    conn.execute("INSERT INTO offer VALUES (1, 4, 'https://news.example.com/a', 'A', 'T', "
                 "'W', 'reader:article:4', 5)")
    conn.commit()
    store = Store(conn, Clock())
    try:
        assert store.one("SELECT * FROM offer WHERE id=1")["withdrawn"] is None
    finally:
        conn.close()
