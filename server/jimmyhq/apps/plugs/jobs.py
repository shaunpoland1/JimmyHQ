"""Jobs: a full app's work in the background, on a schedule.

The app says at start which jobs it has (``PUT /plugs``: ``{name, every}`` in seconds, or
``{name, every_setting}``, a whole-number setting of its own in minutes). The worker's
clock owns the schedule: ``tick`` runs every few seconds and, for each job that is due,
queues one ``job`` delivery, ``{name}``, in the app's inbox. The app runs it and answers.

- Only with ``jobs`` granted, read afresh at every tick, and only while the app is
  switched on and asking for work: a stopped program is not handed a run it cannot do.
- One run at a time: a job whose last run has not been answered is not queued again.
- A run that is not answered within ``apps.job_timeout_s`` is given up (the delivery is
  expired, so it is not handed over again) and recorded as a failure that says so. The
  next run comes at its usual time.

``status(core, app_id)`` is each job's last run for the Apps screen.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import bridge, inbox
from jimmyhq.apps.permissions import FULL, allows
from jimmyhq.apps.plugs.state import state
from jimmyhq.ids import now_ms
from jimmyhq.settings import SettingError

if TYPE_CHECKING:
    from jimmyhq.apps.bridge import Installed
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.plugs.jobs")

MIN_EVERY_S = 5  # the worker's clock ticks every five seconds
TIMEOUT_SETTING = "apps.job_timeout_s"


def may_run_jobs(app: Installed | None) -> bool:
    """An app's jobs run only while it is installed, switched on, full, and granted
    ``jobs`` by the owner. The grant is read at every tick, never remembered."""
    return (app is not None and app.enabled and app.manifest.tier == FULL
            and allows(app.granted, "jobs"))


def every_s(core: Core, app: Installed, job: dict[str, Any]) -> int:
    """How often a job runs, in seconds: its own ``every``, or the minutes its setting
    says (the setting's default until the owner has changed it). Never more often than
    the worker's clock can tell."""
    if job.get("every") is not None:
        seconds = int(job["every"])
    else:
        key = job.get("every_setting")
        declared = {s.key: s for s in app.manifest.settings}
        minutes = declared[key].default if key in declared else 60
        try:
            minutes = core.settings.get(app.manifest.setting_key(key))
        except SettingError:
            pass  # not declared in this process yet: its default
        seconds = int(minutes) * 60
    return max(MIN_EVERY_S, seconds)


def is_due(last_queued: int | None, every: int, now: int) -> bool:
    """A job runs at once the first time, then once ``every`` seconds after its last
    run began."""
    return last_queued is None or now - last_queued >= every * 1000


def timed_out(queued: int | None, timeout_s: int, now: int) -> bool:
    """A run handed over this long ago and not answered is given up."""
    return queued is not None and now - queued >= timeout_s * 1000


def did_not_answer(timeout_s: int) -> str:
    return (f"The app did not finish this run within {timeout_s} seconds, so JimmyHQ "
            "stopped waiting for it. The next run comes at its usual time.")


def settle(core: Core, app_id: str, name: str, row: dict[str, Any], now: int) -> bool:
    """Look at a run that was handed over. True while it is still going."""
    found = inbox.answer_of(core, row["delivery"])
    if found is not None:
        state(core).job_finished(app_id, name, found.ok, found.error, now)
        return False
    timeout_s = core.settings.get(TIMEOUT_SETTING)
    if timed_out(row["queued"], timeout_s, now):
        inbox.expire(core, row["delivery"])
        state(core).job_finished(app_id, name, False, did_not_answer(timeout_s), now)
        log.warning("app %s: job %s did not answer within %ss", app_id, name, timeout_s)
        return False
    return True


def tick_app(core: Core, app_id: str, *, now: int | None = None) -> list[str]:
    """Queue every job of one app that is due. Returns the names queued."""
    now = now_ms() if now is None else now
    app = bridge.find_app(core, app_id)
    if app is None:
        return []
    may_queue = may_run_jobs(app) and inbox.responding(core, app_id, now=now)
    queued = []
    for job in inbox.plugs(core, app_id).get("jobs") or []:
        name = job["name"]
        row = state(core).job(app_id, name)
        if row and row.get("delivery") and settle(core, app_id, name, row, now):
            continue  # still going: never two runs of one job at once
        if not may_queue:
            continue
        if not is_due(row["queued"] if row else None, every_s(core, app, job), now):
            continue
        delivery = inbox.deliver(core, app_id, "job", {"name": name}, key=f"job:{name}")
        state(core).job_queued(app_id, name, delivery, now)
        queued.append(name)
    return queued


def status(core: Core, app_id: str) -> list[dict[str, Any]]:
    """Each job the app offers, with its last run: ``{name, every_s, last_run, finished,
    ok, error, running}``. Times are unix ms, UTC."""
    app = bridge.find_app(core, app_id)
    if app is None:
        return []
    rows = {r["name"]: r for r in state(core).jobs(app_id)}
    out = []
    for job in inbox.plugs(core, app_id).get("jobs") or []:
        row = rows.get(job["name"], {})
        out.append({"name": job["name"], "every_s": every_s(core, app, job),
                    "last_run": row.get("queued"), "finished": row.get("finished"),
                    "ok": None if row.get("ok") is None else bool(row["ok"]),
                    "error": row.get("error"), "running": bool(row.get("delivery"))})
    return out
