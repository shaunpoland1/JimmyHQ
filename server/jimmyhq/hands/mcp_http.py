"""An MCP server at an address, spoken to over streamable HTTP.

Every request is its own POST to the one MCP endpoint. The server answers with a single
JSON object or with an event stream that ends in the answer; both are read here, up to
``max_message_bytes``. The owner's saved headers go with every request and nowhere else:
a redirect is never followed, so they cannot be carried to another host.

Not done here: the browser sign-in some servers want (OAuth), the long-lived stream of
change notices, taking up a stream again after it broke, and the older two-endpoint
transport that came before streamable HTTP.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from jimmyhq.hands.mcp import (
    Config,
    Garbled,
    HttpStatus,
    Lost,
    Reply,
    TimedOut,
    TooLarge,
    Unavailable,
)

ACCEPT = "application/json, text/event-stream"
EVENT_STREAM = "text/event-stream"
SESSION_CHARS = 512
CONNECT_S = 15.0
REFUSAL = {"code": -32601, "message": "This client only lists and calls tools."}


class HttpServer:
    """One address. It keeps nothing between requests but where to send them."""

    kind = "http"
    generation = 0  # there is no program to restart

    def __init__(self, config: Config, http: Any = None):
        self.url = config.url
        self.limits = config.limits
        self._headers = dict(config.headers)
        self.http = http  # an injected client or transport; None means make one
        self.refused = 0

    async def open(self) -> None:
        return None

    def settled(self, ok: bool) -> None:
        return None

    def drop(self) -> None:
        return None

    def close(self) -> None:
        return None

    def _client(self, timeout: float) -> tuple[httpx.AsyncClient, bool]:
        """The client to use, and whether it is ours to close."""
        if isinstance(self.http, httpx.AsyncClient):
            return self.http, False
        return httpx.AsyncClient(
            transport=self.http, follow_redirects=False,
            timeout=httpx.Timeout(timeout, connect=min(timeout, CONNECT_S))), True

    def _sent(self, headers: dict[str, str] | None) -> dict[str, str]:
        # The protocol's own headers are written last: a saved header cannot replace one.
        return self._headers | (headers or {}) | {"Accept": ACCEPT,
                                                  "Content-Type": "application/json"}

    async def exchange(self, message: dict[str, Any], *, timeout: float,
                       headers: dict[str, str] | None = None) -> Reply:
        """POST one request and read its answer. Raises ``Unavailable`` when the server
        could not be reached at all, ``HttpStatus`` for a status with no protocol answer
        in it, and ``Lost`` when the request left and no usable answer came."""
        body = json.dumps(message, separators=(",", ":")).encode("ascii")
        if len(body) > self.limits.max_message_bytes:
            raise Unavailable("The request is larger than this server's limit of "
                              f"{self.limits.max_message_bytes} bytes.")
        client, ours = self._client(timeout)
        try:
            async with asyncio.timeout(timeout):
                async with client.stream("POST", self.url, content=body,
                                         headers=self._sent(headers)) as response:
                    return await self._reply(client, response, message.get("id"), headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.UnsupportedProtocol):
            raise Unavailable("The server could not be reached at its address. Check the "
                              "address and that the server is running.") from None
        except (TimeoutError, httpx.TimeoutException):
            raise TimedOut("The server did not answer in the time allowed.") from None
        except httpx.HTTPError as exc:
            raise Lost(f"The connection to the server broke ({type(exc).__name__}) before "
                       "it answered.") from None
        finally:
            if ours:
                await client.aclose()

    async def notify(self, message: dict[str, Any], *, timeout: float,
                     headers: dict[str, str] | None = None) -> None:
        await self.exchange(message, timeout=timeout, headers=headers)

    async def _reply(self, client: httpx.AsyncClient, response: httpx.Response, rid: Any,
                     headers: dict[str, str] | None) -> Reply:
        status = response.status_code
        session = response.headers.get("mcp-session-id")
        if session is not None and not (0 < len(session) <= SESSION_CHARS
                                        and all("!" <= ch <= "~" for ch in session)):
            raise Garbled("The server named a session in a form the protocol does not allow.")
        kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if rid is None:  # a notice: the server only has to take it
            if status >= 300:
                raise HttpStatus(status)
            return Reply(None, status, session)
        if status == 200 and kind == EVENT_STREAM:
            return Reply(await self._stream(client, response, rid, headers), status, session)
        try:
            message = json.loads(await self._body(response))
        except (ValueError, RecursionError):
            message = None
        if isinstance(message, dict):
            if isinstance(message.get("error"), dict):
                return Reply(message, status, session)
            if status == 200 and "result" in message and message.get("id") == rid:
                return Reply(message, status, session)
        if status != 200:
            raise HttpStatus(status)
        raise Garbled("The server's answer was not the protocol.")

    async def _body(self, response: httpx.Response) -> bytes:
        limit, held = self.limits.max_message_bytes, bytearray()
        async for chunk in response.aiter_bytes():
            held += chunk
            if len(held) > limit:
                raise TooLarge(f"The server sent an answer larger than the limit of {limit} "
                               "bytes, so reading it was stopped.")
        return bytes(held)

    async def _stream(self, client: httpx.AsyncClient, response: httpx.Response, rid: Any,
                      headers: dict[str, str] | None) -> dict[str, Any]:
        """Read an event stream until the answer to this request. Notices on the way
        (progress, log lines) are passed over. A request from the server is refused."""
        limit = self.limits.max_message_bytes
        held, data = bytearray(), bytearray()
        large = TooLarge(f"The server sent a message larger than the limit of {limit} "
                         "bytes, so reading it was stopped.")
        async for chunk in response.aiter_bytes():
            held += chunk
            while True:
                end = held.find(b"\n")
                if end < 0:
                    break
                line = bytes(held[:end]).rstrip(b"\r")
                del held[:end + 1]
                if line.startswith(b"data:"):
                    data += line[5:].removeprefix(b" ") + b"\n"
                    if len(data) > limit:
                        raise large
                elif not line and data:
                    found = await self._event(client, bytes(data), rid, headers)
                    data.clear()
                    if found is not None:
                        return found
                # a comment (":"), an event name, an id or a retry time: not needed
            if len(held) > limit:
                raise large
        if data:  # the stream ended without the blank line that closes an event
            found = await self._event(client, bytes(data), rid, headers)
            if found is not None:
                return found
        raise Lost("The server's reply ended before the answer came.")

    async def _event(self, client: httpx.AsyncClient, data: bytes, rid: Any,
                     headers: dict[str, str] | None) -> dict[str, Any] | None:
        try:
            message = json.loads(data)
        except (ValueError, RecursionError):
            return None
        if not isinstance(message, dict):
            return None
        if "method" in message:
            if "id" in message:
                await self._refuse(client, message, headers)
            return None
        if message.get("id") == rid and ("result" in message or "error" in message):
            return message
        return None

    async def _refuse(self, client: httpx.AsyncClient, message: dict[str, Any],
                      headers: dict[str, str] | None) -> None:
        """A server that asks this client for something is told no, in a POST of its
        own. Only ``ping`` is answered."""
        self.refused += 1
        answer: dict[str, Any] = {"jsonrpc": "2.0", "id": message.get("id")}
        if message.get("method") == "ping":
            answer["result"] = {}
        else:
            answer["error"] = REFUSAL
        try:
            await client.post(self.url, content=json.dumps(answer).encode("ascii"),
                              headers=self._sent(headers), timeout=CONNECT_S)
        except httpx.HTTPError:
            pass
