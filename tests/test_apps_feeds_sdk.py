"""The SDK's feeds: ``app.feeds.read`` against its own fake core, as an app author tests,
and against the real app API, where a producer's program prints and a reader's reads."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, CallError, FeedPage, Payload  # noqa: E402
from jimmyhq_app._transport import HttpTransport  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import client_for, install_full  # noqa: E402
from jimmyhq.apps import install  # noqa: E402

ARTICLES = [{"name": "articles", "kind": "article.offered", "label": "Articles",
             "description": "Articles you chose to offer."}]


@pytest.fixture(autouse=True)
def _kinds_go_with_the_test():
    """Tests share one process on Windows: the kinds these apps declare go with them."""
    from jimmyhq.tape import registry as kinds_registry

    def apps_own(key) -> bool:  # what a core module declares on first import stays
        return (key[0] if isinstance(key, tuple) else key).startswith("app.")

    tables = (kinds_registry._KINDS, kinds_registry._OWNERS, kinds_registry._EXAMPLES,
              kinds_registry._SCHEMA_EXAMPLES)
    kept = [{k: v for k, v in t.items() if apps_own(k)} for t in tables]
    backed = {k for k in kinds_registry._SCHEMA_BACKED if apps_own(k)}
    yield
    for table, was in zip(tables, kept, strict=True):
        for key in [k for k in table if apps_own(k)]:
            del table[key]
        table.update(was)
    kinds_registry._SCHEMA_BACKED.difference_update(
        {k for k in kinds_registry._SCHEMA_BACKED if apps_own(k)} - backed)


def test_the_fake_core_plays_another_apps_feed_and_holds_the_reader_to_its_grant(tmp_path):
    core = FakeCore("writer", data_dir=tmp_path, permissions=["feed:reader.articles"])
    app = App(transport=core)
    first = core.offer("reader.articles", {"url": "https://news.example.com/1"})
    page = app.feeds.read("reader", "articles")
    assert isinstance(page, FeedPage) and [i.seq for i in page.items] == [first]
    assert page.items[0].item == {"url": "https://news.example.com/1"}
    assert app.feeds.read("reader", "articles", after=page.next).items == []
    core.offer("other.articles", {"url": "https://news.example.com/2"})
    with pytest.raises(CallError) as refused:
        app.feeds.read("other", "articles")
    assert refused.value.code == "not_allowed"


@pytest.mark.parametrize("how, code, words", [
    ("remove_app", "not_found", "was removed"),
    ("switch_off", "unavailable", "switched off"),
])
def test_the_fake_core_says_why_a_feed_cannot_be_read(tmp_path, how, code, words):
    core = FakeCore("writer", data_dir=tmp_path)
    app = App(transport=core)
    core.offer("reader.articles", {"url": "https://news.example.com/1"})
    getattr(core, how)("reader")
    with pytest.raises(CallError) as refused:
        app.feeds.read("reader", "articles")
    assert refused.value.code == code and words in refused.value.message


def test_the_fake_core_hands_back_an_erased_item_as_erased(tmp_path):
    core = FakeCore("writer", data_dir=tmp_path)
    app = App(transport=core)
    seq = core.offer("reader.articles", {"url": "https://news.example.com/1"})
    core.erase_item("reader.articles", seq)
    [item] = app.feeds.read("reader", "articles").items
    assert item.erased and item.item is None


def test_the_fake_core_says_when_a_producer_no_longer_offers_a_feed(tmp_path):
    core = FakeCore("writer", data_dir=tmp_path)
    app = App(transport=core)
    core.stop_offering("reader.articles")
    with pytest.raises(CallError) as refused:
        app.feeds.read("reader", "articles")
    assert "no longer offers" in refused.value.message


class Offered(Payload):
    url: str


def test_a_producers_program_prints_and_a_readers_program_reads_through_the_real_api(
        core, tmp_path):
    install_full(core, tmp_path, "seeker", feeds=ARTICLES)
    install_full(core, tmp_path, "writer", permissions=["feed:seeker.articles"])
    with client_for(core) as web:
        seeker = App(transport=HttpTransport(client=web,
                                             token=core.auth.mint_app_token("seeker")),
                     app_id="seeker", data_dir=tmp_path / "f")
        seeker.kind("article.offered", example={"url": "https://news.example.com/a"})(Offered)
        seeker.tape.print("article.offered", {"url": "https://news.example.com/1"})
        writer = App(transport=HttpTransport(client=web,
                                             token=core.auth.mint_app_token("writer")),
                     app_id="writer", data_dir=tmp_path / "w")
        page = writer.feeds.read("seeker", "articles")
        assert [i.item for i in page.items] == [{"url": "https://news.example.com/1"}]
        install.remove(core, "seeker")
        with pytest.raises(CallError) as refused:
            writer.feeds.read("seeker", "articles", after=page.next)
        assert "was removed" in refused.value.message
