"""The break room, as the beat sees it: when an agent may speak outside social mode,
and when a room nobody answers in closes for a while."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.rules import rule

CHOICE_MODES = ("work", "explore", "outlet")


def spoken(e: Any) -> tuple[str, float, str, str]:
    """(speaker, time in seconds, text, id) of a break-room line, a ``breakroom`` event
    of the village log."""
    return str(e.agent or ""), float(e.ts), str(e.data.get("text", "")), str(e.id)


def invited(v: Any, agent_id: str) -> bool:
    """A coworker spoke in the last few minutes (``village.beat.invite_minutes``) and
    this agent has not answered since. The break room itself answers this."""
    return bool(v.ask("comms.breakroom", "invited", agent_id, default=False))


@rule("say_by_choice", "village.beat.say_room")
def can_say(v: Any, agent_id: str) -> bool:
    """Outside social mode: may this agent speak in the break room right now?

    The body only enters social mode when the need for company is low, which for a
    newcomer is hours away. So an agent can speak first whenever someone else is awake
    and it has not had its fill, and can always answer a coworker who just spoke."""
    a, w = v.world.agents[agent_id], v.world
    others = any(o.id != agent_id and not o.asleep for o in w.agents.values())
    wants = (others and w.company_now(a)
             and a.needs["social"] < v.S("social.full_at") - v.beat_s("say_room"))
    return bool(wants or invited(v, agent_id))


def extra_tools(v: Any, agent_id: str, mode: str) -> tuple[str, ...]:
    """Tools offered by the situation, not the mode."""
    return ("say",) if mode in CHOICE_MODES and can_say(v, agent_id) else ()


@rule("empty_room", "village.social.empty_room_wait_min")
def check_empty_room(v: Any, a: Any) -> bool:
    """Nobody has answered this agent's run of messages for a while: close the room to
    it, once per silence, and try again later. True when the room was just closed."""
    now = v.clock.now()
    if a.social_cooldown_until and now < a.social_cooldown_until:
        return False
    first = v.ask("comms.breakroom", "unanswered_since", a.id)
    if first is None:
        return False
    _who, ts, _text, first_id = spoken(first)
    if a.social_cooldown_until and ts < a.social_cooldown_until:
        return False  # that silence already closed the room once
    if now - ts < v.S("social.empty_room_wait_min") * 60:
        return False
    v.world.social_unanswered(a.id, [first_id])
    return True
