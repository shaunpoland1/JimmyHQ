"""One agent's body: its needs, what it is doing, and what it is waiting on."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .guards import RepeatGuard


@dataclass
class Agent:
    id: str
    temperament: dict[str, Any]
    needs: dict[str, float]
    guard: RepeatGuard
    mode: str = "work"
    asleep: bool = False
    sleep_kind: str | None = None  # night | nap | forced
    nap_until: float | None = None
    napped_on: str | None = None
    rest_until: float | None = None  # a rest the agent chose, with its own end
    current_task: str | None = None
    parked: dict[str, dict[str, Any]] = field(default_factory=dict)
    waiting: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_history: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list))
    last_wake_evt: str | None = None
    last_completion_evt: str | None = None
    last_message_evt: str | None = None
    # After an empty room, don't try again until then.
    social_cooldown_until: float | None = None
    # When effort was last charged (not saved; the first action pays in full).
    last_effort_at: float | None = None
    # After a chosen resume: frustration must climb past this to force the task down again.
    set_aside_floor: float | None = None
    # Fruitless browser actions in a row (not saved; a restart starts fresh).
    wall_streak: int = 0
    # The most recent validator_reject event (not saved).
    last_refusal: Any = None
