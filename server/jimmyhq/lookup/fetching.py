"""Reading one page for a model.

``fetch`` is the only way a page is read. It follows the rules in ``safety``
at every step, and a few of its own:

- The request goes to the address ``safety.check`` approved, with the site's
  name in the ``Host`` header and, for https, as the name the certificate
  must match. The name is never resolved a second time.
- Redirects are not followed by the HTTP client. Each one is read here,
  checked like a new URL, and followed at most ``MAX_REDIRECTS`` times.
- Proxy settings in the environment are ignored: a proxy would resolve the
  name itself, somewhere the check cannot see.
- No cookies are kept or sent, and nothing identifies the owner.
- Reading stops at ``lookup.fetch_max_bytes`` (counted after decompression,
  so a small download cannot unpack into a huge one) and the whole fetch at
  ``lookup.fetch_timeout_s``.
- Only text is read: HTML, plain text, JSON, XML and their kin.

Every attempt that reached the network, or was refused before it could,
leaves a ``lookup.fetched`` receipt.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from jimmyhq.core import Core
from jimmyhq.lookup import pages, receipts, safety
from jimmyhq.lookup.errors import WebError
from jimmyhq.models.validate import CALLER

MAX_REDIRECTS = 5
_REDIRECTS = (301, 302, 303, 307, 308)
_ACCEPT = {
    "markdown": "text/html, text/markdown;q=0.9, text/plain;q=0.8, application/json;q=0.7, "
                "*/*;q=0.1",
    "text": "text/plain, text/html;q=0.9, application/json;q=0.7, */*;q=0.1",
    "html": "text/html, application/xhtml+xml;q=0.9, */*;q=0.1",
}
OFF = "Web lookup is switched off. Turn it on under Settings, Web lookup."


@dataclass(frozen=True)
class Page:
    url: str  # as asked for
    final_url: str  # where it was read from, after any redirects
    title: str
    text: str
    truncated: bool  # the page was larger than the size limit, and was cut there
    bytes: int
    content_type: str
    links: tuple[str, ...] = ()  # the web addresses an HTML page links to

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "final_url": self.final_url, "title": self.title,
                "text": self.text, "truncated": self.truncated, "bytes": self.bytes,
                "content_type": self.content_type}


class Lookup:
    """This process's web lookup: where its requests go out. Tests replace
    ``transport`` and ``resolver``; nothing else is kept between calls."""

    def __init__(self, core: Core):
        self.core = core
        self.transport: httpx.AsyncBaseTransport | None = None
        self.resolver: safety.Resolver = safety.system_resolver

    def client(self, timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 10.0)))


def service(core: Core) -> Lookup:
    return core.service("lookup", Lookup)


def check_call(core: Core, actor: Any, caller: Any, *, owner_asked: bool = False) -> None:
    if not isinstance(actor, str) or not actor:
        raise WebError("bad_request", "A web lookup needs an actor.")
    if not isinstance(caller, str) or not CALLER.match(caller) or len(caller) > 100:
        raise WebError("bad_request", "A web lookup needs a caller: a dotted lower-case "
                                      "name such as chat.reply.")
    if not owner_asked and not core.settings.get("lookup.enabled"):
        raise WebError("disabled", OFF)


def _transport_error(exc: Exception, host: str) -> WebError:
    if isinstance(exc, httpx.TimeoutException):
        return WebError("timeout", f"{host} took too long to answer. Try again, or try "
                                   "another page.")
    if isinstance(exc, httpx.ConnectError):
        return WebError("unreachable", f"{host} could not be reached. It may be down, or "
                                       "its certificate may not be valid.")
    return WebError("unreachable", f"Reading from {host} failed part-way. Try again.")


async def _get(client: httpx.AsyncClient, checked: safety.Checked, headers: dict[str, str],
               max_bytes: int) -> tuple[httpx.Response, bytes, bool]:
    """One request to the checked address. The body is read only for an
    answer that is a page, and only up to ``max_bytes``."""
    extensions = {"sni_hostname": checked.host} if checked.scheme == "https" else {}
    request = client.build_request(
        "GET", checked.connect_url, headers=headers | {"Host": checked.host_header},
        extensions=extensions)
    response = await client.send(request, stream=True)
    try:
        if response.status_code in _REDIRECTS or response.status_code >= 400:
            return response, b"", False
        kind, _ = pages.media_type(response.headers.get("content-type"))
        if not pages.is_text(kind):
            return response, b"", False
        body = bytearray()
        truncated = False
        async for piece in response.aiter_bytes():
            body += piece
            if len(body) > max_bytes:
                del body[max_bytes:]
                truncated = True
                break
        return response, bytes(body), truncated
    finally:
        await response.aclose()


async def fetch(core: Core, url: str, *, actor: str, caller: str, private: bool = False,
                format: str = "markdown", cause: str | None = None,  # noqa: A002
                root: str | None = None) -> Page:
    """Read one public web page and return it as text.

    ``format`` is "markdown" (the default), "text" or "html". ``private``
    keeps the address out of the receipt, which then carries a keyed hash.
    ``cause`` and ``root`` are the receipt's Tape lineage. Raises ``WebError``
    with a sentence that says what to do.
    """
    check_call(core, actor, caller)
    if format not in pages.FORMATS:
        raise WebError("bad_request", f"format must be one of: {', '.join(pages.FORMATS)}.")
    get = core.settings.get
    lookup = service(core)
    started = time.monotonic()
    facts: dict[str, Any] = {"status": None, "content_type": None, "bytes": 0, "chars": 0,
                             "truncated": False, "hops": 0}
    asked = url if isinstance(url, str) else ""
    failed: str | None = "stopped"  # until it is known to have ended some other way
    try:
        async with asyncio.timeout(get("lookup.fetch_timeout_s")):
            page = await _fetch(core, lookup, asked, format, facts)
        failed = None
        return page
    except WebError as exc:
        failed = exc.kind
        raise
    except TimeoutError:
        failed = "timeout"
        raise WebError("timeout", "The page took too long to arrive. Try again, or try "
                                  "another page.") from None
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        error = _transport_error(exc, "The site")
        failed = error.kind
        raise error from None
    finally:
        said, hashed = receipts.said(core, asked, private)
        host = None
        if not private:
            try:
                host = safety.parse(asked).host
            except WebError:
                host = None
        receipts.print_receipt(core, "lookup.fetched", {
            "caller": caller, "ok": failed is None, "error_kind": failed, **facts,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "private": bool(private), "host": host, "url": said, "url_hash": hashed,
        }, actor=actor, cause=cause, root=root)


async def _fetch(core: Core, lookup: Lookup, url: str, fmt: str, facts: dict[str, Any]
                 ) -> Page:
    get = core.settings.get
    max_bytes = get("lookup.fetch_max_bytes")
    headers = {"User-Agent": get("lookup.user_agent").strip() or "JimmyHQ",
               "Accept": _ACCEPT[fmt], "Accept-Language": "en;q=0.9, *;q=0.5"}
    current = url
    async with lookup.client(get("lookup.fetch_timeout_s")) as client:
        for _ in range(MAX_REDIRECTS + 1):
            # Checked again on every hop: a redirect is a new address.
            checked = await safety.check(core, current, lookup.resolver)
            client.cookies.clear()
            try:
                response, body, truncated = await _get(client, checked, headers, max_bytes)
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                raise _transport_error(exc, checked.host) from None
            facts["hops"] += 1
            facts["status"] = response.status_code
            if response.status_code not in _REDIRECTS:
                break
            where = response.headers.get("location")
            if not where:
                raise WebError("http", f"{checked.host} answered with a redirect that "
                                       "leads nowhere.")
            try:
                current = urljoin(checked.url, where.strip())
            except ValueError:
                raise WebError("bad_url", f"{checked.host} redirected to an address that "
                                          "cannot be read.") from None
        else:
            raise WebError("redirects", f"The page redirected more than {MAX_REDIRECTS} "
                                        "times, so it was given up on.")
    status = response.status_code
    if status >= 400:
        what = {401: "wants a sign-in", 403: "refused to show the page",
                404: "has no page at that address", 429: "is rate limiting requests"}.get(
                    status, "answered with an error")
        raise WebError("http", f"{checked.host} {what} (HTTP {status}). Try another page.")
    content_type = response.headers.get("content-type")
    kind, _ = pages.media_type(content_type)
    facts["content_type"] = kind[:100] or None
    if not pages.is_text(kind):
        raise WebError("not_text", f"That address holds a file of type {kind[:60]}, not a "
                                   "page of text, so it was not read.")
    facts["bytes"], facts["truncated"] = len(body), truncated
    title, text, links = await asyncio.to_thread(pages.read, body, content_type, fmt,
                                                 checked.url)
    facts["chars"] = len(text)
    return Page(url=url, final_url=checked.url, title=title, text=text, truncated=truncated,
                bytes=len(body), content_type=kind, links=links)
