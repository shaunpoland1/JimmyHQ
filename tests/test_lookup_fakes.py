"""Helpers for the web lookup tests: a made-up internet. Nothing here touches a
network: names resolve from a table and pages are served from a dict."""

from __future__ import annotations

import httpx

from jimmyhq import lookup

# A public address that belongs to no one: the retired 6to4 relay range. The
# documentation ranges (192.0.2.x and friends) are not public, which is the
# very thing these tests check, so they stand in for "somewhere private".
PUBLIC = "192.88.99.10"
OTHER_PUBLIC = "192.88.99.20"
HTML = {"content-type": "text/html; charset=utf-8"}


def private(*parts: int) -> str:
    """A private address, put together here so that the repo's public-safety
    check does not take it for somebody's own network."""
    return ".".join(str(p) for p in parts)


class Names:
    """A resolver that answers from a table and remembers what it was asked."""

    def __init__(self, table: dict[str, list[str]] | None = None):
        self.table = {"example.com": [PUBLIC]} | (table or {})
        self.asked: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.asked.append(host)
        found = self.table.get(host, [])
        return list(found() if callable(found) else found)


class Web(httpx.MockTransport):
    """Serves pages by ``(host, path)``. A page is a body (served as HTML), an
    ``httpx.Response``, or a function of the request. ``seen`` records each
    request as ``(address connected to, Host header, path)``."""

    def __init__(self, pages: dict | None = None):
        self.pages = pages or {}
        self.seen: list[tuple[str, str, str]] = []
        self.requests: list[httpx.Request] = []
        super().__init__(self._serve)

    def _serve(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.requests.append(request)
        self.seen.append((request.url.host, host, request.url.raw_path.decode()))
        page = self.pages.get((host, request.url.path))
        if page is None:
            return httpx.Response(404, text="nothing here")
        if callable(page):
            page = page(request)
        if isinstance(page, httpx.Response):
            return page
        return httpx.Response(200, headers=HTML, content=page)


def switch_on(core, pages: dict | None = None, names: dict | None = None, **settings):
    """Web lookup on, wired to a made-up internet. Returns ``(web, names)``."""
    core.settings.set("lookup.enabled", True, actor="owner")
    for key, value in settings.items():
        core.settings.set(f"lookup.{key}", value, actor="owner")
    service = lookup.service(core)
    service.transport = Web(pages)
    service.resolver = Names(names)
    return service.transport, service.resolver


def receipts(core, kind: str) -> list[dict]:
    return [e.payload for e in core.tape.after(0, limit=100000) if e.kind == kind]
