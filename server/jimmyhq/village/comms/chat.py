"""Chat between the owner and one agent.

Messages are ``chat`` events in the village log, so they are part of the record the night
reads. The owner's words are a trusted source, unlike a web page or an email. The owner
writes through the web, which prints ``village.said`` on the Tape; the village process
mirrors that into the log with ``mirror``. An agent's own message is printed to the Tape
and logged at once by ``post``, and ``mirror`` knows not to log it twice.

An agent's messages are capped per day. The cap counts only messages the agent starts: a
reply to a message from the owner is always allowed and is not counted, or a morning the
owner spends talking with an agent ends with its answer to a live question refused.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.comms import Refused, day_start, limit, newest, owner

OWNER = "owner"
MAX_LEN = 4000
MIRROR_LOOKBACK = 200  # newest messages checked before one is mirrored


def too_long(v, text: str) -> str | None:
    """Rule ``chat_too_long``: a message over the size is not sent. Cutting it silently
    loses its end, and the end is often the question."""
    if len(text) <= MAX_LEN:
        return None
    return (f"Nothing was sent: that is {len(text)} characters and a message takes {MAX_LEN}. "
            "Send the short version, and put anything long in a file and name it in the "
            "message.")


def _log(v, agent_id: str, sender: str, text: str, urgent: bool, tape: str | None) -> Any:
    data: dict[str, Any] = {"sender": sender, "text": text.strip()[:MAX_LEN]}
    if urgent and sender == OWNER:
        data["urgent"] = True
    if tape:
        data["tape"] = tape
    ev = v.log.append("chat", agent_id, v.clock.now(), **data)
    if data.get("urgent") and agent_id in v.world.agents:
        # A call: it wakes the agent even from sleep, and stays at the top of what it
        # sees until it answers. Meant to be rare, so it keeps meaning something.
        v.world.wake_for_call(agent_id, ev)
    if sender == OWNER:
        v.wake(agent_id)
    return ev


def post(v, agent_id: str, sender: str, text: str, urgent: bool = False) -> Any:
    """Say something in the chat, from this process: print it on the Tape (so the owner's
    screen shows it) and put it in the log. Returns the log event."""
    from jimmyhq.village import record

    said = record.said(v.core, agent_id, sender, text.strip()[:MAX_LEN], urgent=urgent)
    return _log(v, agent_id, sender, text, urgent, getattr(said, "id", None))


def mirror(v, event: Any) -> Any | None:
    """Put a ``village.said`` Tape event into the log, once. The village process calls
    this for every such event it tails. None when the log already has it."""
    p = event.payload
    agent_id = p["agent_id"]
    # Looked for by id among the newest messages, not by time: the Tape's clock and the
    # village's need not agree.
    if any(e.data.get("tape") == event.id for e in newest(v, agent_id, ["chat"], MIRROR_LOOKBACK)):
        return None
    sender = OWNER if p["sender"] == OWNER else agent_id  # the Tape says "agent"; the log, who
    return _log(v, agent_id, sender, p["text"], bool(p.get("urgent")), event.id)


def history(v, agent_id: str, since: float | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Messages oldest first: ``{id, ts, sender, text, urgent}``, ``ts`` in seconds."""
    seen: set[str] = set()
    out = []
    for e in v.log.query(agent=agent_id, kinds=["chat"], since=since):
        tape = e.data.get("tape")
        if tape:
            if tape in seen:
                continue
            seen.add(tape)
        out.append({"id": e.id, "ts": e.ts, "sender": e.data["sender"], "text": e.data["text"],
                    "urgent": bool(e.data.get("urgent"))})
    return out[-limit:]


def open_call(v, agent_id: str) -> dict[str, Any] | None:
    """The owner's latest urgent message, if the agent has not written back since."""
    for m in reversed(history(v, agent_id, limit=50)):
        if m["sender"] != OWNER:
            return None
        if m["urgent"]:
            return {"id": m["id"], "ts": m["ts"], "text": m["text"]}
    return None


def unanswered(v, agent_id: str) -> bool:
    """Whether the newest message in the chat is the owner's."""
    last = history(v, agent_id, limit=1)
    return bool(last) and last[-1]["sender"] == OWNER


def sent_today(v, agent_id: str) -> int:
    """The agent's messages today that it started itself. A message sent straight after
    one of the owner's is a reply and does not count; a second in a row does."""
    start = day_start(v)
    n, prev = 0, None
    for m in history(v, agent_id, since=start - 86400, limit=2000):
        if m["ts"] >= start and m["sender"] != OWNER and prev != OWNER:
            n += 1
        prev = m["sender"]
    return n


def chat_messages_per_day(v, agent_id: str) -> None:
    """Rule ``chat_messages_per_day``. Replies are free."""
    if unanswered(v, agent_id):
        return
    cap, sent = limit(v, "chat_messages_per_day"), sent_today(v, agent_id)
    if sent >= cap:
        raise Refused("chat_messages_per_day",
                      f"Nothing was sent: you have started {sent} messages to {owner(v)} "
                      f"today and the day's limit is {cap}. A reply to a message of theirs "
                      "is always allowed. Keep this for tomorrow, or put it in a file.",
                      frustrate=False)


def send(v, agent_id: str, text: str) -> Any:
    """An agent's message to the owner, rules applied."""
    why = too_long(v, text)
    if why:
        raise Refused("chat_too_long", why, frustrate=False)
    chat_messages_per_day(v, agent_id)
    return post(v, agent_id, agent_id, text)
