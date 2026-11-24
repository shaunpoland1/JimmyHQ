"""The image-service interface: what ``making`` calls, and the services.

A provider is handed a ``Request`` and answers a ``Made`` (the picture's bytes) or raises
``ProviderError`` with a sentence the owner can read. It never decides whether to ask:
``making`` checks the daily limit and the room first, and counts the attempt before the
provider is called. A provider makes one picture per call; a request for four is four
calls, so a failure part-way keeps what was made.

    p.id, p.label                    what the setting calls it
    p.outside                        True when it sends anything off this computer
    p.supports_reference             True when it can change a picture the owner gives it
    p.hosts(cfg) -> tuple[str, ...]  the hosts it needs now (for the owner's consent)
    p.problem(cfg) -> str | None     why it cannot be used yet (no key, no address), as a
                                     sentence; None when it can
    p.aspects(cfg) -> list[str]      the shapes it can make, written like ``16:9``
    p.model(cfg) -> str              the model name, shown beside each picture
    p.make(request, cfg) -> Made     one picture, or ``ProviderError``

To add a service, give it these and list it in ``made``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# What went wrong, in a word each: a refusal for the owner's words (change them), a key
# or a setting to fix, a limit, no answer in time, a busy service, or something else.
KINDS = ("key", "refused", "quota", "timeout", "busy", "setup", "other")


@dataclass(frozen=True)
class Reference:
    """A picture of the owner's to start from: its ref in the app's folder and its type."""

    ref: str
    mime: str


@dataclass(frozen=True)
class Request:
    prompt: str
    aspect: str
    reference: Reference | None = None


@dataclass(frozen=True)
class Made:
    data: bytes
    mime: str
    said: str = ""  # words the service sent with the picture, kept short


class ProviderError(Exception):
    """The service made nothing. ``said`` is the sentence for the owner: what happened,
    that nothing was made, and what to do. ``kind`` is one of ``KINDS``. ``sent`` is False
    when nothing left this computer (JimmyHQ refused the request, or the provider found a
    problem before sending), so the request is not counted against the day's limit."""

    def __init__(self, kind: str, said: str, sent: bool = True):
        super().__init__(said)
        self.kind = kind
        self.said = said
        self.sent = sent


class Provider(Protocol):
    id: str
    label: str
    outside: bool
    supports_reference: bool

    def hosts(self, cfg: Any) -> tuple[str, ...]: ...
    def problem(self, cfg: Any) -> str | None: ...
    def aspects(self, cfg: Any) -> list[str]: ...
    def model(self, cfg: Any) -> str: ...
    def make(self, request: Request, cfg: Any) -> Made: ...


def made(app: Any) -> dict[str, Provider]:
    """Every provider, by the id the setting uses."""
    from imagery.providers.gemini import Gemini  # noqa: PLC0415
    from imagery.providers.openai_compatible import OpenAICompatible  # noqa: PLC0415
    from imagery.providers.practice import Practice  # noqa: PLC0415

    return {p.id: p for p in (Practice(), Gemini(app), OpenAICompatible(app))}
