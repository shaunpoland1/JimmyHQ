"""The scheduler: named jobs on an interval, run by the worker process.

The last run of each job is stored, so an interval is measured from when the
job last ran, not from when the process started. A daily job stays daily
across restarts. A failing job is recorded and never stops the loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from jimmyhq.ids import now_ms
from jimmyhq.metrics import Metrics

log = logging.getLogger("jimmyhq.scheduler")


@dataclass(frozen=True)
class Job:
    name: str
    interval_s: float
    fn: Callable[[], None]


class Scheduler:
    def __init__(self, metrics: Metrics):
        self._metrics = metrics
        self._jobs: dict[str, Job] = {}

    def every(self, name: str, interval_s: float, fn: Callable[[], None]) -> None:
        if name in self._jobs:
            raise ValueError(f"job {name!r} is already scheduled")
        if interval_s <= 0:
            raise ValueError(f"job {name!r} needs a positive interval")
        self._jobs[name] = Job(name, interval_s, fn)

    def due(self, now: int | None = None) -> list[Job]:
        now = now_ms() if now is None else now
        out = []
        for job in self._jobs.values():
            last = self._metrics.job_last_run(job.name)
            if last is None or now - last >= job.interval_s * 1000:
                out.append(job)
        return out

    def tick(self) -> list[str]:
        """Run every job that is due. Returns the names that ran."""
        ran = []
        for job in self.due():
            try:
                job.fn()
            except Exception as exc:  # noqa: BLE001 - one bad job must not stop the rest
                log.exception("job %s failed", job.name)
                self._metrics.job_ran(job.name, ok=False, error=f"{type(exc).__name__}: {exc}")
            else:
                self._metrics.job_ran(job.name, ok=True)
            ran.append(job.name)
        return ran
