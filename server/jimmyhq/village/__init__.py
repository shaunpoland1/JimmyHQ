"""The Village: autonomous agents, each with a simulated body.

``world/`` is the body (needs, mood, modes, sleep, guards, the divergence check);
``memory/`` is what an agent keeps and the night that rewrites it; ``record.py`` and
``lens.py`` are the village on the Tape; ``routes.py`` is the owner's API.

    from jimmyhq import village
    world = village.new_world(core)        # wired to settings, the log and the clock
    world.add_agent("ada", temperament.roll(random.Random()))

See ``docs/village.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jimmyhq.village.log import FILE_NAME, SqliteLog
from jimmyhq.village.settings import full_key
from jimmyhq.village.world import Getter, RealClock, World

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core

__all__ = ["SqliteLog", "World", "build", "clock", "log", "new_world", "setting_getter"]


def log(core: Core) -> SqliteLog:
    """This process's handle on the village event log, ``<data>/village.db``."""
    return core.service("village.log", lambda c: SqliteLog(c.config.data / FILE_NAME))


def setting_getter(core: Core) -> Getter:
    """Reads a world setting by its short key (``energy.drain_per_min``), live."""
    return lambda key: core.settings.get(full_key(key))


def clock(core: Core) -> RealClock:
    """Wall-clock time in the owner's time zone, re-read on every use."""
    return RealClock(lambda: core.settings.get("owner.timezone"))


def new_world(core: Core) -> World:
    """A world wired to the settings, the event log and the clock, with nobody in it.

    Bodies live in the memory of whichever process builds this, so exactly one process
    may own them. The caller adds or restores its agents and saves their state."""
    return World(setting_getter(core), log(core), clock(core))


def build(core: Core) -> APIRouter:
    """The owner's API for the Village screen, under ``/api/village``."""
    from jimmyhq.village.routes import build_router
    return build_router(core)
