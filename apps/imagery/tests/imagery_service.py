"""Stand-ins for the image services inside the fake core: answers in the shapes the services'
own documentation shows, and a record of every request. Nothing reaches the network.

The key is the made-up one every test in the repo uses. Pictures are tiny, drawn here.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from imagery import png
from jimmyhq_app import CallError

KEY = "sk-test-SECRET-0123456789"
GEMINI_HOST = "generativelanguage.googleapis.com"
OWN_HOST = "images.example.com"


def tiny_png(shade: int = 90, size: int = 4) -> bytes:
    """A real little PNG; a different ``shade`` gives different bytes."""
    row = bytes([shade % 256, 120, 200]) * size
    return png.write_png(size, size, [row] * size)


def tiny_jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 20 + b"\xff\xd9"


def reply(status: int = 200, data: Any = None, text: str | None = None) -> dict[str, Any]:
    body = text if text is not None else ("" if data is None else json.dumps(data))
    return {"status": status, "headers": {"content-type": "application/json"}, "text": body}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- Gemini, in the shape of generateContent's reference ----------------------------------


def gemini_image(data: bytes | None = None, *, mime: str = "image/png", words: str = "",
                 snake: bool = False) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if words:
        parts.append({"text": words})
    blob = {"mime_type" if snake else "mimeType": mime, "data": b64(data or tiny_png())}
    parts.append({"inline_data" if snake else "inlineData": blob})
    return reply(200, {
        "candidates": [{"index": 0, "content": {"role": "model", "parts": parts},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 1290,
                          "totalTokenCount": 1299},
        "modelVersion": "gemini-3.1-flash-image", "responseId": "r1"})


def gemini_blocked_prompt(reason: str = "PROHIBITED_CONTENT") -> dict[str, Any]:
    return reply(200, {"promptFeedback": {"blockReason": reason, "safetyRatings": []},
                       "usageMetadata": {"promptTokenCount": 9}})


def gemini_finished(reason: str, words: str = "") -> dict[str, Any]:
    parts = [{"text": words}] if words else []
    return reply(200, {"candidates": [{"index": 0, "content": {"role": "model", "parts": parts},
                                       "finishReason": reason}]})


def gemini_error(code: int, status: str, message: str, details: list | None = None
                 ) -> dict[str, Any]:
    return reply(code, {"error": {"code": code, "message": message, "status": status,
                                  "details": details or []}})


def no_answer(message: str = "generativelanguage.googleapis.com could not be reached, so "
              "nothing came back. Try again later.") -> CallError:
    return CallError("unavailable", message)


def owner_wait_ceiling(seconds: int = 20, host: str = GEMINI_HOST) -> CallError:
    """JimmyHQ's refusal when the owner's own wait limit ended a slow request, in the
    words ``bridge.FetchLimits.too_slow`` uses."""
    return CallError("unavailable", (
        f"{host} took longer than {seconds} seconds to answer, the most the owner lets an "
        "app wait (\"Longest wait for an app's network request (seconds)\", "
        "apps.net_fetch_timeout_s), so nothing came back. Your manifest expects to wait up "
        "to 180 seconds for it. Try again later, or ask the owner to raise it under "
        "Settings, Apps."))


def own_wait_ended(seconds: int = 60, host: str = GEMINI_HOST) -> CallError:
    """JimmyHQ's refusal when the wait the app itself asked for (its timeout setting) ran out."""
    return CallError("unavailable", (
        f"{host} took longer than {seconds} seconds, the wait you asked for, to answer. "
        "Try again later, or ask for a longer wait."))


def owner_size_ceiling(kb: int = 1024, host: str = GEMINI_HOST) -> CallError:
    """JimmyHQ's refusal when an answer was larger than the owner's limit."""
    return CallError("too_large", (
        f"The answer from {host} is larger than the {kb} KB the owner lets an app be given "
        "(\"Largest network answer for an app (KB)\", apps.net_fetch_max_kb), so none of "
        "it was returned. Your manifest expects up to 20480 KB from "
        f"{host}. Ask for less at a time, or ask the owner to raise it under Settings, Apps."))


def not_added(host: str = "192.0.2.10:8000") -> CallError:
    """JimmyHQ's refusal for an address on the owner's own network he has not added."""
    return CallError("not_allowed", (
        "Your app \"imagery\" has not been granted that host, so nothing was sent. The "
        "hosts it may reach are: generativelanguage.googleapis.com. For an address on the "
        "owner's own network, the owner adds it himself under \"Addresses on your own "
        "network\" on your app's page under Apps; tell him which in your settings."))


# --- an OpenAI-style server --------------------------------------------------------------


def own_image(data: bytes | None = None) -> dict[str, Any]:
    return reply(200, {"created": 1_800_000_000, "data": [{"b64_json": b64(data or tiny_png())}]})


def own_error(status: int, message: str, code: str = "", kind: str = "") -> dict[str, Any]:
    return reply(status, {"error": {"message": message, "type": kind, "code": code}})


class Service:
    """Scripts the fake core's net.fetch: ``on(url, *answers)`` gives the answers in turn,
    the last repeating. A URL with no answer gets none (a timeout) and is kept in
    ``unscripted``."""

    def __init__(self, core: Any):
        self.core = core
        self.unscripted: list[str] = []

    def on(self, url: str, *answers: Any) -> None:
        queue = list(answers)

        def answer(args: dict[str, Any]) -> Any:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        self.core.responses[url] = answer

    @property
    def sent(self) -> list[dict[str, Any]]:
        return self.core.fetched
