"""Event kinds for the Village: what the owner would call a decision or a milestone.

Everything about one agent is printed with the Tape subject ``agent:<id>``; a priority
with ``priority:<id>``. The beat-by-beat record of an agent's day is not here: it lives
in the village's own event log (``village.db``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

_AGENT = "agent-a"


class ModelChoice(Payload):
    """Which model to ask: a role from the Models screen, or one provider's model."""

    role: str | None = None
    provider_id: str | None = None
    model: str | None = None


@kind("village.born", example={"agent_id": _AGENT, "seed": 7, "role": "researcher"})
class VillageBorn(Payload):
    """A new agent joined the village. ``seed`` is what its temperament was rolled from."""

    agent_id: str
    seed: int
    role: str = ""


@kind("village.retired", example={"agent_id": _AGENT, "note": "The project is finished."})
class VillageRetired(Payload):
    """An agent left. Its work stays readable; its private folder is archived."""

    agent_id: str
    note: str = ""


@kind("village.named", example={"agent_id": _AGENT, "name": "Wren"})
class VillageNamed(Payload):
    """An agent's own choice of name settled, after coming up on several nights."""

    agent_id: str
    name: str


@kind("village.configured", example={
    "agent_id": _AGENT, "role": "researcher", "beat": {"role": "default"},
    "night": {"provider_id": "p1", "model": "m1"}, "local_only": False, "capabilities": [],
})
class VillageConfigured(Payload):
    """How an agent is set up: its role, its models and what it is allowed to use."""

    agent_id: str
    role: str = ""
    beat: ModelChoice = Field(default_factory=lambda: ModelChoice(role="default"))
    night: ModelChoice = Field(default_factory=lambda: ModelChoice(role="smart"))
    local_only: bool = False
    capabilities: list[str] = Field(default_factory=list)


@kind("village.prioritized", example={
    "priority_id": "p-draft-newsletters", "title": "Draft newsletters",
    "why": "Readers hear from us once a month.", "details": "One draft a week.",
    "agents": [], "active": True, "suggested_by": None,
})
class VillagePrioritized(Payload):
    """A standing priority was set, changed or switched off. The payload is the whole
    priority as it now stands. ``agents`` empty means everyone."""

    priority_id: str
    title: str
    why: str = ""
    details: str = ""
    agents: list[str] = Field(default_factory=list)
    active: bool = True
    suggested_by: str | None = None


@kind("village.said", example={
    "agent_id": _AGENT, "sender": "owner", "text": "How is the draft going?", "urgent": False,
})
class VillageSaid(Payload):
    """One line of the chat between the owner and an agent. ``sender`` is ``owner`` or
    ``agent``. An urgent line from the owner is a call: it wakes a sleeping agent."""

    agent_id: str
    sender: Literal["owner", "agent"]
    text: str
    urgent: bool = False


@kind("village.slept", example={
    "agent_id": _AGENT, "day": "2026-01-05", "by": "smart", "ops": 12, "rejected": 1,
    "snapshot": 4, "notes": "A day of drafting and one long talk.",
})
class VillageSlept(Payload):
    """A night sorted an agent's day into memory. ``by`` is ``smart`` (the night model)
    or ``fallback`` (the everyday model, after the night model kept failing)."""

    agent_id: str
    day: str
    by: Literal["smart", "fallback"]
    ops: int
    rejected: int = 0
    snapshot: int | None = None
    notes: str = ""


@kind("village.restored", example={
    "agent_id": _AGENT, "to": 3, "undo": 5, "paths": ["memory", "impressions"],
    "note": "Last night's rewrite lost the plan.",
})
class VillageRestored(Payload):
    """An agent's memory was rolled back to snapshot ``to``. Snapshot ``undo`` holds what
    it was just before, so the rollback itself can be undone."""

    agent_id: str
    to: int
    undo: int
    paths: list[str] = Field(default_factory=list)
    note: str = ""
