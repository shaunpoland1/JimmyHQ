"""The village world: bodies with needs, a clock, guards and an honest log.

This package is pure. It imports nothing from the rest of JimmyHQ, calls no model and
touches no network. It reads settings through a getter and writes events through a log,
both passed in, so it runs the same in the server, in tests and in the simulator.
"""

from __future__ import annotations

from .agent import Agent
from .clock import Clock, RealClock, SimClock
from .defaults import DEFAULTS, TABLE, Getter, getter
from .events import Event, EventLog, MemoryLog
from .world import World

__all__ = [
    "DEFAULTS", "TABLE", "Agent", "Clock", "Event", "EventLog", "Getter", "MemoryLog",
    "RealClock", "SimClock", "World", "getter",
]
