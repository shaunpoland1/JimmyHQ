"""The Gemini API (Google AI), over plain HTTP.

Wire format checked against the generateContent, streamGenerateContent and
models.list references: ``POST {base}/models/{model}:generateContent``,
``POST ...:streamGenerateContent?alt=sse``, ``GET {base}/models``. The key is
sent in the ``x-goog-api-key`` header rather than the ``key`` query parameter
the examples use, so it can never end up in a logged address.

Tools and images, from the same reference (Tool, ToolConfig, Part, Blob):

- ``tools`` is ``[{"functionDeclarations": [{"name", "description",
  "parametersJsonSchema"}]}]``. ``toolConfig.functionCallingConfig.mode`` is
  ``AUTO`` (the default, left out), ``ANY`` or ``NONE``; ``ANY`` with
  ``allowedFunctionNames`` names one function.
- The model answers with ``functionCall`` parts (``name``, ``args``, sometimes
  ``id``). Such a part may carry a ``thoughtSignature``, which is sent back
  with the call unchanged. The first call of a step that has none (it came
  from another provider) is sent with the placeholder the thought signatures
  guide documents for that case, ``skip_thought_signature_validator``. A
  result goes back in a ``user`` turn as a ``functionResponse`` part
  (``name``, ``response`` object, ``id`` when the call had one); images a tool
  returned ride in its ``parts`` as ``inlineData``.
- An image is ``{"inlineData": {"mimeType", "data"}}`` with base64 data.
- Streamed, a ``functionCall`` part arrives whole in one chunk, signature
  included, so the calls are simply collected from each chunk as it passes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from jimmyhq.models.adapters.base import MADE_UP_ID, Adapter, integer, signed
from jimmyhq.models.shapes import ChatRequest, RawReply, ToolCall

_LEVEL = {"none": "MINIMAL", "minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM",
          "high": "HIGH", "xhigh": "XHIGH", "max": "XHIGH"}
_REFUSED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE",
            "IMAGE_SAFETY"}
_USABLE = {"generateContent", "embedContent"}
_BAD_CALL = {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}
_SIGNED = "google:"
# What the thought signatures guide says to send for a function call the API did not
# write itself, such as one made by another provider: the first call of each step
# must carry a signature, and this value is accepted in place of one.
_NO_SIGNATURE = "skip_thought_signature_validator"


class GoogleAdapter(Adapter):
    native_json = True
    streams_tools = True
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.key} if self.key else {}

    async def list_models(self) -> list[str]:
        found: list[str] = []
        params: dict[str, Any] = {"pageSize": 1000}
        for _ in range(20):
            data = await self.get_json(f"{self.base_url}/models", params)
            items = data.get("models")
            if not isinstance(items, list):
                raise self.error("bad_reply", status=200)
            for item in items:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                methods = item.get("supportedGenerationMethods")
                if methods is None or _USABLE & set(methods):
                    found.append(str(item["name"]).removeprefix("models/"))
            if not data.get("nextPageToken"):
                break
            params = {"pageSize": 1000, "pageToken": data["nextPageToken"]}
        return sorted(set(found))

    def _url(self, request: ChatRequest, method: str) -> str:
        model = quote(request.model.removeprefix("models/"), safe="")
        return f"{self.base_url}/models/{model}:{method}"

    @staticmethod
    def _image(part: dict) -> dict[str, Any]:
        return {"inlineData": {"mimeType": part["media_type"], "data": part["data"]}}

    def _parts(self, parts: list[dict]) -> list[dict[str, Any]]:
        """One turn that is more than text, as parts."""
        out: list[dict[str, Any]] = []
        first_call = True
        for part in parts:
            kind = part["type"]
            if kind == "text":
                if part["text"]:
                    out.append({"text": part["text"]})
            elif kind == "image":
                out.append(self._image(part))
            elif kind == "tool_call":
                call: dict[str, Any] = {"name": part["name"], "args": part["arguments"]}
                if not part["id"].startswith(MADE_UP_ID):
                    call["id"] = part["id"]
                sent: dict[str, Any] = {"functionCall": call}
                signature = signed(_SIGNED, part.get("signature"))
                if signature:
                    sent["thoughtSignature"] = signature
                elif first_call:
                    sent["thoughtSignature"] = _NO_SIGNATURE
                first_call = False
                out.append(sent)
            else:
                answer: dict[str, Any] = {
                    "name": part["name"],
                    "response": {"error" if part["is_error"] else "output": part["text"]},
                }
                if not part["tool_call_id"].startswith(MADE_UP_ID):
                    answer["id"] = part["tool_call_id"]
                if part["images"]:
                    answer["parts"] = [self._image(image) for image in part["images"]]
                out.append({"functionResponse": answer})
        return out

    def body(self, request: ChatRequest) -> dict[str, Any]:
        config: dict[str, Any] = {"maxOutputTokens": request.max_tokens}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.effort:
            config["thinkingConfig"] = {"thinkingLevel": _LEVEL.get(request.effort, "MEDIUM")}
        if request.json_schema is not None and self.wants_native_json:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = request.json_schema
        body: dict[str, Any] = {
            "contents": [
                {"role": "model" if role == "assistant" else "user",
                 "parts": [{"text": content}] if isinstance(content, str)
                 else self._parts(content)}
                for role, content in request.messages
            ],
            "generationConfig": config,
        }
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        if request.tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": tool["name"], "description": tool["description"],
                 "parametersJsonSchema": tool["parameters"]} for tool in request.tools]}]
            choice = request.tool_choice
            if isinstance(choice, dict):
                body["toolConfig"] = {"functionCallingConfig": {
                    "mode": "ANY", "allowedFunctionNames": [choice["name"]]}}
            elif choice != "auto":
                body["toolConfig"] = {"functionCallingConfig": {
                    "mode": "ANY" if choice == "required" else "NONE"}}
        return body

    def _calls(self, data: dict) -> tuple[ToolCall, ...]:
        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            return ()
        parts = (candidates[0].get("content") or {}).get("parts") or []
        asked = [p for p in parts
                 if isinstance(p, dict) and isinstance(p.get("functionCall"), dict)]
        return tuple(
            self.tool_call(
                i, p["functionCall"].get("id"), p["functionCall"].get("name"),
                p["functionCall"].get("args"),
                _SIGNED + p["thoughtSignature"]
                if isinstance(p.get("thoughtSignature"), str) and p["thoughtSignature"] else None)
            for i, p in enumerate(asked))

    def _read(self, data: dict) -> tuple[str, Any]:
        """The visible text in one response, and why it finished (if it has)."""
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise self.error("refused")
        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            return "", None
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and not p.get("thought"))
        return text, candidates[0].get("finishReason")

    def _finish(self, reason: Any, so_far: str = "") -> None:
        if reason == "MAX_TOKENS":
            raise self.error("truncated", partial=so_far)
        if reason in _REFUSED:
            raise self.error("refused")
        if reason in _BAD_CALL:
            raise self.error("bad_tool_call")
        if reason == "MISSING_THOUGHT_SIGNATURE":
            raise self.error(
                "bad_request",
                detail="An earlier tool call in the conversation lacks the signature this "
                       "model gave it. Pass tool calls back exactly as they were returned.")

    @staticmethod
    def _usage(text: str, usage: dict, calls: tuple[ToolCall, ...] = ()) -> RawReply:
        # Thinking tokens are billed as output but counted apart.
        out = integer(usage.get("candidatesTokenCount")) + integer(usage.get("thoughtsTokenCount"))
        return RawReply(text, integer(usage.get("promptTokenCount")), out, calls)

    async def chat(self, request: ChatRequest) -> RawReply:
        data = await self.post_json(self._url(request, "generateContent"), self.body(request))
        if "candidates" not in data and "promptFeedback" not in data:
            raise self.error("bad_reply", status=200)
        text, reason = self._read(data)
        self._finish(reason, text)
        calls = self._calls(data) if request.tools else ()
        return self._usage(text, data.get("usageMetadata") or {}, calls)

    async def stream(self, request: ChatRequest) -> AsyncIterator[str | RawReply]:
        parts: list[str] = []
        usage: dict = {}
        calls: tuple[ToolCall, ...] = ()
        url = self._url(request, "streamGenerateContent")
        async for _, chunk in self.post_sse(url, self.body(request), params={"alt": "sse"}):
            if not isinstance(chunk, dict):
                continue
            if isinstance(chunk.get("error"), dict):
                raise self.error("unavailable", detail=str(chunk["error"].get("message", "")))
            if isinstance(chunk.get("usageMetadata"), dict):
                usage = chunk["usageMetadata"]
            text, reason = self._read(chunk)
            if text:
                parts.append(text)
                yield text
            self._finish(reason, "".join(parts))
            if request.tools:
                calls += self._calls(chunk)
        yield self._usage("".join(parts), usage, calls)
