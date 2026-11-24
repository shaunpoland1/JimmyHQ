"""The worker process: scheduled jobs, kept out of the web process so a slow
job can never make the app feel slow."""

from __future__ import annotations

import logging
import threading

from jimmyhq import modules
from jimmyhq.core import Core, open_core
from jimmyhq.scheduler import Scheduler

log = logging.getLogger("jimmyhq.worker")
TICK_S = 5


def build_scheduler(core: Core) -> Scheduler:
    """Each module registers its own jobs; see jimmyhq/modules.py."""
    scheduler = Scheduler(core.metrics)
    modules.register_jobs(core, scheduler)
    return scheduler


def run(stop: threading.Event | None = None, core: Core | None = None) -> None:
    stop = stop or threading.Event()
    core = core or open_core()
    scheduler = build_scheduler(core)
    log.info("worker started")
    try:
        while not stop.is_set():
            core.metrics.beat("worker")
            scheduler.tick()
            stop.wait(TICK_S)
    finally:
        core.close()
