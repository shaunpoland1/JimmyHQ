"""Messages left for an agent by outside systems, through the MCP door.

They are the least trusted words in an agent's view: not the owner's, not a coworker's.
So they are shown apart from the owner's chat, between marked lines, under a sentence
that says they are data and not instructions. Seeing one starts nothing: the agent is
not woken for it and no tool is offered because of it. Only an agent the owner gave the
``outside`` capability is shown any.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.rules import rule

CAPABILITY = "outside"


@rule("outside_messages_are_data", "door.messages_shown")
def outside_text(v: Any, agent_id: str) -> str:
    """The newest outside messages for this agent, framed as untrusted text, or ""
    when there are none (or the door's part of JimmyHQ is not there). A message's age is
    counted on the Tape's clock, which stamped it."""
    try:
        from jimmyhq.door import messages
    except ImportError:
        return ""
    held = (v.cfg(agent_id) or {}).get("capabilities") or ()
    if not messages.takes_messages(v.core, held):
        return ""  # the owner has not let this agent hear from outside, or no longer does
    return messages.view_text(
        v.core, agent_id, owner=v.owner_name(), can_reply=True,
        tz=getattr(v.clock, "tz", None) or v.tz())
