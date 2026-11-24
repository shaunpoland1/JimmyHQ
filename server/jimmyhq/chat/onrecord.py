"""The part of a conversation that is on the record: events on the Tape.

This is the Tape side of ``backends.Backend``. Reading is done by the folds in
``store``; this module prints the ``chat.*`` events of a conversation's life.
Nothing is removed from here: deleting a whole conversation is ``deletion``.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.chat import store
from jimmyhq.core import Core
from jimmyhq.tape.registry import Event


def _payload_target(target: dict[str, Any]) -> dict[str, Any]:
    return {"role": None, "provider_id": None, "model": None, "agent_id": None} | target


def _with_detached(events: list[Event], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the replies whose question is not on the Tape: a reply regenerated
    on the record to a message that was sent off it. The newest to each
    question, in Tape order with the rest."""
    asked_here = {e.payload["message_id"] for e in events if e.kind == "chat.message.sent"}
    detached: dict[str, Event] = {}
    for event in events:
        if (event.kind == "chat.message.answered"
                and event.payload["reply_to"] not in asked_here):
            detached[event.payload["reply_to"]] = event
    if not detached:
        return messages
    return sorted([*messages, *(store.message_of(e) for e in detached.values())],
                  key=lambda m: m["order"])


class OnRecord:
    on_record = True

    def __init__(self, core: Core):
        self.core = core

    # --- reading -----------------------------------------------------------------

    def entries(self) -> list[dict[str, Any]]:
        return list(store.listed(self.core).values())

    def find(self, conv_id: str) -> dict[str, Any] | None:
        """From the list when it is there, otherwise rebuilt from its own events."""
        conv = store.listed(self.core).get(conv_id)
        if conv is not None:
            return conv
        events = store.events_of(self.core, conv_id)
        return store.thread(events)[0] if events else None

    def story(self, conv_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], bool] | None:
        events = store.events_of(self.core, conv_id)
        if not events:
            return None
        conv, messages = store.thread(events)
        if conv.get("mixed"):
            messages = _with_detached(events, messages)
        return conv, messages, len(events) >= store.MAX_EVENTS - 10

    def newest(self, conv_id: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
        return store.newest_messages(self.core, conv_id, limit)

    def last_reply(self, conv_id: str, reply_to: str) -> str | None:
        """The id of the newest reply kept here to one message, if there is one."""
        return next((e.payload["message_id"] for e in reversed(store.events_of(self.core, conv_id))
                     if e.kind == "chat.message.answered"
                     and e.payload["reply_to"] == reply_to), None)

    # --- writing -----------------------------------------------------------------

    def _print(self, kind: str, conv_id: str, payload: dict[str, Any], *, actor: str,
               root: str | None = None, cause: str | None = None) -> Event:
        return self.core.tape.print(kind, {"conversation_id": conv_id, **payload}, actor=actor,
                                    subject=store.subject(conv_id), root=root, cause=cause)

    def create(self, conv_id: str, fields: dict[str, Any], *, actor: str) -> dict[str, Any]:
        event = self._print("chat.conversation.started", conv_id,
                            fields | {"target": _payload_target(fields["target"])}, actor=actor)
        return store.thread([event])[0]

    def change(self, conv: dict[str, Any], diff: dict[str, Any], *, actor: str) -> None:
        payload = dict(diff)
        if "target" in diff:
            payload["target"] = _payload_target(diff["target"])
        if "effort" in diff:
            payload["effort_cleared"] = diff["effort"] is None
        self._print("chat.conversation.changed", conv["id"], payload, actor=actor,
                    root=conv["root"])

    def set_archived(self, conv: dict[str, Any], archived: bool, *, actor: str) -> None:
        if archived:
            self._print("chat.conversation.archived", conv["id"], {}, actor=actor,
                        root=conv["root"])
            return
        left_the_list = conv["id"] not in store.listed(self.core)
        # Carries the list entry, so a list that had let it go can show it again.
        self._print("chat.conversation.restored", conv["id"], {
            "title": conv["title"], "title_source": conv["title_source"],
            "target": _payload_target(conv["target"]), "effort": conv["effort"],
            "local_only": conv["local_only"], "use_memory": conv.get("use_memory", True),
            "created": conv["created"],
            "updated": conv["updated"], "messages": conv["messages"],
            "started_event": conv["root"],
        }, actor=actor, root=conv["root"])
        if left_the_list and conv.get("mixed"):
            # The list entry it comes back with has no room for the switch, so
            # say again how it stands.
            self.set_mode(conv, conv.get("on_record", True), actor=actor)

    def set_mode(self, conv: dict[str, Any], on_record: bool, *, actor: str) -> None:
        """Record that the switch was flipped. Only the Tape keeps the switch:
        a conversation with no part here is off the record by definition."""
        self._print("chat.conversation.mode", conv["id"], {"on_record": on_record}, actor=actor,
                    root=conv["root"])

    def say(self, conv: dict[str, Any], message_id: str, text: str, title: str | None, *,
            actor: str) -> dict[str, Any]:
        event = self._print("chat.message.sent", conv["id"], {
            "message_id": message_id, **store.stored_text(self.core, text), "title": title,
        }, actor=actor, root=conv["root"])
        return store.message_of(event) | {"text": text, "text_ref": None}

    def answer(self, conv: dict[str, Any], reply: dict[str, Any], text: str, *,
               cause: str | None) -> int:
        event = self._print("chat.message.answered", conv["id"],
                            {**reply, **store.stored_text(self.core, text)}, actor="system",
                            root=conv["root"], cause=cause)
        return event.ts

    def retract(self, conv_id: str, reply_to: str) -> None:
        """Nothing to do: a reply that has been replaced stays on the Tape."""
