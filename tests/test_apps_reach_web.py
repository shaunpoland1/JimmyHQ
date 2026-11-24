"""The web for apps (``web.search`` and ``web.read``), from the side of an app that wants
to reach the owner's own network, read while the owner said no, or search past its limits."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jimmyhq import lookup
from jimmyhq.apps import bridge, permissions, quotas, reach
from test_apps_bridge_fakes import WATER, call, events, installs, refused
from test_lookup_fakes import PUBLIC, private, receipts, switch_on

DRAFTS = json.loads((Path(__file__).resolve().parents[1] / "apps" / "examples" / "drafts"
                     / "jimmyhq.app.json").read_text(encoding="utf-8"))
ARTICLE = "https://example.com/article"
LONG = "<html><head><title>Beans</title></head><body><p>" + "beans " * 2000 + "</p></body></html>"


@pytest.fixture
def apps(monkeypatch, tmp_path):
    return installs(monkeypatch, tmp_path)


def searching(core, results=None):
    """Web lookup on, with the owner's own search server answering."""
    asked: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={"results": results if results is not None else [
            {"title": "Broad beans", "url": "https://example.com/beans",
             "content": "Sow in late winter."}]})

    core.settings.set("lookup.enabled", True, actor="owner")
    core.settings.set("lookup.search_provider", "searxng", actor="owner")
    core.settings.set("lookup.searxng_url", "https://search.example.com/", actor="owner")
    lookup.service(core).transport = httpx.MockTransport(answer)
    return asked


# --- permission and the owner's switch ------------------------------------------------------


def test_the_web_permission_says_plainly_that_what_it_searches_for_leaves_this_computer():
    sentence = permissions.BY_ID["web"].sentence
    assert "leave this computer" in sentence and "search" in sentence.lower()
    assert permissions.BY_ID["web"].tiers == permissions.TIERS


@pytest.mark.parametrize("name, args", [("web.search", {"query": "beans"}),
                                        ("web.read", {"url": ARTICLE})])
def test_an_app_without_web_can_neither_search_nor_read(core, apps, name, args):
    apps.add(WATER, granted=["model.ask", "net:example.com"])
    web, _ = switch_on(core, {("example.com", "/article"): LONG})
    asked = searching(core)
    error = refused(core, "water", name, args)
    assert error.code == "not_allowed" and "\"web\"" in error.message
    assert web.seen == [] and asked == []
    assert receipts(core, "lookup.searched") == receipts(core, "lookup.fetched") == []


@pytest.mark.parametrize("name, args", [("web.search", {"query": "beans"}),
                                        ("web.read", {"url": ARTICLE})])
def test_while_the_owner_has_web_lookup_off_an_app_reads_and_searches_nothing(core, apps,
                                                                              name, args):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/article"): LONG})
    asked = searching(core)
    core.settings.set("lookup.enabled", False, actor="owner")
    error = refused(core, "water", name, args)
    assert error.code == "not_allowed" and "switched off" in error.message
    assert "Settings, Web lookup" in error.message
    assert web.seen == [] and asked == []
    assert quotas.used(core, "water", "web_reads_per_day") == 0
    assert quotas.used(core, "water", "web_searches_per_day") == 0


def test_check_lookup_on_reads_the_owners_switch(core):
    with pytest.raises(bridge.BridgeError):
        reach.check_lookup_on(core)
    core.settings.set("lookup.enabled", True, actor="owner")
    reach.check_lookup_on(core)


# --- searching ------------------------------------------------------------------------------


def test_a_search_goes_through_web_lookup_as_the_app_and_leaves_its_receipt(core, apps):
    apps.add(WATER, granted=["web"])
    asked = searching(core)
    got = call(core, "water", "web.search", {"query": "when do broad beans go in"})
    assert got == {"results": [{"title": "Broad beans", "url": "https://example.com/beans",
                                "snippet": "Sow in late winter.", "text": None}],
                   "untrusted": True}
    assert asked[0].url.params["q"] == "when do broad beans go in"
    [receipt] = events(core, "lookup.searched")
    assert receipt.actor == "app:water" and receipt.payload["caller"] == "apps.water"
    assert receipt.payload["query"] == "when do broad beans go in"


def test_a_private_apps_search_leaves_a_hash_not_its_words(core, apps):
    apps.add(DRAFTS, granted=["web"], private=True)
    searching(core)
    call(core, "drafts", "web.search", {"query": "a private matter"})
    [receipt] = receipts(core, "lookup.searched")
    assert receipt["private"] is True and receipt["query"] is None
    assert receipt["query_hash"] and "private matter" not in json.dumps(receipt)


@pytest.mark.parametrize("args", [{"query": ""}, {"query": "  "}, {"query": 5},
                                  {"query": "x" * 401}, {"query": "b", "limit": 0},
                                  {"query": "b", "limit": 7}, {"query": "b", "limit": True},
                                  {"query": "b", "provider": "exa"}])
def test_a_search_asked_wrongly_or_for_more_than_the_owner_allows_is_refused(core, apps,
                                                                            args):
    apps.add(WATER, granted=["web"])
    asked = searching(core)
    assert refused(core, "water", "web.search", args).code == "invalid"
    assert asked == []


def test_web_searches_are_capped_a_day(core, apps):
    apps.add(WATER, granted=["web"])
    asked = searching(core)
    core.settings.set("apps.quota.web_searches_per_day", 2, actor="owner")
    call(core, "water", "web.search", {"query": "one"})
    call(core, "water", "web.search", {"query": "two"})
    error = refused(core, "water", "web.search", {"query": "three"})
    assert error.code == "quota" and "all 2 web searches" in error.message
    assert len(asked) == 2


def test_with_no_search_provider_the_app_is_told_what_to_ask_the_owner(core, apps):
    apps.add(WATER, granted=["web"])
    core.settings.set("lookup.enabled", True, actor="owner")
    error = refused(core, "water", "web.search", {"query": "beans"})
    assert error.code == "unavailable" and "Search provider" in error.message


# --- reading --------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/admin", "http://[::1]/",
    f"http://{private(10, 0, 0, 5)}/", f"http://{private(192, 168, 1, 1)}/",
    "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/",
    "https://inside.example.com/", "file:///etc/hosts", "ftp://example.com/",
])
def test_web_read_of_a_loopback_or_private_address_is_refused_and_nothing_is_read(
        core, apps, url):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, names={"inside.example.com": [private(10, 1, 2, 3)],
                                    "localhost": ["127.0.0.1"]})
    error = refused(core, "water", "web.read", {"url": url})
    assert error.code in ("not_allowed", "invalid") and "Nothing was read" in error.message
    assert web.seen == []


def test_a_redirect_to_a_private_address_is_refused_too(core, apps):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/go"): httpx.Response(
        302, headers={"location": "http://127.0.0.1/secret"})})
    error = refused(core, "water", "web.read", {"url": "https://example.com/go"})
    assert error.code == "not_allowed"
    assert [seen[2] for seen in web.seen] == ["/go"]


def test_a_page_is_read_as_cleaned_text_marked_untrusted_with_its_receipt(core, apps):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/article"): LONG})
    got = call(core, "water", "web.read", {"url": ARTICLE})
    assert got["title"] == "Beans" and got["untrusted"] is True
    assert got["text"].startswith("beans beans") and "<p>" not in got["text"]
    assert got["start"] == 0 and got["end"] == len(got["text"]) <= 12000
    [receipt] = events(core, "lookup.fetched")
    assert receipt.actor == "app:water" and receipt.payload["caller"] == "apps.water"
    assert receipt.payload["url"] == ARTICLE and web.seen == [(PUBLIC, "example.com",
                                                               "/article")]


def test_a_long_page_is_read_a_piece_at_a_time_and_fetched_once(core, apps):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/article"): LONG})
    pieces, start = [], 0
    while start is not None:
        got = call(core, "water", "web.read", {"url": ARTICLE, "start": start, "chars": 3000})
        assert len(got["text"]) <= 3000
        pieces.append(got["text"])
        start = got["next"]
    assert len(pieces) == 4 and len("".join(pieces)) == got["total"]
    assert len(web.seen) == 1 and len(events(core, "lookup.fetched")) == 1
    assert quotas.used(core, "water", "web_reads_per_day") == 1


def test_a_kept_page_is_another_apps_to_fetch_for_itself(core, apps):
    apps.add(WATER, granted=["web"])
    apps.add(DRAFTS, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/article"): LONG})
    call(core, "water", "web.read", {"url": ARTICLE})
    call(core, "drafts", "web.read", {"url": ARTICLE})
    assert len(web.seen) == 2


def test_with_keeping_set_to_zero_every_read_fetches_again(core, apps):
    apps.add(WATER, granted=["web"])
    core.settings.set("apps.web_read_keep_minutes", 0, actor="owner")
    web, _ = switch_on(core, {("example.com", "/article"): LONG})
    call(core, "water", "web.read", {"url": ARTICLE})
    call(core, "water", "web.read", {"url": ARTICLE, "start": 100})
    assert len(web.seen) == 2


def test_one_piece_is_never_longer_than_the_owners_characters_of_a_page(core, apps):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/article"): LONG}, page_chars=1000)
    got = call(core, "water", "web.read", {"url": ARTICLE})
    assert len(got["text"]) == 1000 and got["next"] == 1000
    error = refused(core, "water", "web.read", {"url": ARTICLE, "chars": 1001})
    assert error.code == "invalid" and "1000" in error.message


def test_page_window_starts_and_ends_inside_the_text():
    assert reach.page_window("abcdef", None, None, 4) == (0, 4)
    assert reach.page_window("abcdef", 4, None, 4) == (4, 6)
    assert reach.page_window("abcdef", 99, 2, 4) == (6, 6)
    for start, chars in ((-1, 2), (0, 0), (0, 5), ("1", 2), (True, 2)):
        with pytest.raises(bridge.BridgeError):
            reach.page_window("abcdef", start, chars, 4)


def test_web_reads_are_capped_a_day(core, apps):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/a"): LONG, ("example.com", "/b"): LONG,
                              ("example.com", "/c"): LONG})
    core.settings.set("apps.quota.web_reads_per_day", 2, actor="owner")
    call(core, "water", "web.read", {"url": "https://example.com/a"})
    call(core, "water", "web.read", {"url": "https://example.com/b"})
    error = refused(core, "water", "web.read", {"url": "https://example.com/c"})
    assert error.code == "quota" and "all 2 web pages read" in error.message
    assert len(web.seen) == 2


def test_a_private_apps_read_leaves_a_hash_not_the_address(core, apps):
    apps.add(DRAFTS, granted=["web"], private=True)
    switch_on(core, {("example.com", "/article"): LONG})
    call(core, "drafts", "web.read", {"url": ARTICLE})
    [receipt] = receipts(core, "lookup.fetched")
    assert receipt["url"] is None and receipt["host"] is None and receipt["url_hash"]


@pytest.mark.parametrize("args", [{"url": ""}, {"url": 5}, {"url": "x" * 2001},
                                  {"url": ARTICLE, "start": -1},
                                  {"url": ARTICLE, "format": "html"},
                                  {"url": ARTICLE, "headers": {"Cookie": "a"}}])
def test_a_read_asked_wrongly_is_refused_and_nothing_goes_out(core, apps, args):
    apps.add(WATER, granted=["web"])
    web, _ = switch_on(core, {("example.com", "/article"): LONG})
    assert refused(core, "water", "web.read", args).code == "invalid"
    assert web.seen == []


def test_a_page_that_is_not_text_is_refused_with_a_sentence(core, apps):
    apps.add(WATER, granted=["web"])
    switch_on(core, {("example.com", "/x.zip"): httpx.Response(
        200, headers={"content-type": "application/zip"}, content=b"PK")})
    error = refused(core, "water", "web.read", {"url": "https://example.com/x.zip"})
    assert error.code == "invalid" and "Nothing was read" in error.message
