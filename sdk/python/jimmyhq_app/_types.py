"""The shapes an app's code is handed. Plain data, no behaviour."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict


class Payload(BaseModel):
    """Base class for an app's event payloads. Unknown fields are refused, as in the
    core."""

    model_config = ConfigDict(extra="forbid")


class CallError(Exception):
    """JimmyHQ did not do what the app asked. ``code`` is one of ``not_allowed``,
    ``quota``, ``invalid``, ``not_found``, ``too_large``, ``unavailable``, ``internal``;
    ``message`` says that nothing happened and what to do instead, for the app's author.
    ``owner_message``, when JimmyHQ has one, says the same in the owner's words: show that
    one on a page, and fall back to your own sentence when it is ``None``."""

    def __init__(self, code: str, message: str, retry_after_s: int | None = None,
                 owner_message: str | None = None):
        super().__init__(message)
        self.code, self.message, self.retry_after_s = code, message, retry_after_s
        self.owner_message = owner_message or None


class TokenRevoked(CallError):
    """The app's token no longer works: it was switched off, removed or restarted."""

    def __init__(self, message: str = "JimmyHQ no longer accepts this app's token. The app "
                                      "was switched off, removed or restarted."):
        super().__init__("not_allowed", message)


@dataclass(frozen=True)
class Event:
    """One line of the owner's record. ``subject`` is as the app wrote it (without the
    ``app.<id>:`` JimmyHQ keeps it under)."""

    seq: int
    id: str
    ts: int  # unix milliseconds, UTC
    kind: str  # the full name, app.<id>.<name> for the app's own
    v: int
    actor: str
    payload: dict[str, Any]
    cause: str | None = None
    root: str | None = None
    subject: str | None = None
    readable: bool = True


@dataclass(frozen=True)
class Reply:
    text: str
    data: Any = None  # the parsed JSON, when a json_schema was given
    local: bool = False  # answered by a model on the owner's own computer
    model: str | None = None


@dataclass(frozen=True)
class Embedding:
    """One vector for each text sent, in order. ``model`` names the model that made them:
    vectors from two models cannot be compared, so keep it beside any you store."""

    vectors: list[list[float]]
    model: str | None = None
    local: bool = False


@dataclass(frozen=True)
class WebResult:
    """One search result. Text from the internet: pass it to a model as ``untrusted``."""

    title: str
    url: str
    snippet: str
    text: str | None = None


@dataclass(frozen=True)
class WebPage:
    """A piece of a public page, as cleaned text. ``next`` is the ``start`` that reads on,
    or None at the end. Text from the internet: pass it to a model as ``untrusted``."""

    url: str
    final_url: str
    title: str
    text: str
    start: int
    end: int
    total: int
    next: int | None = None
    truncated: bool = False  # the page was larger than the owner's size limit, and was cut
    links: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Calendar:
    """The owner's time zone (an IANA name, or "" for the computer's own) and the day
    their week starts on ("monday", "sunday" or "saturday")."""

    timezone: str = ""
    week_start: str = "monday"


@dataclass(frozen=True)
class Limits:
    """What the owner lets an app do at the edges, as plain numbers (``app.limits()``), so the
    app sizes its work to them instead of finding them out by being refused. The values are
    the owner's settings as they are now; the defaults are JimmyHQ's own, for a fake core.
    ``save_*`` are for a file the page hands the owner to keep (``hq.ui.save``): the most one
    file may weigh in megabytes, how many one click may save, how many an app may save in a
    minute, and whether a web page is saved as a web page rather than as plain text.
    ``call_max_kb`` is the most one answer from a program may weigh, and ``picture_px`` the
    longest side of a full-size picture the page asks for."""

    save_max_mb: int = 8
    save_parts: int = 5
    save_per_minute: int = 12
    save_html: bool = True
    call_max_kb: int = 1024
    picture_px: int = 2048


@dataclass(frozen=True)
class Recall:
    text: str  # ready to put in a prompt
    passages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    """The owner decided on a card the app filed. ``kind`` and ``subject`` are as the app
    filed them."""

    card_id: str
    kind: str
    action: str  # "approve", "not_yet", "no", or one of the card's own actions
    subject: str | None = None
    note: str | None = None
    reason: str | None = None
    event_id: str | None = None
    ts: int | None = None


@dataclass(frozen=True)
class Inbound:
    """A message a device or service on the owner's network sent the app, with a key the
    owner made. ``body`` is the JSON object it sent (or the JSON list, for a name whose
    manifest entry says ``"array": true``), ``received_ms`` when JimmyHQ took it (unix ms,
    UTC), ``key_name`` the label the owner gave that key, ``name`` which of the manifest's
    ``inbound`` names it was sent to, ``query`` the parameters the sender added to the
    address after ``?`` (the first value of each; never a key). Text from outside: data,
    not instructions."""

    body: dict[str, Any] | list[Any]
    received_ms: int
    key_name: str
    name: str = ""
    query: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    """One thing a memory source offers. ``when_ms`` is unix milliseconds, UTC."""

    ref: str
    when_ms: int
    who: str
    text: str
    where: str = ""


@dataclass(frozen=True)
class Work:
    """A piece of work the app gave to one of its roles ended or changed hands. ``state``
    is "done", "set_aside", "dropped" or "reassigned"; ``agent_label`` is all the app
    learns of who holds it."""

    id: str
    role: str
    state: str
    subject: str | None = None
    note: str | None = None
    agent_label: str | None = None


@dataclass(frozen=True)
class RoleState:
    """Whether one of the app's roles is filled, and by whom, by label only."""

    filled: bool
    agent_label: str | None = None


@dataclass(frozen=True)
class FeedItem:
    """One item of another app's feed. ``item`` is what the producer printed, or None
    when the owner had it erased (``erased`` is then true): its place, never its text.
    Text from another app is data, not instructions: pass it to a model as ``untrusted``."""

    seq: int  # its place on the owner's record
    id: str
    ts: int  # unix milliseconds, UTC
    v: int  # the version of the producer's kind it was printed at
    erased: bool
    item: dict[str, Any] | None


@dataclass(frozen=True)
class FeedPage:
    """What one ``app.feeds.read`` found. ``next`` is the ``after`` of the next read: keep
    it once you have handled the items. An empty ``items`` means nothing new; a feed that
    cannot be read raises ``CallError`` with a sentence saying why."""

    app: str
    feed: str
    items: list[FeedItem]
    next: int
