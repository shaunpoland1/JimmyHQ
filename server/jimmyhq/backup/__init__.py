"""Backups: snapshots of the data folder on a schedule, to folders the owner chooses, and a
regular restore test that proves a backup opens.

- ``snapshot``: making, listing, checking and pruning snapshots; no running JimmyHQ needed.
- ``service``: the schedule, ``Back up now``, the stored view and the Bench card.
- ``routes``: the status (read) and the two buttons (owner). No route restores.
- ``cli``: ``jimmyhq backup list|verify|now|restore``. Restoring is done there only.

See "Back up and move your data" in docs/architecture.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core
    from jimmyhq.scheduler import Scheduler

__all__ = ["build", "jobs"]


def build(core: Core) -> APIRouter:
    from jimmyhq.backup.routes import build_router  # noqa: PLC0415 - keeps import light

    return build_router(core)


def jobs(core: Core, scheduler: Scheduler) -> None:
    from jimmyhq.backup import service  # noqa: PLC0415

    # Looks every few minutes; what is due is decided from the settings and the record,
    # so a new interval takes effect without a restart.
    scheduler.every("backup.due", service.JOB_EVERY_S, lambda: service.job(core))
