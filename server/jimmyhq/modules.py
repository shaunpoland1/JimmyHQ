"""The core's feature modules, and how each one plugs in.

A module is a package ``jimmyhq.<name>`` that may provide:

- ``kinds.py``     event kinds, registered on import
- ``settings.py``  settings, declared on import
- ``build(core)``  in ``__init__.py``: returns the module's ``APIRouter``
- ``jobs(core, scheduler)`` in ``__init__.py``: registers scheduled jobs (worker)
- ``async shutdown(core)`` in ``__init__.py``: finish or record work in flight
  when the web process is asked to stop

Adding a module means adding its name here. Nothing else in the core changes.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core
    from jimmyhq.scheduler import Scheduler

MODULES = ("tasks", "bench", "models", "speech", "chat", "wall", "memory", "village", "lookup",
           "hands", "door", "remote", "apps", "browser", "nodes", "backup")

_loaded = False


def load() -> None:
    """Import every module's kinds and settings. Safe to call more than once."""
    global _loaded
    if _loaded:
        return
    for name in MODULES:
        for part in ("kinds", "settings"):
            if importlib.util.find_spec(f"jimmyhq.{name}.{part}") is not None:
                importlib.import_module(f"jimmyhq.{name}.{part}")
    _loaded = True


def routers(core: Core) -> list[APIRouter]:
    out = []
    for name in MODULES:
        build = getattr(importlib.import_module(f"jimmyhq.{name}"), "build", None)
        if build is not None:
            router = build(core)
            if router is not None:
                out.append(router)
    return out


def register_jobs(core: Core, scheduler: Scheduler) -> None:
    for name in MODULES:
        jobs = getattr(importlib.import_module(f"jimmyhq.{name}"), "jobs", None)
        if jobs is not None:
            jobs(core, scheduler)


async def shutdown(core: Core) -> None:
    """Give each module a moment to settle work in flight before the process ends."""
    for name in MODULES:
        hook = getattr(importlib.import_module(f"jimmyhq.{name}"), "shutdown", None)
        if hook is not None:
            await hook(core)
