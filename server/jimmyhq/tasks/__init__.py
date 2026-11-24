"""The tasks module: one list of work for the owner and, later, their agents.

Other modules call ``service(core)`` and use the ``TaskService`` it returns;
the routes under ``/api/tasks`` are thin wrappers over the same object.
See ``docs/tasks.md``.
"""

from __future__ import annotations

from fastapi import APIRouter

from jimmyhq.core import Core
from jimmyhq.tasks.service import TaskError, TaskService, service

__all__ = ["TaskError", "TaskService", "build", "service"]


def build(core: Core) -> APIRouter:
    from jimmyhq.tasks.routes import build_router

    return build_router(core)
