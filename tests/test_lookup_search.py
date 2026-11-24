"""Searching: each backend's request and how its answer is read, the errors a
person can act on, and the receipt. No search provider is ever contacted."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jimmyhq import lookup
from jimmyhq.lookup import searching
from test_lookup_fakes import receipts

WHO = {"actor": "owner", "caller": "chat.reply"}
KEY = "test-key-0123456789"
QUERY = "when to sow broad beans"
HIT = {"title": "Growing broad beans", "url": "https://example.com/beans"}

# What each provider answers with, in its own shape, for the same two results.
ANSWERS = {
    "exa": {"requestId": "r1", "results": [
        HIT | {"id": "1", "highlights": ["Sow from late winter.", "Hardy to frost."],
               "text": "The whole page."},
        {"title": "Runner beans", "url": "https://example.com/runner", "summary": "Later."}]},
    "tavily": {"query": QUERY, "results": [
        HIT | {"content": "Sow from late winter.", "score": 0.9, "raw_content": "The whole page."},
        {"title": "Runner beans", "url": "https://example.com/runner", "content": "Later."}]},
    "brave": {"type": "search", "web": {"results": [
        HIT | {"description": "Sow from <strong>late winter</strong> &amp; on."},
        {"title": "Runner beans", "url": "https://example.com/runner", "description": "Later."}]}},
    "searxng": {"query": QUERY, "number_of_results": 2, "results": [
        HIT | {"content": "Sow from late winter.", "engine": "example"},
        {"title": "Runner beans", "url": "https://example.com/runner", "content": "Later."}]},
    "jina": {"code": 200, "status": 20000, "data": [
        HIT | {"description": "Sow from late winter.", "content": ""},
        {"title": "Runner beans", "url": "https://example.com/runner", "description": "Later.",
         "content": "The whole page."}]},
}
HOSTED = ("exa", "tavily", "brave", "jina")


class Provider:
    """A fake search provider: records each request and gives one answer."""

    def __init__(self, answer=None, status: int = 200, headers: dict | None = None):
        self.answer, self.status, self.headers = answer, status, headers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        if isinstance(self.answer, str):
            return httpx.Response(self.status, text=self.answer, headers=self.headers)
        return httpx.Response(self.status, json=self.answer, headers=self.headers)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    @property
    def body(self) -> dict:
        return json.loads(self.last.content)


def use(core, provider: str, fake: Provider | None = None, *, key: bool = True) -> Provider:
    fake = fake or Provider(ANSWERS[provider])
    core.settings.set("lookup.enabled", True, actor="owner")
    core.settings.set("lookup.search_provider", provider, actor="owner")
    if provider == "searxng":
        core.settings.set("lookup.searxng_url", "https://search.example.com/", actor="owner")
    elif key:
        core.secrets.set(searching.secret_name(provider), KEY)
    lookup.service(core).transport = httpx.MockTransport(fake)
    return fake


def find(core, query=QUERY, **how):
    return asyncio.run(lookup.search(core, query, **(WHO | how)))


def refused(core, query=QUERY, **how) -> lookup.WebError:
    with pytest.raises(lookup.WebError) as caught:
        find(core, query, **how)
    return caught.value


def test_the_setting_offers_exactly_the_backends_there_are(core):
    from jimmyhq.settings import SettingError  # noqa: PLC0415

    assert core.settings.get("lookup.search_provider") == "none"
    assert set(searching.BACKENDS) == {"exa", "tavily", "brave", "searxng", "jina"}
    with pytest.raises(SettingError):
        core.settings.set("lookup.search_provider", "duckduckgo", actor="owner")


def test_exa_is_asked_the_way_its_reference_says(core):
    fake = use(core, "exa")
    first, second = find(core)
    assert (fake.last.method, str(fake.last.url)) == ("POST", "https://api.exa.ai/search")
    assert fake.last.headers["x-api-key"] == KEY and "authorization" not in fake.last.headers
    assert fake.body == {"query": QUERY, "numResults": 6, "type": "auto",
                         "contents": {"highlights": True}}
    assert first == lookup.Result("Growing broad beans", "https://example.com/beans",
                                  "Sow from late winter. … Hardy to frost.")
    assert second.snippet == "Later."  # no highlights: the summary stands in


def test_tavily_is_asked_the_way_its_reference_says(core):
    fake = use(core, "tavily")
    first, second = find(core, limit=3)
    assert (fake.last.method, str(fake.last.url)) == ("POST", "https://api.tavily.com/search")
    assert fake.last.headers["authorization"] == f"Bearer {KEY}"
    assert fake.body == {"query": QUERY, "max_results": 3, "search_depth": "basic"}
    assert first == lookup.Result("Growing broad beans", "https://example.com/beans",
                                  "Sow from late winter.", "The whole page.")
    assert second.text is None


def test_brave_is_asked_the_way_its_reference_says(core):
    fake = use(core, "brave")
    first, _ = find(core)
    assert fake.last.method == "GET"
    assert str(fake.last.url.copy_with(query=None)) == (
        "https://api.search.brave.com/res/v1/web/search")
    assert dict(fake.last.url.params) == {"q": QUERY, "count": "6"}
    assert fake.last.headers["x-subscription-token"] == KEY
    assert fake.last.headers["accept"] == "application/json"
    # Brave marks the matching words with tags: they are taken out.
    assert first.snippet == "Sow from late winter & on."
    use(core, "brave", Provider({"type": "search", "query": {}}))
    assert find(core) == []  # no "web" at all means no results, not a broken answer


def test_searxng_is_asked_at_the_owners_own_server_with_no_key(core):
    fake = use(core, "searxng")
    first, _ = find(core)
    assert fake.last.method == "GET"
    assert str(fake.last.url.copy_with(query=None)) == "https://search.example.com/search"
    assert dict(fake.last.url.params) == {"q": QUERY, "format": "json"}
    assert "authorization" not in fake.last.headers
    assert first == lookup.Result("Growing broad beans", "https://example.com/beans",
                                  "Sow from late winter.")


def test_jina_is_asked_the_way_its_reference_says(core):
    fake = use(core, "jina")
    first, second = find(core)
    assert (fake.last.method, str(fake.last.url)) == ("POST", "https://s.jina.ai/")
    assert fake.last.headers["authorization"] == f"Bearer {KEY}"
    assert fake.last.headers["accept"] == "application/json"
    assert fake.last.headers["x-respond-with"] == "no-content"
    assert fake.body == {"q": QUERY, "num": 6}
    assert first.snippet == "Sow from late winter." and first.text is None
    assert second.text == "The whole page."


@pytest.mark.parametrize("provider", sorted(ANSWERS))
def test_every_backend_gives_the_same_two_results(core, provider):
    use(core, provider)
    found = find(core)
    assert [(r.title, r.url) for r in found] == [
        ("Growing broad beans", "https://example.com/beans"),
        ("Runner beans", "https://example.com/runner")]
    assert all(r.snippet for r in found)
    assert len(find(core, limit=1)) == 1


@pytest.mark.parametrize("provider", HOSTED)
def test_a_provider_with_no_key_says_where_to_add_it_and_is_not_contacted(core, provider):
    fake = use(core, provider, key=False)
    error = refused(core)
    assert error.kind == "no_key" and "Settings, Web lookup" in error.message
    assert searching.BACKENDS[provider].label in error.message and fake.requests == []


def test_no_provider_chosen_says_where_to_choose_one(core):
    core.settings.set("lookup.enabled", True, actor="owner")
    error = refused(core)
    assert error.kind == "not_configured" and "Search provider" in error.message
    assert receipts(core, "lookup.searched")[-1]["error_kind"] == "not_configured"


def test_searxng_without_an_address_says_where_to_add_it(core):
    use(core, "searxng")
    core.settings.set("lookup.searxng_url", "", actor="owner")
    error = refused(core)
    assert error.kind == "not_configured" and "SearXNG address" in error.message


@pytest.mark.parametrize("bad", ["search.example.com", "ftp://search.example.com",
                                 "https://sam:pw@example.com",
                                 "https://search.example.com/?q=x", "https://"])
def test_the_searxng_address_setting_takes_a_plain_web_address(core, bad):
    from jimmyhq.settings import SettingError  # noqa: PLC0415

    with pytest.raises(SettingError, match="SearXNG address"):
        core.settings.set("lookup.searxng_url", bad, actor="owner")


@pytest.mark.parametrize("provider", sorted(ANSWERS))
def test_a_rate_limit_is_said_plainly(core, provider):
    use(core, provider, Provider({"error": "slow down"}, 429, {"retry-after": "30"}))
    error = refused(core)
    assert error.kind == "rate_limited" and "rate limiting" in error.message
    assert error.status == 429 and "slow down" not in error.message
    if provider != "searxng":
        assert "30 seconds" in error.message
    assert receipts(core, "lookup.searched")[-1]["error_kind"] == "rate_limited"


@pytest.mark.parametrize("status, kind, words", [
    (401, "auth", "refused the key"), (403, "auth", "refused the key"),
    (402, "billing", "cannot be charged"), (500, "unavailable", "having trouble"),
    (503, "unavailable", "having trouble"), (400, "bad_request", "rejected the search"),
    (302, "unavailable", "redirect"),
])
def test_other_failures_say_what_to_do(core, status, kind, words):
    fake = use(core, "tavily", Provider({"detail": "the key sk-secret-1234567 is bad"}, status,
                                        {"location": "https://elsewhere.example.com/"}))
    error = refused(core)
    assert error.kind == kind and words in error.message
    assert "sk-secret" not in error.message and len(fake.requests) == 1  # no redirect followed


def test_tavily_says_when_the_plan_has_run_out(core):
    for status in (432, 433):
        use(core, "tavily", Provider({"detail": "limit"}, status))
        error = refused(core)
        assert error.kind == "billing" and "no searches left" in error.message


def test_searxng_with_json_switched_off_says_how_to_switch_it_on(core):
    use(core, "searxng", Provider("<html>Forbidden</html>", 403))
    error = refused(core)
    assert error.kind == "not_configured" and "settings.yml" in error.message


@pytest.mark.parametrize("answer", ["<html>not json</html>", "[1, 2]", '{"results": "none"}',
                                    '{"nothing": true}'])
def test_an_answer_that_cannot_be_read_is_a_plain_failure(core, answer):
    use(core, "tavily", Provider(answer))
    assert refused(core).kind == "bad_reply"


def test_a_provider_that_cannot_be_reached_or_is_slow_says_so(core):
    use(core, "exa", Provider(httpx.ConnectError("refused")))
    assert refused(core).kind == "unreachable"
    use(core, "exa", Provider(httpx.ReadTimeout("slow")))
    assert refused(core).kind == "timeout"


def test_results_without_a_web_address_are_left_out_and_text_is_cleaned(core):
    use(core, "tavily", Provider({"results": [
        {"title": "No address", "content": "x"},
        {"title": "A script", "url": "javascript:alert(1)", "content": "x"},
        {"title": "A file", "url": "file:///etc/passwd", "content": "x"},
        {"title": "Spaced", "url": "https://example.com/a b", "content": "x"},
        "not even a dict",
        {"title": "  Good\n<b>one</b>\x00 ", "url": "https://example.com/ok",
         "content": "a\tb\n\nc " + "z" * 5000, "raw_content": 7},
        {"url": "https://example.com/untitled", "content": None},
    ]}))
    good, untitled = find(core)
    assert (good.title, good.url) == ("Good one", "https://example.com/ok")
    assert good.snippet.startswith("a b c zzz") and len(good.snippet) == searching.MAX_SNIPPET
    assert good.text is None
    assert (untitled.title, untitled.snippet) == ("https://example.com/untitled", "")


@pytest.mark.parametrize("query, how", [("", {}), ("   ", {}), (None, {}), (5, {}),
                                        ("x" * 401, {}), (QUERY, {"limit": 0}),
                                        (QUERY, {"limit": 21}), (QUERY, {"limit": True}),
                                        (QUERY, {"caller": "Bad Caller"})])
def test_a_search_that_makes_no_sense_is_refused_before_anyone_is_asked(core, query, how):
    fake = use(core, "tavily")
    assert refused(core, query, **how).kind == "bad_request"
    assert fake.requests == [] and receipts(core, "lookup.searched") == []


def test_nothing_is_searched_until_the_owner_switches_web_lookup_on(core):
    fake = use(core, "tavily")
    core.settings.set("lookup.enabled", False, actor="owner")
    error = refused(core)
    assert error.kind == "disabled" and fake.requests == []


def test_a_search_leaves_a_receipt_with_the_query_and_no_results(core):
    use(core, "tavily")
    find(core, "  when to   sow broad beans ")
    (event,) = [e for e in core.tape.after(0) if e.kind == "lookup.searched"]
    receipt = event.payload
    assert event.actor == "owner"
    assert receipt == {
        "caller": "chat.reply", "provider": "tavily", "ok": True, "error_kind": None,
        "results": 2, "chars": receipt["chars"], "duration_ms": receipt["duration_ms"],
        "private": False, "query": QUERY, "query_hash": None}
    assert receipt["chars"] > 40
    assert "late winter" not in json.dumps(receipt) and "example.com" not in json.dumps(receipt)


def test_a_private_search_records_a_keyed_hash_and_no_query(core):
    use(core, "tavily")
    find(core, private=True)
    find(core, private=True)
    find(core, "something else entirely", private=True)
    first, second, third = receipts(core, "lookup.searched")
    assert first["private"] is True and first["query"] is None
    assert len(first["query_hash"]) == 64 and first["query_hash"] == second["query_hash"]
    assert third["query_hash"] != first["query_hash"]
    everything = json.dumps([e.payload for e in core.tape.after(0)])
    assert "broad beans" not in everything and "something else" not in everything


def test_a_key_is_never_in_a_setting_a_receipt_or_an_error(core):
    use(core, "tavily", Provider({"detail": f"bad key {KEY}"}, 401))
    error = refused(core)
    find_all = json.dumps([e.payload for e in core.tape.after(0)]) + error.message
    assert KEY not in find_all
    assert KEY not in json.dumps({s: core.settings.get(s) for s in (
        "lookup.search_provider", "lookup.searxng_url")})
