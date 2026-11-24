"""MCP servers as a provider: each tool of a Model Context Protocol server is an action.

A server is either a program on this machine, spoken to over its standard streams
(``mcp_stdio.py``), or an address spoken to over streamable HTTP (``mcp_http.py``). This
file is everything the two share: reading the owner's record, agreeing a protocol
revision, listing tools, calling one, and turning what comes back into the core's terms.

This adapter is a client that lists tools and calls them, and nothing else. Three rules
hold everywhere in it:

- **A server's text is data.** A tool's name, title, description and schema are shown to
  the owner and handed to the core as an ``Action``; nothing here builds a prompt or
  acts on what a server wrote. A server's ``instructions`` and its name for itself are
  never read. Hidden and control characters are taken out and lengths are capped.
- **A server asks for nothing.** Requests from a server (sampling, elicitation, roots)
  are refused, and a result that asks for more input is a failed call.
- **A saved value never leaves in words.** Environment values and headers come from the
  secret store, go to the server, and are taken out of anything the server sends back.
  The errors raised here carry sentences written here, never a server's output.

The protocol is spoken directly (JSON-RPC 2.0), in revision 2026-07-28 where the server
has it and with the older ``initialize`` handshake where it does not. See
``docs/hands.md``, "MCP servers".
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import hashlib
import itertools
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jimmyhq import __version__
from jimmyhq.hands import options as record_options
from jimmyhq.hands.provider import (
    Account,
    Action,
    Connect,
    Health,
    Outcome,
    ProviderRecord,
    ProviderType,
    RecordField,
    Wiring,
    register_type,
    unregister_type,
)
from jimmyhq.models import validate

log = logging.getLogger("jimmyhq.hands.mcp")

TYPE = "mcp"
ACCOUNT = "server"  # a server has one account: itself
STDIO, HTTP = "stdio", "http"

MODERN = "2026-07-28"  # every request carries its version; no handshake, no session
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # ``initialize`` first
MODERN_ERA, LEGACY_ERA = "modern", "legacy"
HEADER_MISMATCH, MISSING_CAPABILITY, UNSUPPORTED_VERSION = -32020, -32021, -32022
MODERN_ERRORS = (HEADER_MISMATCH, MISSING_CAPABILITY, UNSUPPORTED_VERSION)
META = "io.modelcontextprotocol/"
CLIENT = {"name": "jimmyhq", "version": __version__}

# How long a silent server is given to answer the first question before the older
# handshake is tried as well.
PROBE_WAIT_S = 3.0
MAX_PAGES = 100
MAX_BLOCKS = 200
LABEL_CHARS, DESCRIPTION_CHARS, SCHEMA_TEXT_CHARS = 80, 1000, 300
NAME_CHARS = 128
SCHEMA_BYTES, SCHEMA_DEPTH = 32_000, 16
ERROR_CHARS = 2000
REMOVED = "[removed]"
HANDSHAKE_FAILED = ("The server did not complete the opening handshake. Check that it is an "
                    "MCP server.")
REDACT_FROM = 6  # a saved value shorter than this is too common a word to take out

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,35}$")
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,32}$")
_HEADER_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
# Headers this adapter sets itself, or that would change how the request is framed.
_OWN_HEADERS = ("accept", "connection", "content-length", "content-type", "host",
                "transfer-encoding", "upgrade")
_SAFE_INT = 2 ** 53 - 1


# --- what can go wrong ----------------------------------------------------------------------


class McpError(Exception):
    """``sentence`` is one sentence for the owner, written here. It never holds a
    server's words or a saved value, so it is safe to show and to log."""

    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


class Unavailable(McpError):
    """The server could not be used, and nothing was sent to one of its tools."""


class Lost(McpError):
    """A request left and no usable answer came back. Nobody knows what happened."""


class Exited(Lost):
    """The program stopped while a request was waiting."""


class TimedOut(Lost):
    """No answer came in the time allowed."""


class TooLarge(Lost):
    """The server sent a message larger than the limit."""


class Garbled(Lost):
    """The server answered with something that is not the protocol."""


class RpcError(McpError):
    """The server answered with a protocol error. ``message`` is its words: untrusted."""

    def __init__(self, code: Any, message: Any, data: Any = None, status: int = 200):
        super().__init__("The server answered with an error.")
        self.code = code if isinstance(code, int) else 0
        self.message = message if isinstance(message, str) else ""
        self.data = data
        self.status = status


class HttpStatus(McpError):
    """An HTTP status with no protocol answer in it."""

    def __init__(self, status: int):
        super().__init__(_status_sentence(status))
        self.status = status


def _status_sentence(status: int) -> str:
    if status in (401, 403):
        return (f"The server refused the sign-in (status {status}). Save its header again "
                "under Settings, Integrations. A server that wants a browser sign-in "
                "cannot be used yet.")
    if 300 <= status < 400:
        return (f"The address answered with a redirect (status {status}), which is not "
                "followed. Save the address it points to instead.")
    if status == 429:
        return "The server says it is being asked too often. Try again later."
    if status >= 500:
        return f"The server had a fault (status {status}). Try again later."
    return (f"No MCP server answered at that address (status {status}). Check the address.")


@dataclass(frozen=True)
class Reply:
    """One answer from a transport: the JSON-RPC response, and for HTTP its status and
    the session the server named."""

    message: dict[str, Any] | None
    status: int = 200
    session: str | None = None


# --- the owner's record ---------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    """Every wait and size of one server. The numbers come from the owner's settings
    (``hands.mcp_*``) unless the provider's own options set them: see ``_limits``."""

    start_timeout_s: float
    call_timeout_s: float
    idle_s: float
    max_message_bytes: int
    max_tools: int


# option -> (lowest, highest). The setting that holds its default is ``hands.mcp_<option>``.
LIMIT_RANGES: dict[str, tuple[float, float]] = {
    "start_timeout_s": (0.1, 300), "call_timeout_s": (0.1, 3600), "idle_s": (0.1, 86_400),
    "max_message_bytes": (1_000, 64_000_000), "max_tools": (1, 1000),
}


@dataclass(frozen=True)
class Config:
    """A provider record read as an MCP server. ``env`` and ``headers`` hold saved
    values, so they are kept out of its printed form."""

    transport: str
    limits: Limits
    command: str = ""
    args: tuple[str, ...] = field(default=(), repr=False)  # may hold a saved value
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict, repr=False)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    arg_values: tuple[str, ...] = field(default=(), repr=False)  # saved values put in args

    def secrets(self) -> list[str]:
        """Every saved value, longest first, for taking out of what a server says."""
        found = {v for v in (*self.env.values(), *self.headers.values(), *self.arg_values)
                 if len(v) >= REDACT_FROM}
        for value in self.headers.values():  # "Bearer abc": the token alone is one too
            found |= {part for part in value.split() if len(part) >= REDACT_FROM}
        return sorted(found, key=len, reverse=True)

    def signature(self) -> str:
        written = json.dumps([self.transport, self.command, self.args, self.cwd,
                              sorted(self.env.items()), self.url,
                              sorted(self.headers.items()), list(vars(self.limits).values())])
        return hashlib.sha256(written.encode("utf-8")).hexdigest()


def secret_for(kind: str, name: str) -> str:
    """The short name a value is saved under: ``env_<variable>`` or ``header_<header>``,
    in lower case."""
    return f"{kind}_{name.lower()}"


def _limits(options: dict[str, Any], wiring: Wiring) -> Limits:
    """Each limit from the provider's own option when it has one, else from the owner's
    setting ``hands.mcp_<option>``."""
    given: dict[str, Any] = {}
    for key, (low, high) in LIMIT_RANGES.items():
        value = wiring.limit(options, key, f"mcp_{key}", low=low, high=high)
        if value is None:
            raise Unavailable(f"The option \"{key}\" must be a number from {low:g} to "
                              f"{high:g}. Change it in the provider's options.")
        given[key] = int(value) if key.startswith("max_") else float(value)
    return Limits(**given)


def _names(options: dict[str, Any], key: str, pattern: re.Pattern[str], what: str
           ) -> list[str]:
    names = options.get(key) or []
    if (not isinstance(names, list) or len(names) > 40
            or not all(isinstance(n, str) and pattern.match(n) for n in names)):
        raise Unavailable(f"The option \"{key}\" must be a list of {what} names. Their "
                          "values are not written there: save each one as a secret of this "
                          "provider.")
    if len({n.lower() for n in names}) != len(names):
        raise Unavailable(f"The option \"{key}\" names the same {what} twice.")
    return names


def _saved(names: list[str], kind: str, what: str, wiring: Wiring) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in names:
        value = wiring.secret(secret_for(kind, name))
        if not value:
            raise Unavailable(
                f"No value is saved for the {what} \"{name}\". Save it under Settings, "
                f"Integrations, as the secret \"{secret_for(kind, name)}\" of this provider.")
        out[name] = value
    return out


def read_config(record: ProviderRecord, wiring: Wiring) -> Config:
    """The record as a server to start or an address to call. Raises ``Unavailable``
    with what to fix; nothing is started or contacted here."""
    options = record.options if isinstance(record.options, dict) else {}
    limits = _limits(options, wiring)
    transport = options.get("transport") or (HTTP if record.base_url else STDIO)
    if transport == STDIO:
        return _stdio_config(options, limits, wiring)
    if transport == HTTP:
        return _http_config(record, options, limits, wiring)
    raise Unavailable("The option \"transport\" must be \"stdio\" (a program on this "
                      "machine) or \"http\" (an address).")


def _stdio_config(options: dict[str, Any], limits: Limits, wiring: Wiring) -> Config:
    command, args, cwd = options.get("command"), options.get("args") or [], options.get("cwd")
    if not isinstance(command, str) or not command.strip():
        raise Unavailable("This MCP server has no program to run. Give the program as "
                          "\"command\" in its options, or give an address instead.")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise Unavailable("The option \"args\" must be a list of texts, one for each "
                          "argument of the program.")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
        raise Unavailable("The option \"cwd\" must be a folder's path, or left out.")
    if any("\x00" in part for part in (command, *args, cwd or "")):
        raise Unavailable("The command, its arguments and its folder cannot hold a NUL "
                          "character. Remove it.")
    env = _saved(_names(options, "env", _ENV_NAME, "environment variable"), "env",
                 "environment variable", wiring)
    if any("\x00" in value for value in env.values()):
        raise Unavailable("A saved environment value holds a NUL character, which a "
                          "program cannot be given. Save it again.")
    # A {secret:NAME} in an argument is put in here, when the program is about to be
    # started, and nowhere else: the record only ever holds the name.
    try:
        argv, put_in = record_options.with_secrets(args, wiring.secret)
    except KeyError as exc:
        name = exc.args[0]
        raise Unavailable(
            f"An argument refers to the secret \"{name}\", and no value is saved under "
            "that name. Save it under Settings, Integrations, as a secret of this "
            "provider.") from None
    if any("\x00" in part for part in argv):
        raise Unavailable("A saved value used in an argument holds a NUL character, which "
                          "a program cannot be given. Save it again.")
    return Config(transport=STDIO, limits=limits, command=command.strip(),
                  args=argv, cwd=cwd, env=env, arg_values=tuple(put_in))


def _http_config(record: ProviderRecord, options: dict[str, Any], limits: Limits,
                 wiring: Wiring) -> Config:
    url = record.base_url or ""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Unavailable("This MCP server has no address. Give the address of its MCP "
                          "endpoint, starting with https://.")
    if parts.scheme == "http" and not validate.looks_local(url):
        raise Unavailable("That address is not on your own network and does not use "
                          "https, so a saved header would cross the internet unprotected. "
                          "Use the https:// address.")
    names = _names(options, "headers", _HEADER_NAME, "header")
    if any(n.lower() in _OWN_HEADERS or n.lower().startswith("mcp-") for n in names):
        raise Unavailable("The option \"headers\" names a header the protocol sets itself. "
                          "Remove it from the list.")
    headers = _saved(names, "header", "header", wiring)
    if wiring.key and "authorization" not in {n.lower() for n in headers}:
        headers["Authorization"] = f"Bearer {wiring.key}"
    for name, value in headers.items():
        if value != value.strip() or not all(" " <= ch <= "~" for ch in value):
            raise Unavailable(f"The saved value of the header \"{name}\" has characters a "
                              "header cannot carry. Save it again.")
    return Config(transport=HTTP, limits=limits, url=url, headers=headers)


# --- untrusted text -------------------------------------------------------------------------


def _hidden(ch: str) -> bool:
    """Control, format (zero-width, direction marks, tag characters), private-use,
    surrogate and unassigned characters: nothing a reader sees, and a way to hide text."""
    return unicodedata.category(ch)[0] == "C"


def visible(value: Any) -> str:
    """Text with hidden and control characters taken out. Line breaks and tabs stay."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch in "\n\t" or not _hidden(ch))


def clean(value: Any, limit: int) -> str:
    """One line of a server's text, as it may be shown: no hidden or control
    characters, no line breaks, ``limit`` characters at most."""
    if not isinstance(value, str):
        return ""
    said = " ".join("".join(" " if ch in "\r\n\t" else ch for ch in value
                            if ch in "\r\n\t" or not _hidden(ch)).split())
    return said if len(said) <= limit else said[:limit - 1].rstrip() + "…"


def _plain(value: str) -> bool:
    return not any(_hidden(ch) and ch not in "\r\n\t" for ch in value)


def clean_schema(schema: Any) -> dict[str, Any] | None:
    """A tool's input schema as it may be shown, or None if it cannot be used.

    It must be a JSON Schema of an object, of a bounded size and depth. Descriptions and
    titles are cleaned like any other text from a server. A hidden character anywhere
    else (an argument's name, a listed value) means the schema is not what it looks
    like, so the tool is left out."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    try:
        if len(json.dumps(schema)) > SCHEMA_BYTES:
            return None
    except (TypeError, ValueError, RecursionError):
        return None

    def walk(node: Any, depth: int) -> Any:
        if depth > SCHEMA_DEPTH:
            raise ValueError("too deep")
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if not isinstance(key, str) or not _plain(key) or len(key) > 200:
                    raise ValueError("a name that is not plain text")
                if key in ("description", "title") and isinstance(value, str):
                    out[key] = clean(value, SCHEMA_TEXT_CHARS)
                else:
                    out[key] = walk(value, depth + 1)
            return out
        if isinstance(node, list):
            return [walk(value, depth + 1) for value in node]
        if isinstance(node, str) and not _plain(node):
            raise ValueError("a value with hidden characters")
        return node

    try:
        return walk(schema, 0)
    except ValueError:
        return None


def fingerprint(tool: dict[str, Any]) -> str:
    """Rule ``mcp_tool_fingerprint``: an action's version is a hash of the tool exactly
    as the server sent it (name, title, description, input schema and hints), before any
    cleaning or cutting. A server that rewrites any of them after the owner approved the
    tool reports another version, and the core withdraws the tool until it is reviewed."""
    seen = {key: tool.get(key) for key in ("name", "title", "description", "inputSchema",
                                           "annotations")}
    written = json.dumps(seen, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(written.encode("utf-8")).hexdigest()[:40]


def suggested_effect(tool: dict[str, Any]) -> tuple[str, str]:
    """Rule ``mcp_effect_is_a_suggestion``: the effect a server's hints suggest, and
    where it came from. The hints are the server's own word and nothing checks them, so
    the answer is marked "provider" and the owner confirms it. With no hint the effect
    is "change" and marked a guess: this never guesses that a tool only reads."""
    hints = tool.get("annotations")
    hints = hints if isinstance(hints, dict) else {}
    reads, destroys = hints.get("readOnlyHint"), hints.get("destructiveHint")
    if destroys is True:
        return "delete", "provider"
    if reads is True:
        return "read", "provider"
    if reads is False or destroys is False:
        return "change", "provider"
    return "change", "guess"


def to_action(tool: Any) -> Action | None:
    """Rule ``mcp_tool_text_is_data``: one tool as an action, or None if it cannot be
    used. Its words are for the owner to read: cleaned, cut, and never acted on."""
    if not isinstance(tool, dict):
        return None
    name = tool.get("name")
    if (not isinstance(name, str) or not 0 < len(name) <= NAME_CHARS or not _plain(name)
            or name != name.strip() or any(ch in name for ch in "\r\n\t")):
        return None
    schema = clean_schema(tool.get("inputSchema"))
    if schema is None:
        return None
    hints = tool.get("annotations")
    title = tool.get("title") or (hints.get("title") if isinstance(hints, dict) else None)
    effect, effect_from = suggested_effect(tool)
    return Action(
        name=name, label=clean(title, LABEL_CHARS) or clean(name, LABEL_CHARS),
        description=clean(tool.get("description"), DESCRIPTION_CHARS), arguments=schema,
        effect=effect, effect_from=effect_from, version=fingerprint(tool))


def param_headers(schema: Any) -> list[tuple[tuple[str, ...], str]]:
    """The arguments a tool wants copied into ``Mcp-Param-*`` headers, as (path, header
    name). Raises ValueError when a mark is not allowed where it is: the protocol says
    such a tool must be left out over HTTP."""
    found: list[tuple[tuple[str, ...], str]] = []
    seen: set[str] = set()

    def walk(node: Any, path: tuple[str, ...], reachable: bool, depth: int) -> None:
        if depth > SCHEMA_DEPTH * 2:
            raise ValueError("too deep")
        if isinstance(node, list):
            for value in node:
                walk(value, path, False, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if "x-mcp-header" in node:
            name = node["x-mcp-header"]
            if (not reachable or not path or not isinstance(name, str)
                    or not _HEADER_TOKEN.match(name) or name.lower() in seen
                    or node.get("type") not in ("string", "integer", "boolean")):
                raise ValueError("a header mark that is not allowed")
            seen.add(name.lower())
            found.append((path, name))
        for key, value in node.items():
            if key == "x-mcp-header":
                continue
            if key == "properties" and isinstance(value, dict):
                for prop, inner in value.items():
                    walk(inner, (*path, str(prop)), reachable, depth + 1)
            else:
                walk(value, path, False, depth + 1)

    walk(schema, (), True, 0)
    return found


def header_value(value: Any) -> str | None:
    """A value as an HTTP header carries it, or None if it cannot be one. Plain visible
    ASCII goes as it is; anything else goes as ``=?base64?...?=``."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        if abs(value) > _SAFE_INT:
            return None
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return None
    plain = (all(" " <= ch <= "~" or ch == "\t" for ch in text) and text == text.strip(" \t")
             and not (text.startswith("=?base64?") and text.endswith("?=")))
    if plain:
        return text
    return "=?base64?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def _strings(data: Any, fn: Any, depth: int = 0) -> Any:
    if isinstance(data, str):
        return fn(data)
    if depth > 64:
        return None
    if isinstance(data, dict):
        return {fn(k) if isinstance(k, str) else k: _strings(v, fn, depth + 1)
                for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [_strings(v, fn, depth + 1) for v in data]
    return data


# --- one server, as this process knows it ---------------------------------------------------


class Session:
    """What is remembered about one server between calls: its transport, which revision
    it speaks and, for an older HTTP server, its session. An adapter is made for every
    use, so this lives in ``SESSIONS`` by provider id."""

    def __init__(self, config: Config, transport: Any):
        self.signature = config.signature()
        self.kind = config.transport
        self.limits = config.limits
        self.transport = transport
        self.era: str | None = None
        self.version: str | None = None
        self.session_id: str | None = None
        self.generation = -1
        self.headers_of: dict[str, list[tuple[tuple[str, ...], str]]] | None = None
        self._mutex = threading.Lock()
        self._busy: threading.Event | None = None
        self._ids = itertools.count(1)

    def reset(self) -> None:
        with self._mutex:
            self.era = self.version = self.session_id = None
            self.headers_of = None

    def close(self) -> None:
        self.reset()
        self.transport.close()

    # --- agreeing a revision ----------------------------------------------------------

    async def ready(self) -> None:
        """Start the server if it is not running and agree a revision with it, once
        however many callers arrive together. Raises before anything reaches a tool."""
        for _ in range(4):
            await self.transport.open()
            with self._mutex:
                if self.era and self.generation == self.transport.generation:
                    return
                busy = self._busy
                if busy is None:
                    mine = self._busy = threading.Event()
            if busy is not None:  # another caller is agreeing it: wait, then look again
                # Waited for on the loop, not in a worker thread: enough waiters would
                # fill the thread pool and leave no thread for the starter's own write.
                loop = asyncio.get_running_loop()
                until = loop.time() + self.limits.start_timeout_s
                while not busy.is_set() and loop.time() < until:
                    await asyncio.sleep(0.02)
                continue
            try:
                generation = self.transport.generation
                self.session_id = None
                era, version = await self._negotiate()
                with self._mutex:
                    self.era, self.version, self.generation = era, version, generation
                    self.headers_of = None
                self.transport.settled(True)
                return
            except McpError:
                self.transport.settled(False)  # a failed start: stop it and back off
                raise
            except BaseException:
                self.transport.drop()  # given up half way: the next caller starts clean
                raise
            finally:
                with self._mutex:
                    self._busy = None
                mine.set()
        raise Unavailable("The server was still starting. Try again in a moment.")

    async def _negotiate(self) -> tuple[str, str]:
        """Rule ``mcp_revision_agreed``: ask ``server/discover`` in the newest revision.
        A server that answers it, or refuses the version in the newer way, is spoken to
        with no handshake. Any other answer, or silence, means an older server: it is
        given the ``initialize`` handshake, and must name a revision known here."""
        wait = self.limits.start_timeout_s
        probe = asyncio.ensure_future(self._ask("server/discover", {}, era=MODERN_ERA,
                                                timeout=wait))
        hello: asyncio.Future[Reply] | None = None
        try:
            done, _ = await asyncio.wait({probe}, timeout=min(PROBE_WAIT_S, wait))
            if done:
                if self._probed(probe.result() if not probe.exception() else
                                probe.exception()) == MODERN_ERA:
                    return MODERN_ERA, MODERN
                return LEGACY_ERA, await self._hello_done(await self._hello(wait), wait)
            # Silent so far. An older server may ignore what it does not know, so say
            # hello the old way too and go with whichever is answered.
            hello = asyncio.ensure_future(self._hello(wait))
            waiting: set[asyncio.Future[Reply]] = {probe, hello}
            failed: BaseException | None = None
            while waiting:
                done, waiting = await asyncio.wait(waiting,
                                                   return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    answer = task.exception() or task.result()
                    try:
                        if task is probe:
                            if self._probed(answer) == MODERN_ERA:
                                return MODERN_ERA, MODERN
                        elif isinstance(answer, RpcError | HttpStatus):
                            raise Unavailable(HANDSHAKE_FAILED)
                        elif isinstance(answer, BaseException):
                            raise answer
                        else:
                            return LEGACY_ERA, await self._hello_done(answer, wait)
                    except McpError as exc:
                        failed = exc
            raise failed or Unavailable("The server did not answer.")
        finally:
            for task in (probe, hello):
                if task is not None and not task.done():
                    task.cancel()

    def _probed(self, answer: Reply | BaseException) -> str:
        """Which kind of server answered the first question."""
        if isinstance(answer, TimedOut):
            return LEGACY_ERA  # an older server may say nothing to what it does not know
        if isinstance(answer, HttpStatus):
            if answer.status in (400, 404, 405, 406, 415, 422):
                return LEGACY_ERA
            raise Unavailable(answer.sentence)
        if isinstance(answer, BaseException):
            raise answer
        message = answer.message or {}
        error = message.get("error")
        if isinstance(error, dict):
            if error.get("code") not in MODERN_ERRORS:
                return LEGACY_ERA
            data = error.get("data")
            offered = data.get("supported") if isinstance(data, dict) else None
            if error.get("code") != UNSUPPORTED_VERSION:
                raise Unavailable("The server would not answer this client. It may need "
                                  "something JimmyHQ does not offer.")
        else:
            result = message.get("result")
            offered = result.get("supportedVersions") if isinstance(result, dict) else None
        offered = [v for v in offered if isinstance(v, str)] if isinstance(offered, list) \
            else []
        if MODERN in offered:
            return MODERN_ERA
        if any(v in LEGACY for v in offered):
            return LEGACY_ERA
        raise Unavailable("The server speaks no revision of the protocol that JimmyHQ "
                          f"does ({MODERN}, or {LEGACY[0]} and older). Update one of them.")

    async def _hello(self, timeout: float) -> Reply:
        return await self._ask("initialize", {
            "protocolVersion": LEGACY[0], "capabilities": {}, "clientInfo": CLIENT,
        }, era=LEGACY_ERA, timeout=timeout)

    async def _hello_done(self, reply: Reply, timeout: float) -> str:
        try:
            result = _result(reply)
        except (RpcError, HttpStatus):
            raise Unavailable(HANDSHAKE_FAILED) from None
        version = result.get("protocolVersion")
        if version not in LEGACY:
            raise Unavailable("The server speaks a revision of the protocol that JimmyHQ "
                              "does not. Update one of them.")
        self.session_id = reply.session
        self.version = version
        await self.transport.notify(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=timeout,
            headers=self._headers(LEGACY_ERA, "notifications/initialized", None, version))
        return version

    # --- asking -----------------------------------------------------------------------

    def _headers(self, era: str, method: str, name: str | None, version: str | None
                 ) -> dict[str, str]:
        if era == MODERN_ERA:
            headers = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
            if name is not None:
                headers["Mcp-Name"] = header_value(name) or ""
            return headers
        headers = {}
        if version and method != "initialize":
            headers["MCP-Protocol-Version"] = version
        if self.session_id and method != "initialize":
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _ask(self, method: str, params: dict[str, Any], *, era: str, timeout: float,
                   name: str | None = None, extra: dict[str, str] | None = None) -> Reply:
        params = dict(params)
        if era == MODERN_ERA:
            params["_meta"] = {META + "protocolVersion": MODERN, META + "clientInfo": CLIENT,
                               META + "clientCapabilities": {}}
        headers = self._headers(era, method, name, self.version) | (extra or {})
        message = {"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                   "params": params}
        return await self.transport.exchange(message, timeout=timeout, headers=headers)

    async def request(self, method: str, params: dict[str, Any], *, timeout: float,
                      name: str | None = None, extra: dict[str, str] | None = None
                      ) -> dict[str, Any]:
        """One request to a server that is ready, and its result."""
        for again in (True, False):
            await self.ready()
            try:
                return _result(await self._ask(method, params, era=self.era or MODERN_ERA,
                                               timeout=timeout, name=name, extra=extra))
            except HttpStatus as exc:
                # An older server forgot the session: it did nothing. Start a new one.
                if not (again and exc.status == 404 and self.session_id):
                    raise
                self.reset()
        raise Unavailable("The server did not answer.")  # pragma: no cover - not reached

    async def list_tools(self) -> list[dict[str, Any]]:
        """Rule ``mcp_listing_is_bounded``: every tool the server lists, page by page,
        up to ``max_tools`` and a fixed number of pages. A cursor that repeats ends it."""
        found: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = await self.request("tools/list", params,
                                        timeout=self.limits.call_timeout_s)
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise Garbled("The server's list of tools made no sense.")
            found.extend(t for t in tools if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if (not isinstance(cursor, str) or not cursor or cursor in seen
                    or len(found) >= self.limits.max_tools):
                break
            seen.add(cursor)
        if len(found) > self.limits.max_tools or (isinstance(cursor, str) and cursor):
            log.warning("an MCP server lists more tools than the limit of %d; the rest "
                        "were left out", self.limits.max_tools)
        return found[:self.limits.max_tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        params = {"name": name, "arguments": arguments}
        timeout = self.limits.call_timeout_s
        if self.kind != HTTP:
            return await self.request("tools/call", params, timeout=timeout, name=name)
        try:
            return await self.request("tools/call", params, timeout=timeout, name=name,
                                      extra=await self._param_headers(name, arguments))
        except RpcError as exc:
            if exc.code != HEADER_MISMATCH:
                raise
        # The server refused the headers and did nothing: its tool changed. Read the
        # list again and send once more.
        self.headers_of = None
        return await self.request("tools/call", params, timeout=timeout, name=name,
                                  extra=await self._param_headers(name, arguments))

    async def _param_headers(self, name: str, arguments: dict[str, Any]) -> dict[str, str]:
        await self.ready()
        if self.era != MODERN_ERA:
            return {}
        if self.headers_of is None:
            self.usable(await self.list_tools())
        out: dict[str, str] = {}
        for path, header in (self.headers_of or {}).get(name, []):
            value: Any = arguments
            for step in path:
                value = value.get(step) if isinstance(value, dict) else None
            said = header_value(value)
            if said is not None:
                out[f"Mcp-Param-{header}"] = said
        return out

    def usable(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The tools that may be offered over this transport. Over HTTP in the newest
        revision a tool whose header marks break the protocol's rules is left out, and
        the marks of the others are remembered for calling them."""
        if self.kind != HTTP or self.era != MODERN_ERA:
            return tools
        kept, marks = [], {}
        for tool in tools:
            try:
                found = param_headers(tool.get("inputSchema"))
            except (ValueError, RecursionError):
                continue
            kept.append(tool)
            if found and isinstance(tool.get("name"), str):
                marks[tool["name"]] = found
        self.headers_of = marks
        return kept


def _result(reply: Reply) -> dict[str, Any]:
    message = reply.message or {}
    error = message.get("error")
    if isinstance(error, dict):
        raise RpcError(error.get("code"), error.get("message"), error.get("data"),
                       reply.status)
    result = message.get("result")
    if not isinstance(result, dict):
        raise Garbled("The server's answer made no sense.")
    return result


SESSIONS: dict[str, Session] = {}
_SESSIONS_LOCK = threading.Lock()


def session_for(provider_id: str, config: Config, http: Any = None) -> Session:
    """Rule ``mcp_one_process_per_provider``: the one session of a provider. A record or
    a saved value that changed ends the old one, and its program with it."""
    from jimmyhq.hands import mcp_http, mcp_stdio  # noqa: PLC0415 - they import this file

    with _SESSIONS_LOCK:
        held = SESSIONS.get(provider_id)
        if held is not None and held.signature != config.signature():
            held.close()
            held = None
        if held is None:
            transport = (mcp_stdio.StdioServer(config) if config.transport == STDIO
                         else mcp_http.HttpServer(config, http))
            held = SESSIONS[provider_id] = Session(config, transport)
        elif config.transport == HTTP:
            held.transport.http = http
        return held


def close(provider_id: str) -> None:
    with _SESSIONS_LOCK:
        held = SESSIONS.pop(provider_id, None)
    if held is not None:
        held.close()


def close_all() -> None:
    """Stop every server this process started. Runs when the process ends."""
    with _SESSIONS_LOCK:
        held = list(SESSIONS.values())
        SESSIONS.clear()
    for session in held:
        session.close()


atexit.register(close_all)


# --- the adapter ----------------------------------------------------------------------------

NOTHING_SENT = " Nothing was sent to the tool."


class McpProvider:
    """One MCP server as a hand provider. It lists tools and calls them: no more."""

    type = TYPE

    def __init__(self, record: ProviderRecord, wiring: Wiring):
        self.record = record
        self.problem: str | None = None
        self.session: Session | None = None
        self._secrets: list[str] = []
        try:
            config = read_config(record, wiring)
        except Unavailable as exc:
            self.problem = exc.sentence
            close(record.id)  # what it ran on is no longer what the owner saved
            return
        self._secrets = config.secrets()
        self.session = session_for(record.id, config, wiring.http)

    # --- words ------------------------------------------------------------------------

    def _redact(self, value: str) -> str:
        """Rule ``mcp_saved_values_never_echoed``: a saved value that a server sends
        back is taken out before anything else sees it."""
        for secret in self._secrets:
            value = value.replace(secret, REMOVED)
        return value

    def _said(self, exc: McpError) -> str:
        if isinstance(exc, RpcError):
            words = self._redact(clean(exc.message, 300)) or "it did not say why"
            return f"The server answered with an error: {words}"
        return exc.sentence

    async def _tools(self) -> list[Action]:
        if self.session is None:
            raise Unavailable(self.problem or "This MCP server is not set up.")
        session = self.session
        raw = session.usable(await session.list_tools())
        out, names = [], set()
        for tool in raw:
            action = to_action(tool)
            if action is None or action.name in names:
                continue  # unusable, or a second tool under a name already taken
            names.add(action.name)
            out.append(action)
        if len(out) < len(raw):
            log.warning("%d tools of an MCP server were left out: their definitions "
                        "could not be used", len(raw) - len(out))
        return out

    # --- the protocol of a provider ---------------------------------------------------

    async def check(self) -> Health:
        try:
            await self._tools()
        except McpError as exc:
            return Health(False, self._said(exc))
        return Health(True)

    async def connect(self, app: str | None) -> Connect:
        from jimmyhq.hands.service import NotReady  # noqa: PLC0415 - it imports providers

        try:
            await self._tools()
        except McpError as exc:
            raise NotReady("unreachable", self._said(exc)) from None
        return Connect("ready", account=(await self.accounts())[0])

    async def accounts(self) -> list[Account]:
        return [Account(id=ACCOUNT, label=self.record.label)]

    async def actions(self, account: Account) -> list[Action]:
        from jimmyhq.hands.service import NotReady  # noqa: PLC0415 - it imports providers

        try:
            return await self._tools()
        except McpError as exc:
            raise NotReady("unreachable", self._said(exc)) from None

    async def run(self, account: Account, name: str, arguments: dict[str, Any], *,
                  key: str) -> Outcome:
        """Rule ``mcp_one_call``: one ``tools/call`` with exactly these arguments.

        A failure the server stated, or one that happened before anything was sent, is
        returned as a failed outcome. A request that left and was not answered (the
        program stopped, the time ran out, the answer was too large or made no sense)
        is raised as ``Lost``: the core records that as "outcome unknown" for anything
        that is not a reading, and never tries it again."""
        if self.session is None:
            return Outcome(False, error=(self.problem or "This MCP server is not set up.")
                           + NOTHING_SENT)
        try:
            await self.session.ready()
        except McpError as exc:
            return Outcome(False, error=self._said(exc) + NOTHING_SENT)
        try:
            result = await self.session.call_tool(name, dict(arguments))
        except Unavailable as exc:
            return Outcome(False, error=exc.sentence + NOTHING_SENT)
        except (RpcError, HttpStatus) as exc:
            if exc.status >= 500:
                raise Lost("The server had a fault while the call was with it.") from None
            return Outcome(False, error=self._said(exc))
        return self.outcome(result)

    def outcome(self, result: dict[str, Any]) -> Outcome:
        """Rule ``mcp_result_is_data``: a tool's result as data for the core to shape.

        Text and structured content are passed on with hidden characters and saved
        values taken out. Images, sound and resources are named by kind and size and not
        passed on. A result marked as an error is a failed outcome, and so is one that
        asks for more input: this client gives a server nothing but the call."""
        kind = result.get("resultType", "complete")
        if kind == "input_required":
            return Outcome(False, error="The server asked for more input in the middle of "
                           "the call. JimmyHQ does not answer a server's questions, so the "
                           "call was not completed.")
        if kind != "complete":
            raise Garbled("The server's answer was of a kind this client does not know.")
        content = result.get("content")
        texts: list[str] = []
        others: list[dict[str, Any]] = []
        for block in (content if isinstance(content, list) else [])[:MAX_BLOCKS]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            else:
                others.append(_not_shown(block))

        def tidy(value: str) -> str:
            return self._redact(visible(value))

        text = tidy("\n".join(texts))
        if result.get("isError") is True:
            return Outcome(False, error=text[:ERROR_CHARS] or "The tool reported an error "
                           "and did not say why.")
        structured = _strings(result.get("structuredContent"), tidy)
        if structured is not None and _same(text, structured):
            text = ""  # the text is only the structured answer written out
        if structured is None and not others:
            return Outcome(True, data=text)
        if not text and not others:
            return Outcome(True, data=structured)
        data: dict[str, Any] = {}
        if text:
            data["text"] = text
        if structured is not None:
            data["structured"] = structured
        if others:
            data["not_shown"] = others
        return Outcome(True, data=data)


def _same(text: str, structured: Any) -> bool:
    if not text:
        return True
    try:
        return json.loads(text) == structured
    except (ValueError, RecursionError):
        return False


def _not_shown(block: dict[str, Any]) -> dict[str, Any]:
    """A block that is not text, by kind and size."""
    kind = clean(block.get("type"), 40) or "unknown"
    out: dict[str, Any] = {"kind": kind}
    source = block.get("resource") if kind == "resource" else block
    source = source if isinstance(source, dict) else {}
    mime = clean(source.get("mimeType"), 80)
    if mime:
        out["mime"] = mime
    data = source.get("data") if kind in ("image", "audio") else source.get("blob")
    if isinstance(data, str):
        out["bytes"] = len(data) * 3 // 4
    if kind == "resource" and isinstance(source.get("text"), str):
        out["chars"] = len(source["text"])
    if kind == "resource_link":
        out["name"] = clean(source.get("name"), 120)
        out["uri"] = clean(source.get("uri"), 300)
    return out


def make(record: ProviderRecord, wiring: Wiring) -> McpProvider:
    return McpProvider(record, wiring)


def register() -> ProviderType:
    """Make the "mcp" type available."""
    by_setting = "Empty uses the number under Settings, Integrations."
    return register_type(ProviderType(
        TYPE, "MCP server", make, needs_key=False,
        note="A program on this machine runs as you, with your files in reach. Add only "
             "a server you trust, and switch its tools on one at a time.",
        description="A tool server that speaks the Model Context Protocol: a program "
                    "on this machine, or an address. Each tool it lists is one action.",
        fields=(
            RecordField("base_url", "Address", where="record", only_for="address",
                        required=True,
                        help="The server's MCP endpoint, starting with https://. Leave "
                             "empty for a program on this machine."),
            RecordField("command", "Program", kind="path", only_for="program",
                        required=True,
                        help="The program alone, for example npx or the full path of "
                             "one. What follows it goes in the arguments."),
            RecordField("args", "Arguments", kind="argv", only_for="program",
                        help="One on each line, exactly as the program takes them. No "
                             "key or password here: save it as a secret and write "
                             "{secret:NAME} in its place."),
            RecordField("cwd", "Folder to run it in (optional)", kind="path",
                        only_for="program"),
            RecordField("env", "Environment variables it needs", kind="names",
                        secret_prefix="env_", only_for="program",  # noqa: S106 - a name
                        help="Names only, one on each line. Save each value as the "
                             "secret env_<name> in lower case."),
            RecordField("headers", "Headers to send", kind="names",
                        secret_prefix="header_", only_for="address",  # noqa: S106
                        help="Names only, one on each line. Save each value as the "
                             "secret header_<name> in lower case."),
            RecordField("start_timeout_s", "Seconds to wait for it to start (optional)",
                        kind="number", help=by_setting),
            RecordField("call_timeout_s", "Seconds to wait for one request (optional)",
                        kind="number", help=by_setting),
            RecordField("max_message_bytes", "Largest message, in bytes (optional)",
                        kind="number", help=by_setting),
            RecordField("max_tools", "Most tools listed (optional)", kind="number",
                        help=by_setting),
        ),
        tokens="here",
        tokens_note="There is no browser sign-in for an MCP server yet. The values it "
                    "needs (environment variables, a header) are kept in this machine's "
                    "secret store and given to the server, never written in its record.",
        # The protocol itself calls a server's hints untrusted. Never change this.
        trusted_hints=False))


def unregister() -> None:
    unregister_type(TYPE)
    close_all()


# Every mechanical rule of this adapter: (name, the option that holds its number, function).
RULES = [
    ("mcp_revision_agreed", "start_timeout_s", Session._negotiate),
    ("mcp_listing_is_bounded", "max_tools", Session.list_tools),
    ("mcp_one_process_per_provider", None, session_for),
    ("mcp_tool_text_is_data", None, to_action),
    ("mcp_tool_fingerprint", None, fingerprint),
    ("mcp_effect_is_a_suggestion", None, suggested_effect),
    ("mcp_one_call", "call_timeout_s", McpProvider.run),
    ("mcp_result_is_data", None, McpProvider.outcome),
    ("mcp_saved_values_never_echoed", None, McpProvider._redact),
]
