"""Kinds for the platform itself: start-up, settings, access."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind


@kind("system.opened", example={"version": "0.1.0"})
class SystemOpened(Payload):
    """Printed once, on an empty Tape."""

    version: str


@kind("setting.updated", example={"key": "appearance.theme", "value": "dark"})
class SettingUpdated(Payload):
    key: str
    value: Any


@kind("setting.reset", example={"key": "appearance.theme"})
class SettingReset(Payload):
    """A setting was put back to its default."""

    key: str


@kind("owner.created", example={})
class OwnerCreated(Payload):
    """The owner account was set up. The name is a setting, not part of this event."""


@kind("owner.password_changed", example={"via": "settings", "sessions_ended": 2})
class OwnerPasswordChanged(Payload):
    """The owner's password was replaced. Says where from and how many sessions it
    ended: never the password, its length, or anything worked out from it."""

    via: Literal["settings", "command"]  # the Settings screen, or `jimmyhq owner set-password`
    sessions_ended: int


@kind("owner.sessions_ended", example={"sessions_ended": 2})
class OwnerSessionsEnded(Payload):
    """The owner signed out every browser but the one they were using."""

    sessions_ended: int


@kind("token.created", example={"token_id": "01JABCDEFGHJKMNPQRSTVWXYZ0", "name": "wall",
                                "scopes": ["read"]})
class TokenCreated(Payload):
    token_id: str
    name: str
    scopes: list[str]


@kind("token.revoked", example={"token_id": "01JABCDEFGHJKMNPQRSTVWXYZ0"})
class TokenRevoked(Payload):
    token_id: str


@kind("tape.erased", example={
    "reason": "chat.conversation.deleted", "events": 12,
    "kinds": {"chat.conversation.started": 1, "chat.message.answered": 5,
              "chat.message.sent": 6},
})
class TapeErased(Payload):
    """The owner had the payloads of some events erased (``Tape.erase``). This
    says how many, of which kinds, and why: never what they said. The erased
    events keep their place on the Tape; a lens that folds one of these kinds
    rebuilds itself when it meets this event."""

    reason: str = Field(max_length=100)  # the event kind that explains the erasure
    events: int = Field(ge=1)
    kinds: dict[str, int]
