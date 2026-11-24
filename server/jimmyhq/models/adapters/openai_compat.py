"""OpenAI's Chat Completions API, and everything that speaks it.

Covers the hosted OpenAI API and any OpenAI-compatible server (Ollama, LM
Studio, vLLM, OpenRouter, a self-hosted gateway). Wire format checked against
the Chat Completions, Models and Embeddings references:
``POST {base}/chat/completions``, ``GET {base}/models``, ``POST {base}/embeddings``,
``Authorization: Bearer``.

Tools and images, from the same reference: ``tools`` is a list of
``{"type": "function", "function": {"name", "description", "parameters"}}``;
``tool_choice`` is ``"none"``, ``"auto"``, ``"required"`` or
``{"type": "function", "function": {"name"}}``. The model answers with
``message.tool_calls`` (``id``, ``function.name``, ``function.arguments`` as
JSON text) and ``content`` may be null. A result goes back as a ``tool`` role
message with ``tool_call_id``, right after the assistant message. A tool
message takes text only, so an image a tool returned follows in a user message.
An image is an ``image_url`` part whose ``url`` is a base64 data URL.
``tool_choice`` is left out when it is "auto", the default, because some
compatible servers do not know the field.

Streamed, a tool call arrives in pieces under ``delta.tool_calls``: each piece
has an ``index``; the first for an index carries ``id`` and ``function.name``,
and ``function.arguments`` is JSON text to be joined in order. Some compatible
servers send a whole call in one piece, and some leave ``index`` out.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from typing import Any

from jimmyhq.models.adapters.base import Adapter, integer
from jimmyhq.models.shapes import ChatRequest, RawEmbed, RawReply, ToolCall

MAX_DIMENSIONS = 16_384  # no embeddings model is near this; a longer vector is a mistake


class OpenAICompatibleAdapter(Adapter):
    # Compatible servers differ too much to assume they take a JSON schema;
    # the owner can switch it on per provider.
    native_json = False
    # ``max_tokens`` is what local servers understand.
    max_tokens_field = "max_tokens"
    streams_tools = True

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.key}"} if self.key else {}

    async def list_models(self) -> list[str]:
        data = await self.get_json(f"{self.base_url}/models")
        items = data.get("data")
        if not isinstance(items, list):
            raise self.error("bad_reply", status=200)
        return sorted({str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")})

    @staticmethod
    def _image(part: dict) -> dict[str, Any]:
        return {"type": "image_url",
                "image_url": {"url": f"data:{part['media_type']};base64,{part['data']}"}}

    def _turn(self, role: str, parts: list[dict]) -> list[dict[str, Any]]:
        """One turn that is more than text, as the messages it takes here."""
        text = "".join(p["text"] for p in parts if p["type"] == "text")
        if role == "assistant":
            calls = [{"id": p["id"], "type": "function",
                      "function": {"name": p["name"],
                                   "arguments": json.dumps(p["arguments"], ensure_ascii=False)}}
                     for p in parts if p["type"] == "tool_call"]
            message: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = calls
            return [message]
        out: list[dict[str, Any]] = []
        rest: list[dict[str, Any]] = []
        for part in parts:
            if part["type"] == "tool_result":
                shown = part["text"]
                if part["is_error"]:
                    shown = f"Error: {shown}" if shown else "Error"
                out.append({"role": "tool", "tool_call_id": part["tool_call_id"],
                            "content": shown})
                if part["images"]:
                    rest.append({"type": "text",
                                 "text": f"The tool {part['name']} returned this:"})
                    rest += [self._image(image) for image in part["images"]]
            elif part["type"] == "image":
                rest.append(self._image(part))
            elif part["text"]:
                rest.append({"type": "text", "text": part["text"]})
        if rest:
            out.append({"role": "user", "content": rest})
        return out

    def body(self, request: ChatRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for role, content in request.messages:
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            else:
                messages += self._turn(role, content)
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        body: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            self.max_tokens_field: request.max_tokens,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.effort:
            body["reasoning_effort"] = request.effort
        if request.json_schema is not None and self.wants_native_json:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": request.json_schema, "strict": True},
            }
        if request.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": tool["name"], "description": tool["description"],
                "parameters": tool["parameters"]}} for tool in request.tools]
            choice = request.tool_choice
            if isinstance(choice, dict):
                body["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
            elif choice != "auto":
                body["tool_choice"] = choice
        return body

    def _calls(self, message: dict) -> tuple[ToolCall, ...]:
        found = message.get("tool_calls")
        if not isinstance(found, list):
            return ()
        calls = []
        for index, item in enumerate(found):
            function = item.get("function") if isinstance(item, dict) else None
            if not isinstance(function, dict):
                raise self.error("bad_tool_call", detail="A tool call had no function.")
            calls.append(self.tool_call(index, item.get("id"), function.get("name"),
                                        function.get("arguments")))
        return tuple(calls)

    def _finish(self, reason: Any, so_far: Any = "") -> None:
        if reason == "length":
            raise self.error("truncated", partial=so_far if isinstance(so_far, str) else "")
        if reason == "content_filter":
            raise self.error("refused")

    async def chat(self, request: ChatRequest) -> RawReply:
        data = await self.post_json(f"{self.base_url}/chat/completions", self.body(request))
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise self.error("bad_reply", status=200)
        message = choices[0].get("message") or {}
        self._finish(choices[0].get("finish_reason"), message.get("content"))
        calls = self._calls(message) if request.tools else ()
        text = message.get("content")
        if text is None and calls:
            text = ""
        if not isinstance(text, str):
            if message.get("refusal"):
                raise self.error("refused")
            raise self.error("bad_reply", status=200)
        usage = data.get("usage") or {}
        return RawReply(text, integer(usage.get("prompt_tokens")),
                        integer(usage.get("completion_tokens")), calls)

    async def stream(self, request: ChatRequest) -> AsyncIterator[str | RawReply]:
        body = self.body(request) | {"stream": True, "stream_options": {"include_usage": True}}
        parts: list[str] = []
        usage: dict = {}
        asked: dict[int, dict[str, Any]] = {}  # tool calls being put together, by index
        async for _, chunk in self.post_sse(f"{self.base_url}/chat/completions", body):
            if not isinstance(chunk, dict):
                continue
            if isinstance(chunk.get("error"), dict | str):  # some servers report errors inline
                detail = chunk["error"]
                detail = detail.get("message", "") if isinstance(detail, dict) else detail
                raise self.error("unavailable", detail=str(detail))
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    parts.append(piece)
                    yield piece
                if request.tools:
                    self._gather(asked, delta.get("tool_calls"))
                self._finish(choice.get("finish_reason"), "".join(parts))
        calls = tuple(self.tool_call(position, call["id"], call["name"], call["arguments"])
                      for position, (_, call) in enumerate(sorted(asked.items())))
        yield RawReply("".join(parts), integer(usage.get("prompt_tokens")),
                       integer(usage.get("completion_tokens")), calls)

    def _gather(self, asked: dict[int, dict[str, Any]], pieces: Any) -> None:
        """Add one chunk's pieces of tool calls to those gathered so far."""
        if not isinstance(pieces, list):
            return
        for position, piece in enumerate(pieces):
            function = piece.get("function") if isinstance(piece, dict) else None
            if not isinstance(function, dict):
                raise self.error("bad_tool_call", detail="A tool call had no function.")
            index = piece.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                # No index: a server that sends each call whole, one after another.
                index = len(asked) if piece.get("id") or function.get("name") else position
            call = asked.setdefault(index, {"id": None, "name": None, "arguments": ""})
            if isinstance(piece.get("id"), str) and piece["id"]:
                call["id"] = piece["id"]
            if isinstance(function.get("name"), str) and function["name"]:
                call["name"] = function["name"]
            more = function.get("arguments")
            if isinstance(more, str):
                call["arguments"] += more
            elif isinstance(more, dict):
                call["arguments"] = json.dumps(more)

    async def embed(self, model: str, texts: list[str]) -> RawEmbed:
        """``POST {base}/embeddings`` with ``{"model", "input": [...]}``.

        The answer is ``{"data": [{"index", "embedding": [...]}, ...], "usage":
        {"prompt_tokens", "total_tokens"}}``. ``encoding_format`` is left at its
        default (a list of floats), which every compatible server understands.
        The answer is put back in the order asked, by ``index``, and refused
        unless it has exactly one usable vector per text, all the same length.
        """
        data = await self.post_json(f"{self.base_url}/embeddings",
                                    {"model": model, "input": texts})
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise self.error("bad_reply", status=200)
        vectors: list[list[float] | None] = [None] * len(texts)
        for position, item in enumerate(items):
            if not isinstance(item, dict):
                raise self.error("bad_reply", status=200)
            index = item.get("index", position)
            vector = item.get("embedding")
            if (isinstance(index, bool) or not isinstance(index, int)
                    or not 0 <= index < len(texts) or vectors[index] is not None
                    or not isinstance(vector, list)
                    or not 0 < len(vector) <= MAX_DIMENSIONS):
                raise self.error("bad_reply", status=200)
            if not all(isinstance(x, int | float) and not isinstance(x, bool)
                       and math.isfinite(x) for x in vector):
                raise self.error("bad_reply", status=200)
            vectors[index] = [float(x) for x in vector]
        if len({len(v) for v in vectors if v is not None}) != 1:
            raise self.error("bad_reply", status=200)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        tokens = integer(usage.get("prompt_tokens")) or integer(usage.get("total_tokens"))
        return RawEmbed([v for v in vectors if v is not None], tokens)


class OpenAIAdapter(OpenAICompatibleAdapter):
    """The hosted OpenAI API: same wire format, current field names."""

    native_json = True
    default_base_url = "https://api.openai.com/v1"
    # ``max_tokens`` is deprecated there and rejected by reasoning models.
    max_tokens_field = "max_completion_tokens"
