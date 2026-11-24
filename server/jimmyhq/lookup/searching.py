"""Searching the web through one of several backends.

A backend is a small class that turns a query into its provider's request and
the answer back into ``Result``s. Which one is used is the setting
``lookup.search_provider``. A hosted backend's key lives in the secret store
under ``lookup.<id>.api_key``: never in a setting, never on the Tape.

Wire formats, each checked against the provider's API reference in November
2026 (see docs/lookup.md for the pages):

- **Exa**: ``POST https://api.exa.ai/search``, ``x-api-key``, body
  ``{"query", "numResults", "type": "auto", "contents": {"highlights": true}}``;
  answers ``{"results": [{"title", "url", "highlights", "summary", "text"}]}``.
- **Tavily**: ``POST https://api.tavily.com/search``, ``Authorization:
  Bearer``, body ``{"query", "max_results", "search_depth": "basic"}``;
  answers ``{"results": [{"title", "url", "content", "raw_content"}]}``.
  432 and 433 mean the plan's limit is used up.
- **Brave**: ``GET https://api.search.brave.com/res/v1/web/search?q=&count=``,
  ``X-Subscription-Token``; answers ``{"web": {"results": [{"title", "url",
  "description"}]}}``, with highlight tags inside the text.
- **SearXNG**: ``GET {your server}/search?q=&format=json``, no key; answers
  ``{"results": [{"title", "url", "content"}]}``. A 403 means the server has
  JSON output switched off.
- **Jina**: ``POST https://s.jina.ai/``, ``Authorization: Bearer``,
  ``Accept: application/json``, ``X-Respond-With: no-content``, body
  ``{"q", "num"}``; answers ``{"data": [{"title", "url", "description",
  "content"}]}``.

Redirects are never followed: a search address is where the key is sent.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from jimmyhq.core import Core
from jimmyhq.lookup import receipts
from jimmyhq.lookup.errors import WebError
from jimmyhq.lookup.fetching import check_call, service
from jimmyhq.lookup.settings import PROVIDERS

MAX_QUERY = 400
MAX_TITLE = 300
MAX_SNIPPET = 700
MAX_TEXT = 4000
MAX_RESULTS = 20
WHERE = "Settings, Web lookup"
NO_PROVIDER = (f"No search provider is chosen. Choose one under {WHERE}, \"Search "
               "provider\". A page can still be read by its address.")

_TAGS = re.compile(r"<[^>]{0,200}>")


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str
    text: str | None = None  # more of the page, when the backend sends it

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet,
                "text": self.text}


def secret_name(provider_id: str) -> str:
    return f"lookup.{provider_id}.api_key"


def _words(value: Any, limit: int) -> str:
    """Text from a provider as one clean line: no markup, no control characters."""
    if isinstance(value, list):
        value = " … ".join(v for v in value if isinstance(v, str))
    if not isinstance(value, str):
        return ""
    value = html.unescape(_TAGS.sub("", value))
    return " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())[:limit]


class Backend:
    """One search provider. Subclasses fill in ``search``."""

    id = ""
    label = ""
    needs_key = True
    address = ""  # where a hosted backend lives

    def __init__(self, key: str | None, timeout_s: float,
                 transport: httpx.AsyncBaseTransport | None = None, base_url: str = ""):
        self.key = key
        self.timeout_s = timeout_s
        self.transport = transport
        self.base_url = (base_url or self.address).rstrip("/")

    async def search(self, query: str, limit: int) -> list[Result]:
        raise NotImplementedError

    # --- shared -----------------------------------------------------------------

    def error(self, status: int, response: httpx.Response) -> WebError:
        if status in (401, 403):
            return WebError("auth", f"{self.label} refused the key. Check the key saved "
                                    f"for it under {WHERE}.")
        if status == 402:
            return WebError("billing", f"{self.label} says the account cannot be charged. "
                                       "Check billing with the provider.")
        if status == 429:
            wait = response.headers.get("retry-after", "").strip()
            after = f" It asks for {wait} seconds." if wait.isdigit() else ""
            return WebError("rate_limited", f"{self.label} is rate limiting searches.{after} "
                                            "Wait a little and try again.")
        if 300 <= status < 400:
            return WebError("unavailable", f"{self.label} answered with a redirect, which "
                                           "is not followed. Check the address.")
        if status >= 500:
            return WebError("unavailable", f"{self.label} is having trouble right now. Try "
                                           "again shortly.")
        return WebError("bad_request", f"{self.label} rejected the search (HTTP {status}). "
                                       "Try different words.")

    async def ask(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                  params: dict[str, Any] | None = None, body: dict[str, Any] | None = None
                  ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                    transport=self.transport, follow_redirects=False,
                    timeout=httpx.Timeout(self.timeout_s, connect=min(self.timeout_s, 10.0)),
                    headers={"accept": "application/json"}) as client:
                response = await client.request(method, url, headers=headers, params=params,
                                                json=body)
        except httpx.TimeoutException:
            raise WebError("timeout", f"{self.label} took too long to answer. Try again."
                           ) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise WebError("unreachable", f"{self.label} could not be reached. Check that "
                                          "it is running and that its address is right."
                           ) from None
        if response.status_code != 200:
            raise self.error(response.status_code, response)
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise WebError("bad_reply", f"{self.label} sent back something that could not "
                                        "be read.")
        return data

    def results(self, items: Any, limit: int, snippet: tuple[str, ...],
                text: str | None = None) -> list[Result]:
        """Results from a provider's list: the first field in ``snippet`` that
        holds anything is the snippet. Anything without a web address is left out."""
        if not isinstance(items, list):
            raise WebError("bad_reply", f"{self.label} sent back something that could not "
                                        "be read.")
        out: list[Result] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if (not isinstance(url, str) or not url.startswith(("http://", "https://"))
                    or len(url) > 2000 or any(ord(ch) <= 32 for ch in url)):
                continue
            short = next((w for w in (_words(item.get(name), MAX_SNIPPET) for name in snippet)
                          if w), "")
            more = _words(item.get(text), MAX_TEXT) if text else ""
            out.append(Result(_words(item.get("title"), MAX_TITLE) or url, url, short,
                              more or None))
            if len(out) >= limit:
                break
        return out


class Exa(Backend):
    id, label, address = "exa", "Exa", "https://api.exa.ai"

    async def search(self, query: str, limit: int) -> list[Result]:
        data = await self.ask("POST", f"{self.base_url}/search",
                              headers={"x-api-key": self.key or ""},
                              body={"query": query, "numResults": limit, "type": "auto",
                                    "contents": {"highlights": True}})
        return self.results(data.get("results"), limit, ("highlights", "summary", "text"))


class Tavily(Backend):
    id, label, address = "tavily", "Tavily", "https://api.tavily.com"

    def error(self, status: int, response: httpx.Response) -> WebError:
        if status in (432, 433):
            return WebError("billing", "Tavily says this key's plan has no searches left. "
                                       "Check the plan with Tavily, or choose another "
                                       f"provider under {WHERE}.")
        return super().error(status, response)

    async def search(self, query: str, limit: int) -> list[Result]:
        data = await self.ask("POST", f"{self.base_url}/search",
                              headers={"authorization": f"Bearer {self.key}"},
                              body={"query": query, "max_results": limit,
                                    "search_depth": "basic"})
        return self.results(data.get("results"), limit, ("content",), "raw_content")


class Brave(Backend):
    id, label, address = "brave", "Brave Search", "https://api.search.brave.com"

    async def search(self, query: str, limit: int) -> list[Result]:
        data = await self.ask("GET", f"{self.base_url}/res/v1/web/search",
                              headers={"x-subscription-token": self.key or ""},
                              params={"q": query, "count": limit})
        web = data.get("web")
        if web is None:
            return []  # a search with no web results has no "web" at all
        return self.results(web.get("results") if isinstance(web, dict) else None, limit,
                            ("description",))


class SearXNG(Backend):
    id, label, needs_key = "searxng", "SearXNG", False

    def error(self, status: int, response: httpx.Response) -> WebError:
        if status == 403:
            return WebError("not_configured",
                            "Your SearXNG server refused to answer in JSON. Add \"json\" "
                            "under \"search: formats:\" in its settings.yml and restart it.")
        if status == 429:
            return WebError("rate_limited", "Your SearXNG server is rate limiting searches. "
                                            "Wait a little, or relax its limiter.")
        return super().error(status, response)

    async def search(self, query: str, limit: int) -> list[Result]:
        if not self.base_url:
            raise WebError("not_configured", "SearXNG needs your server's address. Add it "
                                             f"under {WHERE}, \"SearXNG address\".")
        data = await self.ask("GET", f"{self.base_url}/search",
                              params={"q": query, "format": "json"})
        return self.results(data.get("results"), limit, ("content",))


class Jina(Backend):
    id, label, address = "jina", "Jina", "https://s.jina.ai"

    async def search(self, query: str, limit: int) -> list[Result]:
        data = await self.ask("POST", f"{self.base_url}/",
                              headers={"authorization": f"Bearer {self.key}",
                                       "x-respond-with": "no-content"},
                              body={"q": query, "num": limit})
        return self.results(data.get("data"), limit, ("description",), "content")


BACKENDS: dict[str, type[Backend]] = {b.id: b for b in (Exa, Tavily, Brave, SearXNG, Jina)}
assert set(BACKENDS) == {p for p, _ in PROVIDERS} - {"none"}  # noqa: S101 - the setting's choices


def backend(core: Core) -> Backend:
    """The chosen backend, ready to ask, or a refusal that says what to set up."""
    chosen = core.settings.get("lookup.search_provider")
    kind = BACKENDS.get(chosen)
    if kind is None:
        raise WebError("not_configured", NO_PROVIDER)
    key = core.secrets.get(secret_name(kind.id)) if kind.needs_key else None
    if kind.needs_key and not key:
        raise WebError("no_key", f"{kind.label} needs an API key before it can search. Add "
                                 f"it under {WHERE}.")
    base = core.settings.get("lookup.searxng_url") if kind is SearXNG else ""
    return kind(key, core.settings.get("lookup.fetch_timeout_s"), service(core).transport,
                base)


async def search(core: Core, query: str, *, actor: str, caller: str, private: bool = False,
                 limit: int | None = None, cause: str | None = None,
                 root: str | None = None, owner_asked: bool = False) -> list[Result]:
    """Search the web with the provider the owner chose.

    ``limit`` defaults to ``lookup.results``. ``private`` keeps the query out
    of the receipt, which then carries a keyed hash. ``owner_asked`` is for
    the owner's own "try it" click, which works before web lookup is switched
    on for models. Raises ``WebError`` with a sentence that says what to do.
    """
    check_call(core, actor, caller, owner_asked=owner_asked)
    if not isinstance(query, str) or not query.strip():
        raise WebError("bad_request", "A search needs some words to search for.")
    query = " ".join(query.split())
    if len(query) > MAX_QUERY:
        raise WebError("bad_request", f"That search is longer than {MAX_QUERY} characters. "
                                      "Search for fewer words.")
    if limit is None:
        limit = core.settings.get("lookup.results")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise WebError("bad_request", f"limit must be a whole number from 1 to {MAX_RESULTS}.")
    chosen = core.settings.get("lookup.search_provider")
    started = time.monotonic()
    found: list[Result] = []
    failed: str | None = "stopped"  # until it is known to have ended some other way
    try:
        found = await backend(core).search(query, limit)
        failed = None
        return found
    except WebError as exc:
        failed = exc.kind
        raise
    finally:
        said, hashed = receipts.said(core, query, private)
        receipts.print_receipt(core, "lookup.searched", {
            "caller": caller, "provider": chosen, "ok": failed is None, "error_kind": failed,
            "results": len(found),
            "chars": sum(len(r.title) + len(r.snippet) + len(r.text or "") for r in found),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "private": bool(private), "query": said, "query_hash": hashed,
        }, actor=actor, cause=cause, root=root)
