"""What every provider adapter shares: one HTTP client shape, one error type.

An adapter turns a ``ChatRequest`` into the provider's wire format and the
answer back into a ``RawReply``. It never decides routing, never writes to the
Tape, and never logs a key or a prompt.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from jimmyhq.models.shapes import (
    ChatRequest,
    ModelError,
    Provider,
    RawEmbed,
    RawReply,
    ToolCall,
)

# Things that look like credentials, for text that came back from a provider.
_SECRET_LOOKING = re.compile(
    r"(?i)\b(?:bearer\s+)?(?:sk|pk|rk|key|api|xai|gsk|AIza)[-_][A-Za-z0-9_\-*.]{6,}"
)
_DETAIL_MAX = 300
MAX_TOKEN_COUNT = 1_000_000_000  # per call
# Starts the id given to a tool call whose provider sent none. The caller needs
# one to answer the call; it is not sent back to a provider as if it were theirs.
MADE_UP_ID = "call-"
# What Anthropic and Bedrock both accept as a tool call id.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def signed(prefix: str, value: Any) -> str | None:
    """What an adapter stored in a tool call's ``signature``, if it starts with
    ``prefix``. A signature made by another provider (or type) is not ours to
    send, so it reads as none."""
    return value[len(prefix):] if isinstance(value, str) and value.startswith(prefix) else None


def safe_id(value: str, prefix: str) -> str:
    """A tool call id in a form the provider takes. One that already fits is
    sent as it is; any other (another provider's, after a fallback) becomes a
    fixed stand-in, so a call and its result, which share an id, still agree."""
    if _SAFE_ID.match(value):
        return value
    return prefix + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:24]


def mask(text: str, key: str | None = None) -> str:
    """Remove the key, and anything shaped like one, from text that may be shown."""
    if key:
        text = text.replace(key, "***")
    return _SECRET_LOOKING.sub("***", text)


@dataclass(frozen=True)
class Timeouts:
    connect: float
    read: float


_SENTENCES = {
    "unreachable": "{label} could not be reached. Check that it is running and that its "
                   "address is right.",
    "timeout": "{label} took too long to answer. Try again, or raise the waiting time under "
               "Settings, Models.",
    "auth": "{label} refused the key. Check the key saved for this provider.",
    "billing": "{label} says the account cannot be charged. Check billing with the provider.",
    "rate_limited": "{label} is rate limiting requests. Wait a little and try again.",
    "unavailable": "{label} is having trouble right now. Try again shortly.",
    "not_found": "{label} does not know that model or address. Check the model name and the "
                 "provider's address.",
    "redirected": "{label} answered with a redirect, which is not followed so the key cannot "
                  "be sent elsewhere. Use the final address as the provider's address.",
    "bad_reply": "{label} sent back something that could not be read.",
    "bad_request": "{label} rejected the request.",
    "truncated": "The answer from {label} was cut off before it finished. Ask for a longer "
                 "answer limit or a shorter reply.",
    "refused": "{label} declined to answer this request.",
    "missing_extra": "{label} needs the AWS library. Install it with "
                     "`uv sync --extra bedrock` and restart.",
    "no_embeddings": "{label} is a provider type that cannot make embeddings yet. Choose an "
                     "OpenAI or OpenAI-compatible provider for the embeddings role under "
                     "Settings, Models.",
    "no_tools": "{label} would not take a request that offers tools, or did not call one "
                "when told it must. The model or its server may not support tool calling, "
                "or being made to call a tool. Choose a model that does for this role under "
                "Settings, Models, or ask with tool_choice \"auto\".",
    "no_images": "{label} would not take a request with an image in it. The model may not "
                 "be able to see. Choose a model that can read images for this role under "
                 "Settings, Models.",
    "bad_tool_call": "{label} asked for a tool in a way that could not be read. Try again, "
                     "or choose a model that is better at calling tools for this role.",
}


class Adapter:
    """Base class. Subclasses fill in ``list_models``, ``chat`` and ``stream``."""

    native_json = False  # whether this provider type takes a JSON schema natively
    # Whether ``stream`` can carry tool calls. Where it cannot, the Brain asks
    # with ``chat`` instead and hands the text over in one piece.
    streams_tools = False
    default_base_url: str | None = None

    def __init__(self, provider: Provider, key: str | None, timeouts: Timeouts,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.provider = provider
        self.key = key
        self.timeouts = timeouts
        self._transport = transport

    # --- to implement ---------------------------------------------------------

    async def list_models(self) -> list[str]:
        raise NotImplementedError

    async def chat(self, request: ChatRequest) -> RawReply:
        raise NotImplementedError

    def stream(self, request: ChatRequest) -> AsyncIterator[str | RawReply]:
        """Text pieces as they arrive, then one ``RawReply`` with the whole text and usage.
        With ``streams_tools``, the ``RawReply`` also carries the tool calls."""
        raise NotImplementedError

    async def embed(self, model: str, texts: list[str]) -> RawEmbed:
        """One vector per text, in order. Provider types that cannot do this say so."""
        raise self.error("no_embeddings")

    # --- shared ---------------------------------------------------------------

    @property
    def wants_native_json(self) -> bool:
        chosen = self.provider.options.get("native_json")
        return self.native_json if chosen is None else bool(chosen)

    @property
    def base_url(self) -> str:
        url = (self.provider.base_url or self.default_base_url or "").rstrip("/")
        if not url:
            raise self.error("bad_request", detail="This provider has no address.")
        return url

    def headers(self) -> dict[str, str]:
        return {}

    def client(self) -> httpx.AsyncClient:
        # Redirects are never followed: a provider address is where the key is
        # sent, and a redirect could carry it to a different host.
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(self.timeouts.read, connect=self.timeouts.connect),
            follow_redirects=False,
            headers={"content-type": "application/json", **self.headers()},
        )

    def seal(self, kind: str) -> str:
        """What starts a signature this provider made. It names the provider,
        not only its type: sealed reasoning is only good with the provider that
        wrote it, and two providers can be the same type."""
        return f"{kind}{self.provider.id}:"

    def error(self, kind: str, *, status: int | None = None, detail: str = "",
              partial: str = "") -> ModelError:
        """``partial`` is the text of an answer that was cut off. It rides on the
        error for the caller and is in neither sentence."""
        public = _SENTENCES[kind].format(label=self.provider.label)
        detail = mask(detail.strip(), self.key)[:_DETAIL_MAX]
        message = f"{public} ({detail})" if detail else public
        return ModelError(kind, message, status=status, public=public, detail=detail,
                          partial=partial)

    def tool_call(self, index: int, call_id: Any, name: Any, arguments: Any,
                  signature: Any = None) -> ToolCall:
        """One tool call from a provider, checked. ``arguments`` may be the
        JSON text some providers send or the object others do; either way the
        result is an object, or the call is refused."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                raise self.error(
                    "bad_tool_call", detail="The arguments were not valid JSON.") from None
        elif arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise self.error("bad_tool_call", detail="The arguments were not a JSON object.")
        if not isinstance(name, str) or not name:
            raise self.error("bad_tool_call", detail="The tool had no name.")
        if not isinstance(call_id, str) or not call_id:
            # Random, so that two turns of one conversation never share an id.
            call_id = f"{MADE_UP_ID}{index}-{secrets.token_hex(4)}"
        return ToolCall(call_id, name, arguments,
                        signature if isinstance(signature, str) and signature else None)

    def from_status(self, response: httpx.Response, body: bytes) -> ModelError:
        status = response.status_code
        if status in (401, 403):
            kind = "auth"
        elif status == 402:
            kind = "billing"
        elif status == 404:
            kind = "not_found"
        elif status == 408:
            kind = "timeout"
        elif status == 429:
            kind = "rate_limited"
        elif 300 <= status < 400:
            kind = "redirected"
        elif status >= 500:
            kind = "unavailable"
        else:
            kind = "bad_request"
        return self.error(kind, status=status, detail=_detail(body))

    def from_transport(self, exc: Exception) -> ModelError:
        if isinstance(exc, httpx.ConnectTimeout | httpx.ConnectError | httpx.UnsupportedProtocol):
            return self.error("unreachable")
        if isinstance(exc, httpx.TimeoutException):
            return self.error("timeout")
        if isinstance(exc, httpx.InvalidURL):
            return self.error("bad_request", detail="The provider's address is not a valid URL.")
        return self.error("unreachable", detail=type(exc).__name__)

    async def get_json(self, url: str, params: dict | None = None) -> Any:
        try:
            async with self.client() as client:
                response = await client.get(url, params=params)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise self.from_transport(exc) from None
        return self._json(response)

    async def post_json(self, url: str, body: dict) -> Any:
        try:
            async with self.client() as client:
                response = await client.post(url, json=body)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise self.from_transport(exc) from None
        return self._json(response)

    def _json(self, response: httpx.Response) -> Any:
        if response.status_code != 200:
            raise self.from_status(response, response.content)
        try:
            data = response.json()
        except ValueError:
            raise self.error("bad_reply", status=200) from None
        if not isinstance(data, dict):
            raise self.error("bad_reply", status=200)
        return data

    async def post_sse(self, url: str, body: dict,
                       params: dict | None = None) -> AsyncIterator[tuple[str, Any]]:
        """POST and yield ``(event name, parsed data)`` for each server-sent event."""
        try:
            async with self.client() as client, client.stream(
                "POST", url, json=body, params=params
            ) as response:
                if response.status_code != 200:
                    raise self.from_status(response, await response.aread())
                event = ""
                seen = False
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        seen = True
                        if data == "[DONE]":
                            return
                        try:
                            parsed = json.loads(data)
                        except ValueError:
                            raise self.error("bad_reply", status=200) from None
                        yield event, parsed
                    elif not line:
                        event = ""
                if not seen:
                    # A 200 that carried no events at all is not a stream:
                    # an error page, or a server that ignored the request.
                    raise self.error("bad_reply", status=200)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise self.from_transport(exc) from None


def _detail(body: bytes) -> str:
    """The provider's own explanation, if the error body has one."""
    text = body.decode("utf-8", errors="replace")
    with contextlib.suppress(ValueError):
        data = json.loads(text)
        if isinstance(data, list) and data:
            data = data[0]
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            return data["message"]
        return ""
    return text if len(text) < 200 and "<" not in text else ""


def integer(value: Any) -> int:
    """A token count from a provider, which may be missing or oddly typed."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    if not math.isfinite(value) or value < 0:
        return 0
    # A count no real call could reach is a provider's mistake, and it must
    # not be able to swamp the usage totals for good.
    return min(int(value), MAX_TOKEN_COUNT)
