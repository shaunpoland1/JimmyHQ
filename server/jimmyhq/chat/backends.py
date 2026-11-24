"""The two places a conversation's words can be kept.

A conversation has a switch, on the record or off it, that can be flipped at
any time. Each message is kept according to the switch when it was sent, so
one conversation may have a part in each place:

- ``onrecord.OnRecord``: events on the Tape, long text in the Vault, the list
  in a lens. Nothing is ever removed.
- ``offrecord.OffRecord``: rows in a SQLite file of its own. Nothing reaches
  the Tape or the Vault, and what is there can be deleted for good.

Both keep a part of a conversation the same way, which is the ``Backend``
interface below; ``conversations`` puts the two parts together and decides
which one a write goes to, so nothing else asks where anything is kept.

Each has one thing the other cannot do: only the Tape records the switch
(``OnRecord.set_mode``), and only the other store can forget
(``OffRecord.forget``).

Parts and messages travel as the plain dicts ``store.thread`` builds. Every
message carries ``on_record`` and ``order``, its place among the messages of
both stores.
"""

from __future__ import annotations

from typing import Any, Protocol

from jimmyhq.chat.offrecord import OffRecord
from jimmyhq.chat.onrecord import OnRecord
from jimmyhq.core import Core

Part = dict[str, Any]
Message = dict[str, Any]


class Backend(Protocol):
    on_record: bool

    def entries(self) -> list[Part]:
        """The part kept here of every conversation the list can show."""

    def find(self, conv_id: str) -> Part | None:
        """The part of one conversation kept here, without its messages."""

    def story(self, conv_id: str) -> tuple[Part, list[Message], bool] | None:
        """The part kept here, its visible messages oldest first, and whether
        it has grown too long to add to."""

    def newest(self, conv_id: str, limit: int) -> tuple[list[Message], bool]:
        """The newest ``limit`` visible messages kept here, oldest first, and
        whether there are older ones."""

    def create(self, conv_id: str, fields: dict[str, Any], *, actor: str) -> Part:
        """Begin keeping a conversation here: title, title_source, target,
        effort, local_only."""

    def change(self, conv: dict[str, Any], diff: dict[str, Any], *, actor: str) -> None:
        """Apply what ``store.differing`` found: never empty."""

    def set_archived(self, conv: dict[str, Any], archived: bool, *, actor: str) -> None: ...

    def say(self, conv: dict[str, Any], message_id: str, text: str, title: str | None, *,
            actor: str) -> Message:
        """Keep a message from the person; ``title`` when it also names the
        conversation."""

    def answer(self, conv: dict[str, Any], reply: dict[str, Any], text: str, *,
               cause: str | None) -> int:
        """Keep a reply that has ended, in place of any earlier reply kept
        here to the same message. Returns when, in unix milliseconds."""

    def retract(self, conv_id: str, reply_to: str) -> None:
        """A newer reply to this message is now kept in the other place.
        Remove the one kept here, if this store removes anything."""


def both(core: Core) -> tuple[OffRecord, OnRecord]:
    return core.service("chat.backends", lambda c: (OffRecord(c), OnRecord(c)))


def pick(core: Core, on_record: bool) -> tuple[Backend, Backend]:
    """Where something is kept under this setting of the switch, and the other place."""
    off, on = both(core)
    return (on, off) if on_record else (off, on)
