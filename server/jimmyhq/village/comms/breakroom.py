"""The break room: free talk between agents. No prompts, no agenda.

Messages are ``breakroom`` events in the village log. Saying something fills the
speaker's social need only if someone else is awake to hear it: talking to an empty room
fills nothing, because an agent that talks to itself spirals. A frustrated agent's
messages are cut shorter, which is how coworkers can tell. A village-wide cap on messages
per hour backs up the social mechanic, and reaching it is logged as a fault in the
village, not in the agent.
"""

from __future__ import annotations

import re
from typing import Any

from jimmyhq.village.comms import Refused, limit, setting

INVITE_MINUTES = 10  # a message this recent invites coworkers to reply outside social mode
SHORT_TEMPER_MARGIN = 7  # this close to needing a break, an agent is short-tempered


def recent(v, agent_id: str | None = None, minutes: float = 120, limit: int = 12) -> list[Any]:
    """The room's newest messages, oldest first. Everyone hears the same room, so
    ``agent_id`` changes nothing; it is there so every view is called the same way."""
    evs = v.log.query(kinds=["breakroom"], since=v.clock.now() - minutes * 60)
    return evs[-limit:]


def invite_minutes(v) -> float:
    return float(setting(v, "village.beat.invite_minutes", INVITE_MINUTES))


def invited(v, agent_id: str) -> bool:
    """Someone else spoke in the last few minutes and this agent has not answered since."""
    evs = recent(v, minutes=invite_minutes(v))
    return bool(evs) and evs[-1].agent != agent_id


def unanswered_since(v, agent_id: str) -> Any | None:
    """The first message of the agent's current run that nobody has answered, or None."""
    run_start = None
    for e in reversed(recent(v, minutes=240, limit=50)):
        if e.agent != agent_id:
            break  # someone else spoke after (or between) this agent's messages
        run_start = e
    return run_start


def _interests(v, agent_id: str) -> str:
    a = v.world.agents[agent_id]
    words = " ".join(a.temperament.get("seed_curiosities", []))
    try:
        from jimmyhq.village.memory.store import MemoryStore

        wants = MemoryStore(v.agent_dir(agent_id)).section("wants")
        words += " " + " ".join(str(i.get("text", "")) for i in wants)
    except Exception:  # noqa: BLE001 - no memory yet is no interests yet
        pass
    return words


def topic_match(v, agent_id: str, text: str) -> bool:
    """Does what is being said touch this agent's interests (what it is curious about
    and what it wants)?"""
    words = set(re.findall(r"[a-z]{4,}", _interests(v, agent_id).lower()))
    return bool(words & set(re.findall(r"[a-z]{4,}", text.lower())))


def short_tempered(v, agent_id: str) -> bool:
    a = v.world.agents[agent_id]
    return a.needs["frustration"] >= v.world.S("modes.outlet_at_frustration") - SHORT_TEMPER_MARGIN


def say_too_long(v, agent_id: str, text: str) -> str | None:
    """Rule ``say_too_long``: a calm agent's over-long message is not sent. It is told
    the size and says it again, shorter. Cut mid-sentence, it would say it again anyway.
    A short-tempered agent is cut instead: that is the mechanic."""
    if short_tempered(v, agent_id):
        return None
    most = int(v.world.S("social.say_chars"))
    if len(text) <= most:
        return None
    return (f"Nothing was said: that is {len(text)} characters and the break room takes "
            f"{most}. Say the short version here. Anything longer belongs on the work board "
            "(post_to_board) or in a file, with one line here pointing at it.")


def breakroom_per_hour(v, agent_id: str) -> None:
    """Rule ``breakroom_per_hour``: the whole room's cap. Reaching it means the social
    mechanic did not end the conversation by itself, so it is logged as a fault."""
    cap = limit(v, "breakroom_per_hour")
    n = len(v.log.query(kinds=["breakroom"], since=v.clock.now() - 3600))
    if n >= cap:
        v.log.append("platform_fault", agent_id, v.clock.now(), what="break-room cap reached",
                     detail="the social mechanic did not end conversations before the hourly cap")
        raise Refused("breakroom_per_hour",
                      f"Nothing was said: the break room has had {n} messages this hour and "
                      f"takes {cap}, so it is closed for the rest of the hour. Go back to "
                      "what you were doing; if a coworker needs something from you, use the "
                      "work board (post_to_board).", frustrate=False)


def say(v, agent_id: str, text: str) -> tuple[Any, str]:
    """Say it. Returns the event and a note for the speaker. The world fills social only
    if someone is there."""
    w = v.world
    a = w.agents[agent_id]
    tense = short_tempered(v, agent_id)
    most = int(w.S("social.frustrated_say_chars") if tense else w.S("social.say_chars"))
    note = ""
    if len(text) > most:
        text = text[:most].rstrip() + "…"
        note = " (you were too short-tempered to say more)" if tense else \
            " (cut: too long for the break room)"
    ev = v.log.append("breakroom", agent_id, v.clock.now(), text=text)
    listeners = [o for o in w.agents.values() if o.id != agent_id and not o.asleep]
    if listeners:
        last_other = next((e for e in reversed(recent(v, minutes=30)) if e.agent != agent_id),
                          None)
        match = topic_match(v, agent_id, text) or (
            last_other is not None and topic_match(v, agent_id, last_other.data["text"]))
        w.message(agent_id, ",".join(o.id for o in listeners), topic_match=match,
                  venting=a.needs["frustration"] > 40)
        for o in listeners:
            # Being talked to fills too, if they are in the room or were just invited in.
            if o.mode == "social" or invited(v, o.id):
                w.message(o.id, agent_id, topic_match=topic_match(v, o.id, text), heard=True,
                          evidence=[ev.id])
            v.wake(o.id)
    return ev, "said in the break room" + note + (
        "" if listeners else "; nobody else is around right now")


def speak(v, agent_id: str, text: str) -> tuple[Any, str]:
    """``say`` with its rules applied."""
    breakroom_per_hour(v, agent_id)
    why = say_too_long(v, agent_id, text)
    if why:
        raise Refused("say_too_long", why, frustrate=False)
    return say(v, agent_id, text)
