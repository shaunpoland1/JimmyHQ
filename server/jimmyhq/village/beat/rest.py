"""Rest is mechanical: no model call. But resting is not sleeping, so the owner's
message still gets an answer."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.rules import rule

THOUGHTS_THAT_SAW_IT = 2


@rule("rest_no_model", "village.beat.idle_wait_s")
def rest_wait(mode: str, roused: bool, idle_s: float) -> float | None:
    """A resting agent makes no model call; look again after the idle wait."""
    return idle_s if mode == "rest" and not roused else None


@rule("owner_rouses_rest")
def owner_waiting(v: Any, st: Any, agent_id: str) -> Any | None:
    """The owner's latest message, if the agent has not written since, has not already
    been roused for it, and has not already had it in front of it.

    "Had it in front of it" is two thoughts since, not one: a message that lands during
    a model call is logged before that beat's thought but was not in what it read."""
    chats = v.tail(agent_id, ["chat"], 1)
    if not chats or chats[-1].data.get("sender") == agent_id or chats[-1].id == st.rest_heard:
        return None
    thoughts = v.log.query(agent=agent_id, kinds=["thought"], since=chats[-1].ts)
    if len(thoughts) >= THOUGHTS_THAT_SAW_IT:
        return None
    return chats[-1]


def rouse(v: Any, st: Any, a: Any, message: Any) -> None:
    """Get up for one beat; after it the body decides again."""
    st.rest_heard = message.id
    a.rest_until = None
    v.log.append("mode_change", a.id, v.clock.now(), old="rest", new="work",
                 needs={k: round(x, 1) for k, x in a.needs.items()}, cause="owner_wrote",
                 evidence=[message.id])
    a.mode = "work"
