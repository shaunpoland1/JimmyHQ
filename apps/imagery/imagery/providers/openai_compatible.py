"""Your own server: an images endpoint at an address the owner sets, answering the way
OpenAI's image service does.

``POST {base}/images/generations`` with ``{"model", "prompt", "n", "size"}`` and, when the
owner leaves it on, ``"response_format": "b64_json"``; the answer is ``{"data": [{"b64_json":
"..."}]}``. The address is one of two kinds, and JimmyHQ lets a request through to exactly
that and no other:

- **A public https address** (``https://images.example.com/v1``): a host the owner added under
  Your own addresses, with its key typed beside it.
- **An address on the owner's own network** (``http://192.0.2.10:8000/v1``, or https on a
  port other than 443): reached once the owner adds that exact scheme, host and port under
  "Addresses on your own network" on Imagery's page in Apps. Imagery cannot ask whether he
  has (no call tells an app), so it sends, and a refusal from JimmyHQ becomes a sentence that
  says where to add it; nothing was sent and the request is not counted. Its key, if it
  needs one, is the one "Key for a server on your own network".

What was read (OpenAI's API reference for ``POST /v1/images/generations``, at
https://developers.openai.com/api/reference/resources/images, 23 November 2026): ``model``,
``prompt``, ``n`` (1 to 10), ``size`` (``1024x1024``, ``1536x1024`` and ``1024x1536`` work
across its image models), ``response_format`` (``url`` or ``b64_json``; its newer image
models do not take it and always answer with base64); the answer is ``{"created", "data":
[{"b64_json"}]}``. That page does not describe an error body. This provider reads the shape
OpenAI-style servers use, ``{"error": {"message", "type", "code"}}``, and also a plain
``{"error": "..."}`` or ``{"detail": "..."}``, and treats that as a guess.

There is no starting picture here. OpenAI's own pages describe ``/images/edits`` in more
than one shape (multipart with ``image[]`` or ``image``, and JSON with ``images`` holding
``image_url`` or ``file_id``), and say nothing of other servers taking either, so there is
no one documented shape to build on. See docs/imagery.md.

An answer that holds only an address (``url``) is not followed: the host is not one the
owner agreed to, and JimmyHQ would refuse it. The owner is told to make the server answer
with the picture itself.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from jimmyhq_app import CallError
from jimmyhq_app.keybind import KeyBinding

from imagery import png
from imagery.config import COMPATIBLE_SECRET, OWN_NETWORK_SECRET
from imagery.providers import Made, ProviderError, Request
from imagery.providers.net import clip, post_json, scrub
from imagery.rules import parse_sizes

LABEL = "Your own server"
MOVED_KEY = ("The key for a server on your own network was typed for {was}, and Imagery's "
             "settings now name {address}, so the key was not sent and nothing was sent. If "
             "you meant to change the address, clear \"Key for a server on your own network\" "
             "under Settings, Apps, Imagery, press Make once so Imagery sees it is gone, and "
             "type the key again.")
# Words an OpenAI-style server uses when it turns a description away.
REFUSAL_WORDS = ("content_policy_violation", "moderation_blocked", "safety", "content_filter",
                 "policy")


class Target(NamedTuple):
    host: str  # the name or address, lower case, with no brackets
    base: str  # what Imagery adds /images/generations to, with no trailing slash
    origin: str  # scheme://host[:port] as the owner types it under Addresses on your own network
    own_net: bool  # an address on the owner's own network, not a public https host


def parse_base(text: str) -> Target | str:
    """What the owner typed as a ``Target``, or the sentence that says what is wrong with
    it. https on the standard port is a public host the owner adds under Your own addresses;
    http, or https on another port, is an address on his own network."""
    raw = str(text or "").strip()
    if not raw:
        return ("Type your server's address in Imagery's settings, such as "
                "https://images.example.com/v1 or, on your own network, "
                "http://192.0.2.10:8000/v1. Nothing was sent.")
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        host, port, parts = "", None, urlsplit("")
    if parts.scheme not in ("http", "https") or not host:
        return ("Your server's address must start with https:// or http:// and name a host, "
                "such as https://images.example.com/v1 or, on your own network, "
                "http://192.0.2.10:8000/v1. Nothing was sent.")
    if parts.username or parts.password or parts.query or parts.fragment:
        return ("Your server's address must have no sign-in part, question mark or #. "
                "Nothing was sent. Put a key in the key field instead.")
    path = parts.path.rstrip("/")
    if parts.scheme == "https" and port in (None, 443):
        return Target(host, f"https://{host}{path}", f"https://{host}", False)
    shown = f"[{host}]" if ":" in host else host
    origin = f"{parts.scheme}://{shown}" + (f":{port}" if port else "")
    return Target(host, f"{origin}{path}", origin, True)


class OpenAICompatible:
    id = "openai_compatible"
    label = "Your own server (OpenAI style)"
    outside = True
    supports_reference = False

    def __init__(self, app: Any):
        self.app = app
        self.bind = KeyBinding(app, "own_network")

    def _granted(self) -> list[str]:
        try:
            return [h.lower() for h in self.app.hosts.list().hosts]
        except CallError:
            return []

    def hosts(self, cfg: Any) -> tuple[str, ...]:
        found = parse_base(str(cfg.openai_base))
        return () if isinstance(found, str) or found.own_net else (found.host,)

    def problem(self, cfg: Any) -> str | None:
        found = parse_base(str(cfg.openai_base))
        if isinstance(found, str):
            return found
        if not found.own_net and found.host not in self._granted():
            return (f"Add {found.host} under Your own addresses on Imagery's Settings page, "
                    "so JimmyHQ lets Imagery reach it. Nothing was sent.")
        if not str(cfg.openai_model).strip():
            return "Type the model name your server expects in Imagery's settings."
        if not parse_sizes(cfg.openai_sizes):
            return ("Imagery's settings list no size your server takes. Add a line such as "
                    "1:1=1024x1024.")
        return None

    def aspects(self, cfg: Any) -> list[str]:
        return list(parse_sizes(cfg.openai_sizes))

    def model(self, cfg: Any) -> str:
        return str(cfg.openai_model).strip()

    def _key(self, target: Target) -> str | None:
        """The key for this address and no other. A public host's key is the one typed beside
        it, which JimmyHQ keeps for that host alone (``host=``). JimmyHQ keeps no key for an
        address on the owner's own network, so the one typed for it is sent only to the
        address it was first sent to (``KeyBinding``), read again at every request: if the
        address in the settings changes, the key stays behind until the owner clears it and
        types it again."""
        try:
            if not target.own_net:
                return self.app.secrets.get(COMPATIBLE_SECRET, host=target.host) or None
            key = self.app.secrets.get(OWN_NETWORK_SECRET) or None
        except CallError:
            return None
        if key is None:
            self.bind.forget()
            return None
        if not self.bind.allows(key, target.origin):
            raise ProviderError("setup", MOVED_KEY.format(
                address=target.origin, was=self.bind.bound_to() or "another address"),
                sent=False)
        return key

    def make(self, request: Request, cfg: Any) -> Made:
        problem = self.problem(cfg)
        if problem:
            raise ProviderError("setup", problem, sent=False)
        found = parse_base(str(cfg.openai_base))
        assert not isinstance(found, str)  # problem() said it reads
        who = found.origin if found.own_net else found.host
        size = parse_sizes(cfg.openai_sizes).get(request.aspect)
        if not size:
            raise ProviderError("setup", (
                f"Imagery's settings give no size for the shape {request.aspect}, so nothing "
                "was sent. Choose another shape, or add a line such as "
                f"{request.aspect}=1024x1024 to \"Sizes your server takes\"."), sent=False)
        key = self._key(found)
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body: dict[str, Any] = {"model": self.model(cfg), "prompt": request.prompt, "n": 1,
                                "size": size}
        if cfg.openai_ask_base64:
            body["response_format"] = "b64_json"
        answer = post_json(self.app, f"{found.base}/images/generations", headers=headers,
                           body=body, timeout_s=int(cfg.timeout_s), who=who, secret=key,
                           own_address=found.origin if found.own_net else None)
        if answer.status != 200:
            raise read_failure(answer.status, answer.data, who, key, own_net=found.own_net)
        return read_answer(answer.data, who)


def read_answer(data: Any, host: str) -> Made:
    """The picture in a 200 answer, or a ``ProviderError`` that says why there is none."""
    rows = data.get("data") if isinstance(data, dict) else None
    first = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if first is None:
        raise ProviderError("other", (f"{host} answered, but with no picture Imagery can "
                                      "read, so nothing was made. Try again."))
    if first.get("b64_json"):
        try:
            raw = base64.b64decode(str(first["b64_json"]), validate=False)
        except (binascii.Error, ValueError):
            raise ProviderError("other", (f"{host} sent a picture that could not be read, "
                                          "so nothing was made. Try again.")) from None
        kind = png.kind_of(raw[:16])
        if kind is None:
            raise ProviderError("other", (f"{host} sent a file that is not a PNG, JPEG or "
                                          "WebP picture, so nothing was kept. Try again."))
        return Made(raw, png.TYPES[kind])
    if first.get("url"):
        raise ProviderError("setup", (
            f"{host} answered with an address for the picture instead of the picture itself, "
            "and JimmyHQ cannot fetch it from another host, so nothing was made. Turn on "
            "\"Ask your server for the picture itself\" in Imagery's advanced settings, or "
            "set your server to answer with base64."))
    raise ProviderError("other", (f"{host} answered with no picture, so nothing was made. "
                                  "Try again."))


def read_failure(status: int, data: Any, host: str, key: str | None,
                 own_net: bool = False) -> ProviderError:
    """What an HTTP status other than 200 means, in the owner's words."""
    said, code = "", ""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            said = str(error.get("message") or "")
            code = f"{error.get('code') or ''} {error.get('type') or ''}".lower()
        elif isinstance(error, str):
            said = error
        elif isinstance(data.get("detail"), str):
            said = data["detail"]
    said = clip(scrub(said, key), 220)
    if any(word in code for word in REFUSAL_WORDS):
        return ProviderError("refused", (
            f"{host} would not make this picture, so nothing was made."
            + (f" It said: {said}" if said else "") + " Change the words and try again."))
    if status in (401, 403):
        return ProviderError("key", (
            f"{host} did not accept the key, so nothing was made. "
            + ("Check \"Key for a server on your own network\" on Imagery's Settings page."
               if own_net else "Check the key typed beside its address under Your own "
                             "addresses on Imagery's Settings page.")))
    if status in (402, 429):
        return ProviderError("quota", (
            f"{host} says you are over its limit or out of credit, so nothing was made. "
            "Wait and try again, or check your account there."
            + (f" It said: {said}" if said else "")))
    if status == 404:
        return ProviderError("setup", (
            f"{host} has no images endpoint at that address, or does not know the model, so "
            "nothing was made. Check \"Your server's address\" and \"Your server's model\" "
            "in Imagery's settings." + (f" It said: {said}" if said else "")))
    if status in (408, 504):
        return ProviderError("timeout", (
            f"{host} took too long, so nothing was made. Try again."))
    if status >= 500:
        return ProviderError("busy", (
            f"{host} is busy or having trouble, so nothing was made. Try again in a minute."
            + (f" It said: {said}" if said else "")))
    return ProviderError("other", (
        f"{host} did not accept the request (error {status}), so nothing was made."
        + (f" It said: {said}" if said else "")))
