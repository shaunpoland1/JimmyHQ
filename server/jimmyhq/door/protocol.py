"""The Model Context Protocol, server side, over streamable HTTP at one address.

Spoken directly (JSON-RPC 2.0 in an HTTP POST, one JSON object back), with no SDK: the
door offers tools and nothing else, the whole protocol for that is a handful of
methods, and a boundary this small is easier to hold when every line that reads a
request is here to be read. The client adapter in ``jimmyhq.hands`` made the same
choice; the two share no code.

Two eras are served on the one address:

- **2026-07-28**: no handshake and no session. Every request carries its revision in
  ``_meta`` and in the ``MCP-Protocol-Version`` header, its method in ``Mcp-Method`` and
  a tool's name in ``Mcp-Name``; a header that disagrees with the body is refused
  (``-32020``), an unknown revision is refused with the list of known ones (``-32022``).
  ``server/discover`` says what is spoken.
- **2025-11-25 and older**: ``initialize`` first. No session is given out: nothing is
  remembered between requests in either era, so there is no session to steal.

Order of checks on every request, cheapest and least revealing first: is the door on,
is the request addressed to a name the door answers to, does it come from a web page
that may call, does it carry a client's token (401 with no detail otherwise), is the
client within its rate, is the request small enough, and only then is it read.

Not offered: resources, prompts, sampling, elicitation, change notices, the GET
stream, sessions, batches, and the OAuth sign-in flow. A client is given a token by the
owner and sends it as ``Authorization: Bearer``.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from jimmyhq import __version__
from jimmyhq.auth import MAX_TOKEN
from jimmyhq.door.service import NOT_A_TOOL, Door
from jimmyhq.web.locality import (
    LOCAL_NAMES,
    client_key,
    host_name,
    is_forwarded,
    is_local_name,
    is_public,
    public_refusal,
)

log = logging.getLogger("jimmyhq.door")

PATH = "/mcp"
MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED = (MODERN, *LEGACY)
META = "io.modelcontextprotocol/"
SERVER = {"name": "jimmyhq-door", "version": __version__}
INSTRUCTIONS = ("A private system's door. You can use only the tools listed, within daily "
                "limits; call whoami to see them. Everything you write is signed with your "
                "client's name and the owner sees every call.")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022
SLOW_DOWN = -32000

_SENTINEL = ("=?base64?", "?=")


def bound_to(door: Door) -> str:
    """The address the door is reached on: JimmyHQ's own, or the door's own port's."""
    return door.core.config.host if door.on_main_port() else str(door.S("host"))


def allowed_host(door: Door, request: Request) -> bool:
    """Rule ``door_host_allowed``: the door answers only to names it is known by: this
    computer's own, the address it listens on, and the owner's list. Without this,
    another site could point its own name at this machine and reach the door from a
    browser on it.

    As on JimmyHQ's own address (``web.locality``): this computer's own names count only
    for a request nothing relayed. A tunnel on this computer passes on whatever Host the
    client wrote, so through it ``localhost`` would open the door to anyone who can reach
    the tunnel; a relayed request at one of them is refused, even if the owner listed it."""
    name = host_name(request.headers.get("host"))
    if not name:
        return False
    bound = bound_to(door)
    if not is_forwarded(request):
        if name in LOCAL_NAMES or name == bound.lower():
            return True
    elif is_local_name(name, bound):
        return False
    public = urlsplit(str(door.S("public_url") or "")).hostname
    return name == (public or "").lower() or name in {h.lower() for h in door.S("allowed_hosts")}


def allowed_origin(door: Door, origin: str | None, host: str | None) -> bool:
    """Rule ``door_origin_allowed``: a request that names the web page it came from is
    refused unless that page is on the owner's list. A client that is a program sends no
    Origin and passes. The page's own address being the door's is not enough: nothing is
    served there for a browser to be on."""
    if origin is None:
        return True
    name = (urlsplit(origin).hostname or "").lower()
    return bool(name) and name in {o.lower() for o in door.S("allowed_origins")}


def bearer(header: str | None) -> str | None:
    """The token in an Authorization header, if it is a Bearer one of a sane length."""
    if not header or not header[:7].lower() == "bearer ":
        return None
    token = header[7:].strip()
    return token if 0 < len(token) <= MAX_TOKEN else None


def header_text(value: str | None) -> str | None:
    """A mirrored header's value as the body would carry it: plain, or decoded from
    ``=?base64?...?=``. None when it is missing or cannot be decoded."""
    if value is None:
        return None
    if value.startswith(_SENTINEL[0]) and value.endswith(_SENTINEL[1]):
        try:
            return base64.b64decode(value[len(_SENTINEL[0]):-len(_SENTINEL[1])],
                                    validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
    return value


def _plain(status: int, headers: dict[str, str] | None = None) -> Response:
    """An answer with no body: there is nothing to learn from it but the number."""
    return Response(status_code=status, headers={"Cache-Control": "no-store",
                                                 **(headers or {})})


def _json(status: int, message: dict[str, Any], headers: dict[str, str] | None = None
          ) -> Response:
    return Response(json.dumps(message, ensure_ascii=False, separators=(",", ":")),
                    status_code=status, media_type="application/json",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             **(headers or {})})


def _error(status: int, rid: Any, code: int, message: str, data: Any = None,
           headers: dict[str, str] | None = None) -> Response:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return _json(status, {"jsonrpc": "2.0", "id": rid, "error": error}, headers)


def _result(rid: Any, result: dict[str, Any]) -> Response:
    return _json(200, {"jsonrpc": "2.0", "id": rid, "result": result})


async def read_body(request: Request, limit: int) -> bytes | None:
    """Rule ``door_request_size``: the request's body, or None when it is larger than
    the limit. A declared size over the limit is refused before a byte is read, and a
    body that grows past it stops being read."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    held = bytearray()
    async for chunk in request.stream():
        held += chunk
        if len(held) > limit:
            return None
    return bytes(held)


def era_of(method: str, header: str | None, meta_version: Any) -> tuple[str | None, str]:
    """Rule ``door_revision_agreed``: which era a request is in: ``("modern", "")``,
    ``("legacy", "")``, or ``(None, why)`` with ``why`` one of ``mismatch`` and
    ``unsupported``. ``initialize`` is always the older handshake. Otherwise the header
    decides, and the body must agree with it."""
    if method == "initialize":
        return "legacy", ""
    if header is None and meta_version is None:
        return "legacy", ""  # the oldest clients send neither
    if header is None or (meta_version is not None and meta_version != header):
        return None, "mismatch" if (MODERN in (header, meta_version)) else "unsupported"
    if header == MODERN:
        return ("modern", "") if meta_version == MODERN else (None, "mismatch")
    if header in LEGACY:
        return "legacy", ""
    return None, "unsupported"


def tool_result(outcome: Any, modern: bool) -> dict[str, Any]:
    """A tool's outcome as the protocol carries it: the answer as JSON text and as
    structured content, or the refusal's sentence marked as an error. A failed app tool
    carries the door's own sentence, then what the app said in its marked shape."""
    if outcome.ok:
        out: dict[str, Any] = {
            "content": [{"type": "text", "text": json.dumps(outcome.data, ensure_ascii=False)}],
            "structuredContent": outcome.data, "isError": False}
    elif outcome.data is not None:
        out = {"content": [{"type": "text", "text": outcome.sentence},
                           {"type": "text",
                            "text": json.dumps(outcome.data, ensure_ascii=False)}],
               "structuredContent": outcome.data, "isError": True}
    else:
        out = {"content": [{"type": "text", "text": outcome.sentence}], "isError": True}
    if modern:
        out["resultType"] = "complete"
    return out


async def handle(door: Door, request: Request) -> Response:  # noqa: C901, PLR0911, PLR0912
    """One request to the MCP address, start to finish."""
    if not door.enabled():
        return _plain(404)
    # Not from the public internet (Tailscale Funnel), unless the owner said so in
    # config.toml: the same rule as JimmyHQ's own address, on the door's own port too.
    if is_public(request) and not door.core.config.allow_public:
        port = door.core.config.port if door.on_main_port() else int(door.S("port"))
        return _error(403, None, INVALID_REQUEST, public_refusal(port))
    host = request.headers.get("host")
    if not allowed_host(door, request):
        return _plain(421)
    if not allowed_origin(door, request.headers.get("origin"), host):
        return _plain(403)

    # Who is asking. Nothing below this is reachable without a client's token.
    # Relayed requests are counted apart from this computer's own (``client_key``).
    source = client_key(request, bound_to(door))
    token = bearer(request.headers.get("authorization"))
    client = await run_in_threadpool(door.authenticate, token) if token else None
    if client is None:
        if door.failures.blocked(source, int(door.S("failed_tries")),
                                 int(door.S("failed_tries_all"))):
            return _plain(429, {"Retry-After": "900"})
        door.failures.record(source)
        return _plain(401, {"WWW-Authenticate": "Bearer"})

    if request.method != "POST":
        return _plain(405, {"Allow": "POST"})
    if not door.within_rate(client):
        await run_in_threadpool(lambda: door.receipt(client, "(any)", ok=False,
                                                     rule="door_rate_limit"))
        return _error(429, None, SLOW_DOWN,
                      "Nothing happened: you are sending requests faster than this client "
                      "is allowed each minute. Wait a minute and carry on more slowly.",
                      headers={"Retry-After": "60"})
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind != "application/json":
        return _plain(415)
    accept = request.headers.get("accept")
    if accept and not any(part.split(";")[0].strip() in ("application/json", "application/*",
                                                         "*/*")
                          for part in accept.split(",")):
        return _plain(406)
    limit = int(door.S("max_request_bytes"))
    body = await read_body(request, limit)
    if body is None:
        return _error(413, None, INVALID_REQUEST,
                      f"The request is larger than the door reads ({limit} bytes). Send "
                      "less in one request.")
    try:
        message = json.loads(body)
    except (ValueError, RecursionError):
        return _error(400, None, PARSE_ERROR, "The request is not valid JSON.")
    if not isinstance(message, dict):
        return _error(400, None, INVALID_REQUEST,
                      "Send one JSON-RPC request in each POST, not a list.")
    method, rid, params = message.get("method"), message.get("id"), message.get("params", {})
    if isinstance(rid, bool) or not isinstance(rid, str | int | type(None)):
        return _error(400, None, INVALID_REQUEST, "The request's id must be text or a number.")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str) \
            or not isinstance(params, dict):
        return _error(400, rid, INVALID_REQUEST, "This is not a JSON-RPC 2.0 request.")
    if "id" not in message:
        # A notice (the older handshake's "initialized", a cancel): taken, nothing done.
        return _plain(202)

    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    header = request.headers.get("mcp-protocol-version")
    era, why = era_of(method, header, meta.get(META + "protocolVersion"))
    if era is None:
        if why == "mismatch":
            return _error(400, rid, HEADER_MISMATCH,
                          "Header mismatch: MCP-Protocol-Version must be sent and must match "
                          "the protocolVersion in the request's _meta.")
        return _error(400, rid, UNSUPPORTED_VERSION, "Unsupported protocol version",
                      {"supported": list(SUPPORTED),
                       "requested": str(header or meta.get(META + "protocolVersion"))[:40]})
    modern = era == "modern"
    if modern:
        if header_text(request.headers.get("mcp-method")) != method:
            return _error(400, rid, HEADER_MISMATCH,
                          "Header mismatch: Mcp-Method must be sent and must match the "
                          "request's method.")
        if method == "tools/call" and \
                header_text(request.headers.get("mcp-name")) != params.get("name"):
            return _error(400, rid, HEADER_MISMATCH,
                          "Header mismatch: Mcp-Name must be sent and must match the tool's "
                          "name in the request.")

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(rid, {
            "protocolVersion": asked if asked in LEGACY else LEGACY[0],
            "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER,
            "instructions": INSTRUCTIONS})
    if method == "server/discover" and modern:
        return _result(rid, {
            "resultType": "complete", "supportedVersions": list(SUPPORTED),
            "capabilities": {"tools": {"listChanged": False}},
            "_meta": {META + "serverInfo": SERVER}, "instructions": INSTRUCTIONS,
            "ttlMs": 0, "cacheScope": "private"})
    if method == "ping":
        return _result(rid, {"resultType": "complete"} if modern else {})
    if method == "tools/list":
        listed = await run_in_threadpool(door.listed, client)
        result: dict[str, Any] = {"tools": listed}
        if modern:
            # What a client may use depends on who it is, so nobody else may reuse it.
            result |= {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
        return _result(rid, result)
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _error(400, rid, INVALID_PARAMS, "tools/call needs the tool's name.")
        outcome = await run_in_threadpool(door.call, client, name, params.get("arguments"),
                                          len(body))
        if not outcome.known:
            return _error(200, rid, INVALID_PARAMS, NOT_A_TOOL)
        return _result(rid, tool_result(outcome, modern))
    return _error(404 if modern else 200, rid, METHOD_NOT_FOUND,
                  "Method not found. This server offers tools only: server/discover, "
                  "tools/list, tools/call and ping.")
