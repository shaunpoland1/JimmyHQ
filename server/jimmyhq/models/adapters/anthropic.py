"""The Anthropic Messages API, over plain HTTP.

The project takes no new dependencies, so this talks to the API directly
rather than through the SDK. Wire format checked against the Messages, Models
and streaming references for API version 2026-10-21:

- ``POST /v1/messages`` and ``GET /v1/models`` with ``x-api-key`` and
  ``anthropic-version``.
- Effort is ``output_config.effort``; a JSON schema is ``output_config.format``.
- ``temperature`` is not sent: that API version rejects it on every model.
- Thinking is left to the model's default, since which settings a model
  accepts varies by model and models are not named in this code.

Tools and images, from the tool use and vision guides for the same version:

- ``tools`` is a list of ``{"name", "description", "input_schema"}``.
  ``tool_choice`` is ``{"type": "auto" | "any" | "none"}`` or
  ``{"type": "tool", "name"}``; "auto" is the default and is left out. Some
  models answer a forced choice ("any" or "tool") with a 400.
- The model answers with ``tool_use`` blocks (``id``, ``name``, ``input``) and
  the stop reason ``tool_use``. Results go back in the next user message as
  ``tool_result`` blocks (``tool_use_id``, ``content``, ``is_error``), which
  must come before any other block; their content may hold text and images.
- An image is ``{"type": "image", "source": {"type": "base64", "media_type",
  "data"}}``.
- A model that thinks returns ``thinking`` blocks before its tool calls, and
  the thinking guide requires them back, unmodified, with the tool results.
  So the whole content of an answer that calls tools is kept in the first
  call's ``signature`` and sent back as it was.
- Streamed, each content block opens with ``content_block_start`` (carrying
  the block, a ``tool_use`` one with an empty ``input``) and is filled by
  ``content_block_delta``: ``text_delta``, ``input_json_delta`` (pieces of
  the input as JSON text), ``thinking_delta`` and ``signature_delta``. The
  blocks are put back together here so the same whole answer can be kept.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from jimmyhq.models.adapters.base import Adapter, integer, safe_id, signed
from jimmyhq.models.shapes import ChatRequest, RawReply, ToolCall

API_VERSION = "2026-10-21"
_SIGNED = "anthropic:"
_EFFORT = {"none": "low", "minimal": "low"}  # the lowest level this API has is "low"
_STREAM_ERRORS = {
    "overloaded_error": "unavailable", "api_error": "unavailable",
    "rate_limit_error": "rate_limited", "authentication_error": "auth",
    "permission_error": "auth", "invalid_request_error": "bad_request",
    "not_found_error": "not_found", "billing_error": "billing",
}


class AnthropicAdapter(Adapter):
    native_json = True
    streams_tools = True
    default_base_url = "https://api.anthropic.com"

    def headers(self) -> dict[str, str]:
        headers = {"anthropic-version": API_VERSION}
        if self.key:
            headers["x-api-key"] = self.key
        return headers

    async def list_models(self) -> list[str]:
        found: list[str] = []
        params: dict[str, Any] = {"limit": 1000}
        for _ in range(20):  # a bound, in case a server never says it is done
            data = await self.get_json(f"{self.base_url}/v1/models", params)
            items = data.get("data")
            if not isinstance(items, list):
                raise self.error("bad_reply", status=200)
            found += [str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")]
            if not data.get("has_more") or not data.get("last_id"):
                break
            params = {"limit": 1000, "after_id": data["last_id"]}
        return sorted(set(found))

    @staticmethod
    def _image(part: dict) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "base64", "media_type": part["media_type"],
                                            "data": part["data"]}}

    def _blocks(self, parts: list[dict]) -> list[dict[str, Any]]:
        """One turn that is more than text, as content blocks."""
        calls = [p for p in parts if p["type"] == "tool_call"]
        was = signed(self.seal(_SIGNED), calls[0].get("signature")) if calls else None
        if was:
            # The answer exactly as this API gave it, thinking included.
            try:
                original = json.loads(was)
            except ValueError:
                original = None
            if isinstance(original, list) and [
                b.get("id") for b in original
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ] == [p["id"] for p in calls]:
                return original
        blocks: list[dict[str, Any]] = []
        for part in parts:
            kind = part["type"]
            if kind == "text":
                if part["text"]:
                    blocks.append({"type": "text", "text": part["text"]})
            elif kind == "image":
                blocks.append(self._image(part))
            elif kind == "tool_call":
                blocks.append({"type": "tool_use", "id": safe_id(part["id"], "toolu_"),
                               "name": part["name"], "input": part["arguments"]})
            else:
                block: dict[str, Any] = {"type": "tool_result",
                                         "tool_use_id": safe_id(part["tool_call_id"], "toolu_")}
                if part["images"]:
                    block["content"] = (
                        [{"type": "text", "text": part["text"]}] if part["text"] else []
                    ) + [self._image(image) for image in part["images"]]
                elif part["text"]:
                    block["content"] = part["text"]
                if part["is_error"]:
                    block["is_error"] = True
                blocks.append(block)
        return blocks

    def body(self, request: ChatRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": role,
                 "content": content if isinstance(content, str) else self._blocks(content)}
                for role, content in request.messages],
        }
        if request.system:
            body["system"] = request.system
        if request.tools:
            body["tools"] = [{"name": tool["name"], "description": tool["description"],
                              "input_schema": tool["parameters"]} for tool in request.tools]
            choice = request.tool_choice
            if isinstance(choice, dict):
                body["tool_choice"] = {"type": "tool", "name": choice["name"]}
            elif choice != "auto":
                body["tool_choice"] = {"type": "any" if choice == "required" else "none"}
        output: dict[str, Any] = {}
        if request.effort:
            output["effort"] = _EFFORT.get(request.effort, request.effort)
        if request.json_schema is not None and self.wants_native_json:
            output["format"] = {"type": "json_schema", "schema": request.json_schema}
        if output:
            body["output_config"] = output
        return body

    def _stop(self, reason: Any, so_far: str = "") -> None:
        if reason in ("max_tokens", "model_context_window_exceeded"):
            raise self.error("truncated", partial=so_far)
        if reason == "refusal":
            raise self.error("refused")

    @staticmethod
    def _input_tokens(usage: dict) -> int:
        # Cached tokens are reported apart from the rest; all three are input.
        return (integer(usage.get("input_tokens"))
                + integer(usage.get("cache_creation_input_tokens"))
                + integer(usage.get("cache_read_input_tokens")))

    async def chat(self, request: ChatRequest) -> RawReply:
        data = await self.post_json(f"{self.base_url}/v1/messages", self.body(request))
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise self.error("bad_reply", status=200)
        text = "".join(str(b.get("text") or "") for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text")
        self._stop(data.get("stop_reason"), text)
        calls: tuple[ToolCall, ...] = ()
        if request.tools:
            used = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
            calls = tuple(self.tool_call(i, b.get("id"), b.get("name"), b.get("input"))
                          for i, b in enumerate(used))
            if calls:
                kept = self.seal(_SIGNED) + json.dumps(blocks, ensure_ascii=False)
                calls = (replace(calls[0], signature=kept), *calls[1:])
        usage = data.get("usage") or {}
        return RawReply(text, self._input_tokens(usage), integer(usage.get("output_tokens")),
                        calls)

    async def stream(self, request: ChatRequest) -> AsyncIterator[str | RawReply]:
        body = self.body(request) | {"stream": True}
        parts: list[str] = []
        tokens_in = tokens_out = 0
        blocks: dict[int, dict[str, Any]] = {}  # the answer's blocks, when tools are offered
        inputs: dict[int, str] = {}  # each tool call's input, as the JSON text so far
        async for _, event in self.post_sse(f"{self.base_url}/v1/messages", body):
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if request.tools:
                self._rebuild(blocks, inputs, event)
            if kind == "message_start":
                usage = (event.get("message") or {}).get("usage") or {}
                tokens_in = self._input_tokens(usage)
                tokens_out = integer(usage.get("output_tokens"))
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    parts.append(delta["text"])
                    yield delta["text"]
            elif kind == "message_delta":
                usage = event.get("usage") or {}
                tokens_out = integer(usage.get("output_tokens")) or tokens_out
                if usage.get("input_tokens") is not None:  # cumulative when present
                    tokens_in = self._input_tokens(usage)
                self._stop((event.get("delta") or {}).get("stop_reason"), "".join(parts))
            elif kind == "error":
                error = event.get("error") or {}
                raise self.error(_STREAM_ERRORS.get(error.get("type"), "unavailable"),
                                 detail=str(error.get("message", "")))
        calls: tuple[ToolCall, ...] = ()
        if request.tools:
            whole = [blocks[i] for i in sorted(blocks)]
            for index, text in inputs.items():
                if index in blocks:
                    # Checked and parsed by ``tool_call`` below; kept parsed.
                    blocks[index]["input"] = self.tool_call(index, "x", "x", text).arguments
            used = [b for b in whole if b.get("type") == "tool_use"]
            calls = tuple(self.tool_call(i, b.get("id"), b.get("name"), b.get("input"))
                          for i, b in enumerate(used))
            if calls:
                kept = self.seal(_SIGNED) + json.dumps(whole, ensure_ascii=False)
                calls = (replace(calls[0], signature=kept), *calls[1:])
        yield RawReply("".join(parts), tokens_in, tokens_out, calls)

    @staticmethod
    def _rebuild(blocks: dict[int, dict[str, Any]], inputs: dict[int, str],
                 event: dict) -> None:
        """Apply one stream event to the content blocks being put back together."""
        index = event.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            return
        if event.get("type") == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict):
                blocks[index] = dict(block)
                if block.get("type") == "tool_use":
                    inputs[index] = ""
        elif event.get("type") == "content_block_delta" and index in blocks:
            delta = event.get("delta") or {}
            block = blocks[index]
            for kind, field in (("text_delta", "text"), ("thinking_delta", "thinking"),
                                ("signature_delta", "signature")):
                if delta.get("type") == kind and isinstance(delta.get(field), str):
                    block[field] = str(block.get(field) or "") + delta[field]
            if delta.get("type") == "input_json_delta" and index in inputs:
                inputs[index] += str(delta.get("partial_json") or "")
