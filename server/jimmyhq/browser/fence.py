"""The network fence: the one place every request an agent's browser makes must pass.

The browser is started with this module's forward proxy as its only way out (see
``engine.flags``: no direct connections, no name lookups of its own, loopback included).
So a page's own address, a redirect, a picture, a script, a frame, a background request
and a web socket are all the same thing here: one request for one host and port, checked
by ``Fence.check`` before a single byte goes anywhere.

The rules, in order, each a named function:

- ``shape``: only ``http`` and ``https``, a readable host, no user name or password.
- ``hard_denied``: the owner's list of sites no browser may reach (``browser.hard_deny``).
- ``outside_allow_list``: for an agent the owner gave an allow-list, everything else.
- ``address``: the lookup module's address rules, unchanged. No loopback, private,
  link-local, tailnet or carrier-grade NAT address; never JimmyHQ's own host and port.
  The name is resolved once, here, and the proxy connects to the address that passed, so
  a name that answers "public" to the check and "private" a moment later gains nothing.

What the fence cannot see: inside an ``https`` connection it knows the host and the port
and nothing else, not the path, not what is sent. So its lists are lists of hosts. What
is sent is judged elsewhere, before it is sent (``village.tools.browser``).
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from jimmyhq.lookup import WebError, safety

log = logging.getLogger("jimmyhq.browser.fence")

HEAD_MAX = 64 * 1024
HEAD_WAIT_S = 20.0
CONNECT_WAIT_S = 20.0
KEPT = 50  # refusals remembered, newest last

DENIED = ("This site is on the list of places no agent's browser may go ({host}). "
          "Nothing was fetched.")
NOT_LISTED = ("This agent's browser may only reach the sites its owner listed, and {host} "
              "is not one of them. Nothing was fetched.")


class Denied(Exception):  # noqa: N818 - an answer, not a fault
    """A request the fence will not let out. ``sentence`` says why; ``rule`` names the
    function that said no."""

    def __init__(self, rule: str, sentence: str, host: str = "", kind: str = "refused"):
        super().__init__(sentence)
        self.rule, self.sentence, self.host, self.kind = rule, sentence, host, kind


@dataclass(frozen=True)
class Refused:
    ts_ms: int
    host: str
    rule: str
    port: int = 0  # 0: the address was refused before it could be read


def host_matches(host: str, pattern: str) -> bool:
    """Whether a host falls under one pattern. A plain name covers itself and every
    name under it; a pattern with a star is matched against the whole host."""
    host, pattern = host.lower().strip("."), pattern.lower().strip(".")
    if not host or not pattern:
        return False
    if "*" in pattern:
        return fnmatch.fnmatchcase(host, pattern)
    return host == pattern or host.endswith("." + pattern)


def shape(url: object) -> safety.Parsed:
    """Rule ``browser_address_shape``: the lookup module's reading of an address. Only
    http and https, so never a file, and never a browser's own pages."""
    try:
        return safety.parse(url)
    except WebError as e:
        raise Denied("browser_address_shape", e.message, kind=e.kind) from None


def hard_denied(host: str, patterns: Iterable[str]) -> str | None:
    """Rule ``browser_hard_deny``: the pattern that puts this host out of reach."""
    return next((p for p in patterns if host_matches(host, p)), None)


def outside_allow_list(host: str, allowed: Iterable[str] | None) -> bool:
    """Rule ``browser_allow_list``: with an allow-list, every host not on it."""
    if not allowed:
        return False
    return not any(host_matches(host, p) for p in allowed)


class Fence:
    """The rules for one agent's browser. ``allow_only`` reads that agent's allow-list
    each time, so the owner's change takes effect on the next request. ``also_allow``
    is for tests alone: host and port pairs let past the address rule, so a test can
    serve pages from loopback. Nothing in JimmyHQ sets it."""

    def __init__(self, core: Any, *, allow_only: Callable[[], Iterable[str] | None] | None = None,
                 resolver: safety.Resolver = safety.system_resolver,
                 also_allow: Iterable[tuple[str, int]] = ()):
        self.core = core
        self.allow_only = allow_only or (lambda: None)
        self.resolver = resolver
        self.also_allow = frozenset((h.lower(), int(p)) for h, p in also_allow)
        self.refused: deque[Refused] = deque(maxlen=KEPT)
        self.refused_total = 0

    async def address(self, parsed: safety.Parsed) -> safety.Checked:
        """Rule ``browser_public_address``: the lookup module's address rules, and the
        one address to connect to."""
        if (parsed.host, parsed.port) in self.also_allow:
            found = [parsed.host] if parsed.literal is not None else \
                await self.resolver(parsed.host, parsed.port)
            if found:
                return safety.Checked(parsed.url, parsed.scheme, parsed.host, parsed.port,
                                      parsed.target, found[0], allowed_private=True)
        try:
            return await safety.check(self.core, parsed.url, self.resolver)
        except WebError as e:
            raise Denied("browser_public_address", e.message, parsed.host, e.kind) from None

    async def check(self, url: object) -> safety.Checked:
        """One request, checked. Raises ``Denied``, and remembers that it did."""
        port = 0
        try:
            parsed = shape(url)
            port = int(parsed.port or 0)
            if hard_denied(parsed.host, self.core.settings.get("browser.hard_deny")):
                raise Denied("browser_hard_deny", DENIED.format(host=parsed.host), parsed.host)
            if outside_allow_list(parsed.host, self.allow_only()):
                raise Denied("browser_allow_list", NOT_LISTED.format(host=parsed.host),
                             parsed.host)
            return await self.address(parsed)
        except Denied as e:
            self.refused.append(Refused(int(time.time() * 1000), e.host[:253], e.rule, port))
            self.refused_total += 1
            raise


# --- the proxy ------------------------------------------------------------------------------


def _answer(status: str, rule: str = "", text: str = "") -> bytes:
    body = (f"<!doctype html><title>Not opened</title><p>{text}</p>").encode() if text else b""
    head = [f"HTTP/1.1 {status}", "Connection: close", f"Content-Length: {len(body)}",
            "Content-Type: text/html; charset=utf-8"]
    if rule:
        head.append(f"X-JimmyHQ-Fence: {rule}")
    return ("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            writer.close()


class FenceProxy:
    """A forward proxy on loopback that lets out only what ``Fence.check`` allows, and
    connects to the address that was checked. One request per connection for plain
    http, so every request is checked; an https tunnel is checked when it is opened and
    goes to one host only."""

    def __init__(self, fence: Fence):
        self.fence = fence
        self.port = 0
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        safety.listening_here(self.port)  # never an address an app may be let reach
        return self.port

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        safety.stopped_listening_here(self.port)
        server.close()
        for task in list(self._tasks):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), 2)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        try:
            await self._handle(reader, writer)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                ValueError, IndexError):
            pass
        finally:
            with contextlib.suppress(OSError, RuntimeError):
                writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_WAIT_S)
        if len(head) > HEAD_MAX:
            writer.write(_answer("431 Request Header Fields Too Large"))
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            writer.write(_answer("400 Bad Request"))
            return
        tunnel = method.upper() == "CONNECT"
        if tunnel:
            url = f"https://{target}/"
        elif target.lower().startswith("http://"):
            url = target
        else:  # not a request to a proxy, or a scheme a proxy request cannot carry
            writer.write(_answer("400 Bad Request", "browser_address_shape",
                                 "Only http and https addresses are fetched."))
            return
        try:
            checked = await self.fence.check(url)
        except Denied as e:
            writer.write(_answer("403 Forbidden", e.rule, e.sentence))
            await writer.drain()
            return
        body_length = 0
        if not tunnel:
            headers = _header_values(lines[1:])
            if "transfer-encoding" in headers:
                writer.write(_answer("411 Length Required", "browser_request_shape",
                                     "A request body must say how long it is; chunked "
                                     "bodies are not sent."))
                return
            try:
                body_length = _body_length(headers.get("content-length", []))
            except ValueError:
                writer.write(_answer("400 Bad Request", "browser_request_shape",
                                     "The request's length was not valid."))
                return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(checked.address, checked.port), CONNECT_WAIT_S)
        except (OSError, TimeoutError):
            writer.write(_answer("502 Bad Gateway", "", "The site could not be reached."))
            return
        try:
            if tunnel:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
                return
            kept = [line for line in lines[1:] if line and not line.lower().startswith(
                ("proxy-", "connection:", "keep-alive:"))]
            request = "\r\n".join([f"{method} {checked.target} {version}", *kept,
                                   "Connection: close"]) + "\r\n\r\n"
            up_writer.write(request.encode("latin-1"))
            await up_writer.drain()
            # Exactly one request: its head and its body, and nothing the browser sends
            # after it is read, so a second request on this connection is never passed on.
            await _copy_exactly(reader, up_writer, body_length)
            await _relay_answer(up_reader, writer, head_only=method.upper() == "HEAD")
        finally:
            with contextlib.suppress(OSError, RuntimeError):
                up_writer.close()


def _header_values(lines: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in lines:
        name, sep, value = line.partition(":")
        if sep:
            out.setdefault(name.strip().lower(), []).append(value.strip())
    return out


def _body_length(values: list[str]) -> int:
    """The one Content-Length a request names (0 when it names none). Two that differ,
    or one that is not a plain number, are an error: the proxy and the site could
    otherwise read the request's end in different places."""
    if not values:
        return 0
    if len(set(values)) != 1 or not values[0].isdigit():
        raise ValueError("content-length")
    return int(values[0])


async def _copy_exactly(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                        count: int) -> None:
    while count > 0:
        data = await reader.read(min(count, 65536))
        if not data:
            raise asyncio.IncompleteReadError(b"", count)
        writer.write(data)
        await writer.drain()
        count -= len(data)


async def _relay_answer(up: asyncio.StreamReader, out: asyncio.StreamWriter, *,
                        head_only: bool) -> None:
    """Pass one answer from the site to the browser and stop where it ends. Its
    Connection header says close and its Keep-Alive is dropped, so the browser does not
    send another request down this connection, and the connection is closed after it
    even if the site would keep it open."""
    while True:
        head = await up.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ", 2)[1])
        headers = _header_values(lines[1:])
        kept = [line for line in lines[1:] if line and not line.lower().startswith(
            ("connection:", "keep-alive:", "proxy-connection:"))]
        out.write(("\r\n".join([lines[0], *kept, "Connection: close"]) + "\r\n\r\n")
                  .encode("latin-1"))
        await out.drain()
        if status < 200 and status != 101:
            continue  # an interim answer (100, 103): the real one follows
        break
    if head_only or status in (101, 204, 304):
        return
    if any("chunked" in v.lower() for v in headers.get("transfer-encoding", [])):
        while True:
            line = await up.readuntil(b"\r\n")
            out.write(line)
            size = int(line.split(b";")[0].strip(), 16)
            if size == 0:
                while (trailer := await up.readuntil(b"\r\n")) != b"\r\n":
                    out.write(trailer)
                out.write(b"\r\n")
                await out.drain()
                return
            await _copy_exactly(up, out, size + 2)
    elif "content-length" in headers:
        await _copy_exactly(up, out, _body_length(headers["content-length"]))
    else:  # no length: the body ends when the site closes
        await _pipe(up, out)
    await out.drain()


# --- what may be sent -----------------------------------------------------------------------

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def sends_data(method: str, has_body: bool = False) -> bool:
    """Whether a request carries something to the site: any method but GET, HEAD and
    OPTIONS, or any request with a body whatever its method says."""
    return has_body or method.upper() not in SAFE_METHODS


def may_send(method: str, level: str, permitted: bool = False, has_body: bool = False) -> bool:
    """Rule ``browser_sending_needs_leave``: whether a request the page is about to make
    may go, by its method and whether it has a body. This is the backstop under the
    tools' own check of what a click would do, for whatever a page sends by script.

    A request that sends nothing (GET, HEAD, OPTIONS with no body) always may. At
    ``read`` nothing else does. At ``approve`` a request that sends goes only while the
    village has just permitted it (the owner approved a form on that page), whether it is
    a page load or a page's own background request, from a click or from the page's own
    script. At ``alone`` everything passes.

    What this cannot hold: data a page puts into an address and fetches with GET (a GET
    form, a link, a picture, ``location``), and what a page sends over a web socket once
    it is open. Neither looks any different, at this level, from reading."""
    if level == "alone" or not sends_data(method, has_body):
        return True
    if level == "read":
        return False
    return permitted
