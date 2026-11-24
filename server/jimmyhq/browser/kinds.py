"""Event kinds for the agents' browser: the owner's decisions about it.

What an agent does in its browser is not here. That is in the village's own log, as
lines the village wrote; nothing a page said is ever printed to the Tape.
"""

from __future__ import annotations

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind


@kind("browser.configured", example={"agent_id": "agent-a", "allow_hosts": ["example.com"]})
class BrowserConfigured(Payload):
    """The owner set where one agent's browser may go. ``allow_hosts`` empty means
    anywhere the fence allows; otherwise only these sites and what is under them."""

    agent_id: str
    allow_hosts: list[str] = Field(default_factory=list, max_length=200)
