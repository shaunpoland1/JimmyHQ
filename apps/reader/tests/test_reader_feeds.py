"""Reader and other apps: what it offers is its feed ``articles``, and what Posts says went
out marks an article "close to something already posted"."""

from __future__ import annotations

import json

import pytest
from reader import kinds, posted
from reader_rig import APP_ROOT, BEES, BEES_TEXT, SOIL, SOIL_TEXT


def found(rig, *pages):
    rig.topic(outcomes=["list"])
    rig.results("soil water mulch", *pages)
    rig.pick(*range(1, len(pages) + 1))
    rig.search()
    rig.advance()


def went_out(rig, post: int = 1, *, url: str = "", title: str = "", opening: str = "") -> int:
    return rig.core.offer("posts.posted", {"post": post, "page": 1, "title": title,
                                           "source_url": url or None, "opening": opening})


@pytest.fixture
def reading(make_rig):
    return make_rig(reads_posts=True)


# --- what Reader offers -----------------------------------------------------------------------


def test_readers_feed_carries_the_kind_reader_prints_an_offer_as():
    manifest = json.loads((APP_ROOT / "jimmyhq.app.json").read_text(encoding="utf-8"))
    feeds = {f["name"]: f["kind"] for f in manifest["feeds"]}
    assert feeds == {"articles": "article.offered", "withdrawn": "offer.withdrawn"}
    assert "feed:posts.posted" in manifest["permissions"]


def test_an_offer_carries_the_address_and_why_and_never_the_articles_text(rig):
    found(rig, (SOIL, SOIL_TEXT))
    [article] = rig.articles("new")
    rig.core.call("article.choose", {"id": article["id"], "action": "send"})
    [printed] = [e for e in rig.core.events if e["kind"] == "app.reader.article.offered"]
    assert set(printed["payload"]) == set(kinds.OFFER_EXAMPLE)
    assert "41% more water" not in json.dumps(printed["payload"])


# --- close to something already posted --------------------------------------------------------


def test_an_article_already_posted_is_marked_in_reader(reading):
    rig = reading
    found(rig, (SOIL, SOIL_TEXT), (BEES, BEES_TEXT))
    went_out(rig, 7, url=SOIL, title="Wood chips keep beds wet")
    rig.advance()
    rows = {a["url"]: a for a in rig.core.call("articles.list")}
    assert rows[SOIL]["posted_close"].startswith("Close to something already posted: post 7")
    assert rows[BEES]["posted_close"] == ""


def test_an_article_close_in_words_to_a_post_is_marked_with_how_close(reading):
    rig = reading
    found(rig, (SOIL, SOIL_TEXT))
    went_out(rig, 3, title="Soil that holds water through a dry summer",
             opening="A three-year field trial in the valley found that beds mulched with "
                     "wood chips held 41% more water in August than bare beds.")
    rig.advance()
    [row] = rig.core.call("articles.list")
    assert "post 3" in row["posted_close"] and "by shared words" in row["posted_close"]


def test_a_post_the_owner_had_erased_marks_nothing(reading):
    rig = reading
    found(rig, (SOIL, SOIL_TEXT))
    seq = went_out(rig, 7, url=SOIL)
    rig.core.erase_item("posts.posted", seq)
    rig.advance()
    [row] = rig.core.call("articles.list")
    assert row["posted_close"] == ""


def test_an_older_posted_item_with_ids_only_is_left_out():
    assert posted.item_row({"post": 1, "page": 1}) is None
    assert posted.item_row("not an object") is None
    row = posted.item_row({"post": 2, "page": 1, "title": "t" * 900, "opening": "x"})
    assert len(row["title"]) == posted.MAX_TITLE


def test_without_posts_reader_marks_nothing_and_says_so_quietly(reading):
    rig = reading
    found(rig, (SOIL, SOIL_TEXT))
    rig.advance()
    said = rig.core.call("overview")["posted_feed"]
    assert said["reads"] and not said["ok"] and "is not installed" in said["said"]
    assert rig.core.call("articles.list")[0]["posted_close"] == ""


def test_without_the_grant_reader_never_asks_for_posts_feed(rig):
    found(rig, (SOIL, SOIL_TEXT))
    went_out(rig, 7, url=SOIL)
    rig.advance()
    assert rig.core.feed_reads == []
    assert rig.core.call("overview")["posted_feed"]["reads"] is False
