"""AWS Bedrock through the Converse API, using boto3 from the optional extra.

Credentials are never handled here: boto3 finds them itself from the named
profile (or its usual chain), so no access key ever reaches the Tape or the
secret store. Request and response shapes checked against the Converse and
ConverseStream API references.

Tools and images, from the same references (ContentBlock, ToolConfiguration,
ToolChoice, ToolResultBlock, ImageBlock):

- ``toolConfig.tools`` is a list of ``{"toolSpec": {"name", "description",
  "inputSchema": {"json": schema}}}``. ``toolConfig.toolChoice`` is
  ``{"auto": {}}`` (the default, left out), ``{"any": {}}`` or
  ``{"tool": {"name"}}``. There is no "none": tools are left out instead,
  which only works while the conversation has no tool calls in it.
- The model answers with ``toolUse`` blocks (``toolUseId``, ``name``,
  ``input``) and the stop reason ``tool_use``. A result goes back in the next
  user message as ``{"toolResult": {"toolUseId", "content": [{"text"} |
  {"image"}], "status"}}``; ``status`` is sent only for an error, since not
  every model takes the field.
- An image is ``{"image": {"format": "png" | "jpeg" | "webp", "source":
  {"bytes": ...}}}``. boto3 takes the raw bytes and encodes them itself.
- A model that reasons returns ``reasoningContent`` blocks before its tool
  calls and wants them back unchanged with the results. So the whole content
  of an answer that calls tools is kept in the first call's ``signature`` and
  sent back as it was.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

from jimmyhq.models.adapters.base import Adapter, Timeouts, integer, safe_id, signed
from jimmyhq.models.shapes import ChatRequest, ModelError, Provider, RawReply, ToolCall

_CODES = {
    "ThrottlingException": "rate_limited", "ModelNotReadyException": "rate_limited",
    "TooManyRequestsException": "rate_limited",
    "AccessDeniedException": "auth", "UnrecognizedClientException": "auth",
    "ExpiredTokenException": "auth", "InvalidSignatureException": "auth",
    "ValidationException": "bad_request",
    "ResourceNotFoundException": "not_found",
    "ModelTimeoutException": "timeout",
}
_STREAM_ERRORS = {
    "throttlingException": "rate_limited", "validationException": "bad_request",
    "internalServerException": "unavailable", "modelStreamErrorException": "unavailable",
    "serviceUnavailableException": "unavailable",
}
_SIGNED = "bedrock:"
_BYTES = "__bytes__"  # how bytes inside a kept block are written as JSON


def _pack(blocks: list) -> str:
    return json.dumps(
        blocks, ensure_ascii=False,
        default=lambda raw: {_BYTES: base64.b64encode(bytes(raw)).decode("ascii")})


def _bytes_back(node: Any) -> Any:
    """``node`` with each bytes value that ``_pack`` wrote turned back into bytes."""
    if isinstance(node, dict):
        if set(node) == {_BYTES}:
            return base64.b64decode(node[_BYTES], validate=True)
        return {key: _bytes_back(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_bytes_back(item) for item in node]
    return node


def _unpack(text: str) -> Any:
    """The blocks ``_pack`` wrote. What a model put in a tool call's ``input``
    is its own, and can look like anything, so bytes are only restored
    elsewhere."""
    try:
        blocks = json.loads(text)
        if not isinstance(blocks, list):
            return None
        return [
            block | {"toolUse": {key: value if key == "input" else _bytes_back(value)
                                 for key, value in block["toolUse"].items()}}
            if isinstance(block, dict) and isinstance(block.get("toolUse"), dict)
            else _bytes_back(block)
            for block in blocks]
    except (ValueError, TypeError):
        return None


_TRUNCATED = {"max_tokens", "model_context_window_exceeded"}
_REFUSED = {"guardrail_intervened", "content_filtered"}

# (service name, provider, timeouts) -> a boto3 client. Replaced in tests.
ClientFactory = Callable[[str, Provider, Timeouts], Any]


def boto_client(service: str, provider: Provider, timeouts: Timeouts) -> Any:
    try:
        import boto3  # noqa: PLC0415 - optional extra, imported only when used
        from botocore.config import Config  # noqa: PLC0415
    except ImportError:
        raise ModelError(
            "missing_extra",
            f"{provider.label} needs the AWS library. Install it with "
            "`uv sync --extra bedrock` and restart.",
        ) from None
    session = boto3.Session(profile_name=provider.profile or None,
                            region_name=provider.region or None)
    return session.client(service, config=Config(
        connect_timeout=timeouts.connect, read_timeout=timeouts.read,
        retries={"max_attempts": 1},
    ))


class BedrockAdapter(Adapter):
    native_json = False  # asked for in the prompt and checked; schemas vary by model family

    def __init__(self, provider: Provider, key: str | None, timeouts: Timeouts,
                 transport: Any = None, client_factory: ClientFactory | None = None):
        super().__init__(provider, key, timeouts, transport)
        self._factory = client_factory or boto_client

    def _client(self, service: str) -> Any:
        try:
            return self._factory(service, self.provider, self.timeouts)
        except ModelError:
            raise
        except Exception as exc:  # noqa: BLE001 - boto raises its own family of errors
            raise self._translate(exc) from None

    def _translate(self, exc: Exception) -> ModelError:
        name = type(exc).__name__
        response = getattr(exc, "response", None)
        if isinstance(response, dict):  # botocore ClientError
            error = response.get("Error") or {}
            status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            kind = _CODES.get(str(error.get("Code")), "unavailable")
            return self.error(kind, status=status, detail=str(error.get("Message", "")))
        if name in ("NoCredentialsError", "PartialCredentialsError", "ProfileNotFound",
                    "TokenRetrievalError", "SSOTokenLoadError", "UnauthorizedSSOTokenError"):
            return self.error("auth", detail="AWS credentials were not found for this profile.")
        if name in ("EndpointConnectionError", "ConnectTimeoutError", "ConnectionClosedError",
                    "NoRegionError"):
            return self.error("unreachable")
        if name == "ReadTimeoutError":
            return self.error("timeout")
        return self.error("unavailable", detail=name)

    async def _call(self, fn: Callable[..., Any], **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, **kwargs)
        except ModelError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from None

    async def list_models(self) -> list[str]:
        client = self._client("bedrock")
        data = await self._call(client.list_foundation_models)
        items = data.get("modelSummaries") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise self.error("bad_reply")
        return sorted({str(m["modelId"]) for m in items if isinstance(m, dict)
                       and m.get("modelId")})

    @staticmethod
    def _image(part: dict) -> dict[str, Any]:
        return {"image": {"format": part["media_type"].removeprefix("image/"),
                          "source": {"bytes": base64.b64decode(part["data"])}}}

    def _blocks(self, parts: list[dict]) -> list[dict[str, Any]]:
        """One turn that is more than text, as content blocks."""
        calls = [p for p in parts if p["type"] == "tool_call"]
        was = signed(self.seal(_SIGNED), calls[0].get("signature")) if calls else None
        original = _unpack(was) if was else None
        if isinstance(original, list) and [
            b["toolUse"].get("toolUseId") for b in original
            if isinstance(b, dict) and isinstance(b.get("toolUse"), dict)
        ] == [p["id"] for p in calls]:
            return original  # the answer exactly as it came, reasoning included
        blocks: list[dict[str, Any]] = []
        for part in parts:
            kind = part["type"]
            if kind == "text":
                if part["text"]:
                    blocks.append({"text": part["text"]})
            elif kind == "image":
                blocks.append(self._image(part))
            elif kind == "tool_call":
                blocks.append({"toolUse": {
                    "toolUseId": safe_id(part["id"], "tooluse_"), "name": part["name"],
                    "input": part["arguments"]}})
            else:
                content = ([{"text": part["text"]}] if part["text"] else []
                           ) + [self._image(image) for image in part["images"]]
                result: dict[str, Any] = {
                    "toolUseId": safe_id(part["tool_call_id"], "tooluse_"),
                    # An empty list is refused; say there was nothing.
                    "content": content or [{"text": "(no output)"}],
                }
                if part["is_error"]:
                    result["status"] = "error"
                blocks.append({"toolResult": result})
        return blocks

    def arguments(self, request: ChatRequest) -> dict[str, Any]:
        config: dict[str, Any] = {"maxTokens": request.max_tokens}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        args: dict[str, Any] = {
            "modelId": request.model,
            "messages": [
                {"role": role,
                 "content": [{"text": content}] if isinstance(content, str)
                 else self._blocks(content)}
                for role, content in request.messages],
            "inferenceConfig": config,
        }
        if request.system:
            args["system"] = [{"text": request.system}]
        choice = request.tool_choice
        if request.tools and choice == "none":
            if request.has_tool_parts:
                raise self.error(
                    "no_tools",
                    detail="This provider cannot be told to call no tool once the "
                           "conversation has tool calls in it.")
        elif request.tools:
            tools: dict[str, Any] = {"tools": [{"toolSpec": {
                "name": tool["name"], "description": tool["description"],
                "inputSchema": {"json": tool["parameters"]}}} for tool in request.tools]}
            if isinstance(choice, dict):
                tools["toolChoice"] = {"tool": {"name": choice["name"]}}
            elif choice == "required":
                tools["toolChoice"] = {"any": {}}
            args["toolConfig"] = tools
        return args

    def _stop(self, reason: Any, so_far: str = "") -> None:
        if reason in _TRUNCATED:
            raise self.error("truncated", partial=so_far)
        if reason in _REFUSED:
            raise self.error("refused")
        if reason == "malformed_tool_use":
            raise self.error("bad_tool_call")
        if reason == "malformed_model_output":
            raise self.error("bad_reply")

    async def chat(self, request: ChatRequest) -> RawReply:
        client = self._client("bedrock-runtime")
        data = await self._call(client.converse, **self.arguments(request))
        try:
            blocks = data["output"]["message"]["content"]
        except (KeyError, TypeError):
            raise self.error("bad_reply") from None
        text = "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict))
        self._stop(data.get("stopReason"), text)
        calls: tuple[ToolCall, ...] = ()
        if request.tools:
            used = [b["toolUse"] for b in blocks
                    if isinstance(b, dict) and isinstance(b.get("toolUse"), dict)]
            calls = tuple(self.tool_call(i, u.get("toolUseId"), u.get("name"), u.get("input"))
                          for i, u in enumerate(used))
            if calls:
                kept = self.seal(_SIGNED) + _pack(blocks)
                calls = (replace(calls[0], signature=kept), *calls[1:])
        usage = data.get("usage") or {}
        return RawReply(text, integer(usage.get("inputTokens")),
                        integer(usage.get("outputTokens")), calls)

    async def stream(self, request: ChatRequest) -> AsyncIterator[str | RawReply]:
        # Text only. A request that offers tools is not sent here: the Brain
        # sees ``streams_tools`` is False and uses ``chat`` (see ``Brain._leg``).
        client = self._client("bedrock-runtime")
        data = await self._call(client.converse_stream, **self.arguments(request))
        events = iter(data.get("stream") or ())
        done = object()
        parts: list[str] = []
        usage: dict = {}
        while True:
            # boto3's stream is blocking, so each step runs off the event loop.
            event = await self._next(events, done)
            if event is done:
                break
            if not isinstance(event, dict):
                continue
            for name, kind in _STREAM_ERRORS.items():
                if name in event:
                    raise self.error(kind, detail=str((event[name] or {}).get("message", "")))
            if "contentBlockDelta" in event:
                piece = (event["contentBlockDelta"].get("delta") or {}).get("text")
                if isinstance(piece, str) and piece:
                    parts.append(piece)
                    yield piece
            elif "messageStop" in event:
                self._stop(event["messageStop"].get("stopReason"), "".join(parts))
            elif "metadata" in event:
                usage = event["metadata"].get("usage") or {}
        yield RawReply("".join(parts), integer(usage.get("inputTokens")),
                       integer(usage.get("outputTokens")))

    async def _next(self, events: Any, done: object) -> Any:
        try:
            return await asyncio.to_thread(next, events, done)
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from None
