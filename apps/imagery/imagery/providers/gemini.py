"""Gemini: Google's image models, through the Gemini API's ``generateContent`` method.

Every request goes through JimmyHQ's ``net.fetch`` to exactly
``generativelanguage.googleapis.com``, which the manifest asks for, with the owner's key
from the app's Settings page in the ``x-goog-api-key`` header. With no key the provider
says so and nothing is sent. This file is the only one that knows Gemini's address, fields
and error words.

What was read, and where (Google's own pages, read 23 November 2026; see docs/imagery.md for
what could not be confirmed):

- https://aistudio.google.com/docs/image-generation.md?generate-content=true (the image
  guide's generateContent variant). Its REST examples: ``POST
  .../models/<model>:generateContent`` with header ``x-goog-api-key``, body
  ``{"contents": [{"parts": [{"text": ...}]}]}``; to edit, a second part
  ``{"inline_data": {"mime_type": "image/jpeg", "data": "<base64>"}}`` after the text;
  ``"generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig":
  {"aspectRatio": "1:1", "imageSize": "1K"}}``; sizes in capital K (``1K`` ``2K`` ``4K``;
  ``512`` on one model only); up to 14 reference images (10 objects and 4 characters on the
  newer models); every image carries a SynthID watermark.
- https://aistudio.google.com/api-reference/generate-content (the reference): the response
  is ``{"candidates": [{"content": {"parts": [...]}, "finishReason": ...}],
  "promptFeedback": {"blockReason": ...}}``; a part may hold ``inlineData`` with
  ``mimeType`` and ``data``; the image-related finish reasons are ``IMAGE_SAFETY``,
  ``IMAGE_PROHIBITED_CONTENT``, ``IMAGE_OTHER``, ``NO_IMAGE`` and ``IMAGE_RECITATION``;
  ``blockReason`` is one of ``SAFETY``, ``OTHER``, ``BLOCKLIST``, ``PROHIBITED_CONTENT``,
  ``IMAGE_SAFETY``, and when it is set no candidates come back; ``imageConfig.aspectRatio``
  is one of 1:1 1:4 4:1 1:8 8:1 2:3 3:2 3:4 4:3 4:5 5:4 9:16 16:9 21:9.
- https://aistudio.google.com/docs/api-errors.md?generate-content=true: 400
  INVALID_ARGUMENT and FAILED_PRECONDITION, 402 and 429 RESOURCE_EXHAUSTED, 403
  PERMISSION_DENIED, 404 NOT_FOUND, 499 CANCELLED, 500 INTERNAL, 503 UNAVAILABLE, 504
  DEADLINE_EXCEEDED; the body is ``{"error": {"code", "message", "status", "details"}}``.

Google now calls generateContent its legacy API and recommends the newer Interactions API
for new work; the pages above are still current for generateContent. This provider uses
generateContent because its request and its answer are fully documented; moving to the
other would change only this file.

Both spellings of a part's field names are read (``inlineData``/``inline_data``,
``mimeType``/``mime_type``), since the guide writes one and the reference the other.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from jimmyhq_app import CallError

from imagery import png
from imagery.providers import Made, ProviderError, Request
from imagery.providers.net import clip, post_json, scrub
from imagery.rules import clean_aspects

HOST = "generativelanguage.googleapis.com"
BASE = f"https://{HOST}/v1beta"
KEY_HEADER = "x-goog-api-key"
LABEL = "Gemini"
# What the manifest's ``net_expects`` says of this host: answers up to about 20 MB, a wait
# of up to 3 minutes. The same numbers are in jimmyhq.app.json (a test keeps them equal).
EXPECT_KB = 20480
EXPECT_S = 180
# The shapes the reference lists. A shape outside it is not offered with Gemini.
ASPECTS = ("1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4",
           "9:16", "16:9", "21:9")
SECRET = "gemini_key"  # noqa: S105 - the name of the secret, not a value
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\Z")

NO_KEY = ("Imagery has no Gemini key, so nothing was sent. Make one in Google AI Studio and "
          "type it in on Imagery's Settings page under Apps.")
# finishReason and blockReason values that mean the words or the picture were refused.
REFUSED = frozenset({
    "SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST",
    "SPII", "IMAGE_RECITATION", "RECITATION", "OTHER", "IMAGE_OTHER"})


def refused_sentence(reason: str) -> str:
    return (f"Gemini would not make this picture (its reason: {reason}), so nothing was "
            "made. Its safety rules decide this, not Imagery. Change the words and try "
            "again.")


def build_body(request: Request, cfg: Any) -> dict[str, Any]:
    """The request body, as the guide's REST examples write it."""
    parts: list[dict[str, Any]] = [{"text": request.prompt}]
    if request.reference is not None:
        # JimmyHQ puts the file's base64 in where the marker is: the bytes never pass here.
        parts.append({"inline_data": {"mime_type": request.reference.mime,
                                      "data": {"$file": request.reference.ref,
                                               "as": "base64"}}})
    image: dict[str, Any] = {"aspectRatio": request.aspect}
    if str(cfg.gemini_image_size) != "default":
        image["imageSize"] = str(cfg.gemini_image_size)
    return {"contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                 "imageConfig": image}}


def _field(part: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in part:
            return part[name]
    return None


def read_answer(data: Any, key: str | None = None) -> Made:
    """The picture in a 200 answer, or a ``ProviderError`` that says why there is none."""
    if not isinstance(data, dict):
        raise ProviderError("other", ("Gemini answered, but not in a form Imagery can read, "
                                      "so nothing was made. Try again."))
    feedback = data.get("promptFeedback") or {}
    reason = str(feedback.get("blockReason") or "")
    candidates = data.get("candidates") or []
    if reason and not candidates:
        raise ProviderError("refused", refused_sentence(reason))
    if not candidates:
        raise ProviderError("other", ("Gemini answered with no picture and no reason, so "
                                      "nothing was made. Try again, or change the words."))
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = (first.get("content") or {}).get("parts") or []
    words: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        blob = _field(part, "inlineData", "inline_data")
        if isinstance(blob, dict) and blob.get("data"):
            try:
                raw = base64.b64decode(str(blob["data"]), validate=False)
            except (binascii.Error, ValueError):
                raise ProviderError("other", ("Gemini sent a picture that could not be "
                                              "read, so nothing was made. Try again.")) \
                    from None
            kind = png.kind_of(raw[:16])
            if kind is None:
                raise ProviderError("other", ("Gemini sent a file that is not a PNG, JPEG "
                                              "or WebP picture, so nothing was kept. Try "
                                              "again."))
            return Made(raw, png.TYPES[kind], clip(" ".join(words), 200))
        if isinstance(part.get("text"), str) and not part.get("thought"):
            words.append(part["text"])
    finish = str(first.get("finishReason") or "")
    if finish in REFUSED:
        raise ProviderError("refused", refused_sentence(finish))
    said = clip(scrub(" ".join(words), key), 200)
    more = f" It said: \"{said}\"." if said else ""
    why = f" (finish reason {finish})" if finish and finish not in ("STOP", "NO_IMAGE") else ""
    raise ProviderError("other", (
        f"Gemini answered with words and no picture{why}, so nothing was made.{more} "
        "Describe what the picture should show, rather than asking a question."))


def read_failure(status: int, data: Any, model: str, key: str | None) -> ProviderError:
    """What an HTTP status other than 200 means, in the owner's words."""
    error = (data or {}).get("error") if isinstance(data, dict) else None
    error = error if isinstance(error, dict) else {}
    said = clip(scrub(error.get("message"), key), 220)
    word = str(error.get("status") or "")
    low = said.lower() + " " + str(error.get("details") or "").lower()
    if status in (401, 403) or ("api key" in low and status == 400) or "api_key_invalid" in low:
        return ProviderError("key", (
            "Gemini did not accept the key, or the key may not use image models, so nothing "
            "was made. Check the key on Imagery's Settings page, and that its Google project "
            "may use Gemini image models."
            + (f" Google said: {said}" if said and status == 403 else "")))
    if status in (402, 429) or word == "RESOURCE_EXHAUSTED":
        return ProviderError("quota", (
            "Gemini says this key is over its quota or out of credit, so nothing was made. "
            "Image models often have no free quota: check that billing is on for the key's "
            "Google project, or wait and try again."))
    if status == 404 or word == "NOT_FOUND":
        return ProviderError("setup", (
            f"Gemini does not know the model \"{model}\", so nothing was made. Change "
            "\"Gemini model\" in Imagery's settings to one Google lists for image "
            "generation."))
    if status in (408, 499, 504) or word == "DEADLINE_EXCEEDED":
        return ProviderError("timeout", (
            "Gemini took too long to make the picture, so nothing was made. Try again, or "
            "ask for a smaller picture size."))
    if status >= 500:
        return ProviderError("busy", (
            "Gemini is busy or having trouble right now, so nothing was made. Try again in a "
            "minute."))
    if word == "FAILED_PRECONDITION":
        return ProviderError("setup", (
            "Gemini says this key's project cannot use the service yet (often because "
            "billing is off or the service is not offered where you are), so nothing was "
            f"made. {said}"))
    return ProviderError("other", (
        f"Gemini did not accept the request (error {status}), so nothing was made."
        + (f" It said: {said}" if said else "")))


class Gemini:
    id = "gemini"
    label = "Gemini (Google)"
    outside = True
    supports_reference = True

    def __init__(self, app: Any):
        self.app = app

    def hosts(self, cfg: Any) -> tuple[str, ...]:
        return (HOST,)

    def _key(self) -> str | None:
        try:
            return self.app.secrets.get(SECRET) or None
        except CallError:
            return None

    def problem(self, cfg: Any) -> str | None:
        if not self._key():
            return NO_KEY
        if not _MODEL.match(str(cfg.gemini_model).strip()):
            return ("Type a Gemini model name in Imagery's settings, such as the one Google "
                    "lists for image generation: letters, digits, dots, dashes and "
                    "underscores only. Nothing was sent.")
        return None

    def aspects(self, cfg: Any) -> list[str]:
        return [a for a in clean_aspects(cfg.aspects) if a in ASPECTS]

    def model(self, cfg: Any) -> str:
        return str(cfg.gemini_model).strip()

    def make(self, request: Request, cfg: Any) -> Made:
        key = self._key()
        if not key:
            raise ProviderError("key", NO_KEY, sent=False)
        problem = self.problem(cfg)
        if problem:
            raise ProviderError("setup", problem, sent=False)
        if request.aspect not in ASPECTS:
            raise ProviderError("setup", (
                f"Gemini does not make pictures in the shape {request.aspect}, so nothing "
                "was sent. Choose another shape."), sent=False)
        model = self.model(cfg)
        answer = post_json(
            self.app, f"{BASE}/models/{model}:generateContent",
            headers={KEY_HEADER: key, "Content-Type": "application/json"},
            body=build_body(request, cfg), timeout_s=int(cfg.timeout_s), who=HOST,
            secret=key, need_kb=EXPECT_KB, need_s=EXPECT_S)
        if answer.status != 200:
            raise read_failure(answer.status, answer.data, model, key)
        return read_answer(answer.data, key)
