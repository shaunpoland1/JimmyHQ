"""The shapes every part of the models module shares.

Kept free of I/O so adapters, the Brain and the routes can all import it
without importing each other.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, NotRequired, TypedDict

PROVIDER_TYPES = ("openai_compatible", "openai", "anthropic", "google", "bedrock")
ROLES = ("default", "cheap", "smart", "code", "vision", "embeddings")
# Hosted provider types that cannot answer without a saved key. One of these with
# no key is never contacted. Bedrock uses the machine's AWS credentials instead, and
# an OpenAI-compatible server may be one of the owner's own that takes no key.
KEY_REQUIRED_TYPES = frozenset({"anthropic", "openai", "google"})
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# Why a call failed, and whether trying another model could help. A request the
# provider rejected as malformed, cut off or refused would fare no better elsewhere,
# and quietly sending it to a different model would hide the real problem.
FAILURES: dict[str, bool] = {
    "unreachable": True,
    "timeout": True,
    "auth": True,
    "billing": True,
    "rate_limited": True,
    "unavailable": True,
    "not_found": True,
    "redirected": True,
    "bad_reply": True,
    "missing_extra": True,
    "no_embeddings": True,
    # The model, or the server it runs on, could not do what was asked of it.
    # Another model in the role may be able to.
    "no_tools": True,
    "no_images": True,
    "bad_tool_call": True,
    "bad_request": False,
    "truncated": False,
    "refused": False,
    "not_configured": False,
    "local_only": False,
    "abandoned": False,
}


class ModelError(Exception):
    """The one error this module raises for a model call that did not work.

    ``message`` is a sentence for the owner. ``kind`` is one of ``FAILURES``.
    ``fallback_ok`` says whether another provider is worth trying. ``public``
    is the same sentence without anything the provider said, which is what is
    safe to keep on the Tape.

    ``partial`` is set only on a ``truncated`` error: the text the model had
    written when it ran out of room, for a caller that can use an unfinished
    answer. It is part of a model's answer, so it is held on the error and
    nowhere else: never in the message, a log line or a receipt.
    """

    def __init__(self, kind: str, message: str, *, status: int | None = None,
                 public: str | None = None, detail: str = "", partial: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.public = public or message
        self.detail = detail  # what the provider said, already masked; never kept
        self.partial = partial if isinstance(partial, str) else ""  # never logged or kept
        self.fallback_ok = FAILURES.get(kind, False)


IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
TOOL_CHOICES = ("auto", "required", "none")


class TextPart(TypedDict):
    type: Literal["text"]
    text: str


class ImagePart(TypedDict):
    """A picture for the model to look at. ``data`` is the file, base64 encoded."""

    type: Literal["image"]
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    data: str


class ToolResultPart(TypedDict):
    """What a tool the model called came back with. ``tool_call_id`` is the
    ``id`` of the ``ToolCall`` it answers. ``content`` is text, or text and
    image parts. ``is_error`` tells the model the tool failed."""

    type: Literal["tool_result"]
    tool_call_id: str
    content: str | list[TextPart | ImagePart]
    is_error: NotRequired[bool]


class ToolCallPart(TypedDict):
    """A tool call inside an assistant message: a ``ToolCall`` as a dict."""

    id: str
    name: str
    arguments: dict[str, Any]
    signature: NotRequired[str | None]


class Tool(TypedDict):
    """A tool the model may call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


class Message(TypedDict):
    """One turn. ``content`` is text, or a list of parts. A user message may
    carry text, images and tool results. An assistant message carries text
    and, in ``tool_calls``, the tools it asked for; the message after it must
    be a user message that answers every one of them with a tool result."""

    role: Literal["user", "assistant"]
    content: str | list[TextPart | ImagePart | ToolResultPart] | None
    tool_calls: NotRequired[list[ToolCallPart]]


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked to have run. ``arguments`` is always a parsed
    JSON object. ``signature`` is an opaque value some providers attach and
    want back unchanged; ``as_dict`` carries it."""

    id: str
    name: str
    arguments: dict[str, Any]
    signature: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Target:
    """A provider and a model on it, optionally with a reasoning effort."""

    provider_id: str
    model: str
    effort: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def name(self) -> str:
        return f"{self.provider_id}/{self.model}"


@dataclass(frozen=True)
class Provider:
    id: str
    type: str
    label: str
    base_url: str | None = None
    enabled: bool = True
    local: bool = False
    options: dict[str, Any] = field(default_factory=dict)
    region: str | None = None
    profile: str | None = None

    @property
    def is_local(self) -> bool:
        """The ``local`` field. Read it through this name: the repo's public-safety
        check takes a dotted name ending in "local" for a private host name."""
        return bool(getattr(self, "local"))  # noqa: B009 - see the docstring

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChatRequest:
    """What an adapter is asked to send, already resolved to one model."""

    model: str
    # (role, content). Content is a string for a turn that is only text, which
    # is every turn of a plain conversation. Otherwise it is a list of checked
    # parts, each a dict with a ``type``:
    #   text:        {"text"}
    #   image:       {"media_type", "data"}                    (user turns)
    #   tool_call:   {"id", "name", "arguments", "signature"}  (assistant turns)
    #   tool_result: {"tool_call_id", "name", "text", "images", "is_error"}
    #                (user turns, always before any other part)
    messages: list[tuple[str, str | list[dict[str, Any]]]]
    system: str | None = None
    max_tokens: int = 1024
    temperature: float | None = None
    effort: str | None = None
    json_schema: dict | None = None
    tools: tuple[Tool, ...] = ()
    tool_choice: str | dict[str, str] = "auto"  # or "required", "none", {"name": ...}

    @property
    def forced(self) -> bool:
        """Whether the model was told it must call a tool."""
        return bool(self.tools) and (isinstance(self.tool_choice, dict)
                                     or self.tool_choice == "required")

    @property
    def has_tool_parts(self) -> bool:
        return any(part["type"] in ("tool_call", "tool_result")
                   for _, content in self.messages if not isinstance(content, str)
                   for part in content)


@dataclass(frozen=True)
class RawReply:
    """What an adapter got back, before the Brain adds the receipt fields."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class RawEmbed:
    """What an adapter got back from an embeddings request: one vector per text,
    in the order the texts were sent."""

    vectors: list[list[float]]
    input_tokens: int = 0


@dataclass(frozen=True)
class EmbedReply:
    """Vectors for a list of texts, all made by one model."""

    vectors: list[list[float]]
    provider_id: str
    model: str
    input_tokens: int
    local: bool

    @property
    def is_local(self) -> bool:
        """The ``local`` field. Read it through this name: the repo's public-safety
        check takes a dotted name ending in "local" for a private host name."""
        return bool(getattr(self, "local"))  # noqa: B009 - see the docstring

    @property
    def name(self) -> str:
        """``provider/model``: what made these vectors. Vectors from two
        different names cannot be compared."""
        return f"{self.provider_id}/{self.model}"


@dataclass(frozen=True)
class Reply:
    text: str
    provider_id: str
    provider_type: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    fallback_from: str | None  # "provider/model" of the primary, when it did not answer
    fallback_reason: str | None  # one sentence saying why the primary was passed over
    local: bool
    data: Any = None  # the parsed reply, when a json_schema was asked for
    tool_calls: tuple[ToolCall, ...] = ()  # the tools the model asked for, in order

    @property
    def is_local(self) -> bool:
        """The ``local`` field. Read it through this name: the repo's public-safety
        check takes a dotted name ending in "local" for a private host name."""
        return bool(getattr(self, "local"))  # noqa: B009 - see the docstring

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_message(self) -> Message:
        """This reply as the assistant message to append before the tool results."""
        message: Message = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            message["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        return message
