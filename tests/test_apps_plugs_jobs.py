"""Jobs: a full app's work on a schedule, queued by the worker's clock through the app's
inbox. Tried from the attacker's side too: jobs without the permission, a grant taken
away, a job that never answers, and one that would pile up."""

from __future__ import annotations

import dataclasses

import pytest

from apps_plugs_helpers import answer, client_for, handed, install_full, offer, running, speak
from jimmyhq.apps import inbox, install
from jimmyhq.apps.plugs import jobs
from jimmyhq.ids import now_ms

MINUTE = 60_000


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def worker(core, tmp_path):
    install_full(core, tmp_path, "chores_bot", permissions=["jobs"],
                 settings=[{"key": "every_minutes", "type": "int", "default": 30, "min": 1,
                            "label": "Every (minutes)", "help": "How often."}])
    return "chores_bot"


def test_a_job_is_queued_at_once_the_first_time_and_handed_to_its_app(web, core, worker):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 60}])
    running(core, worker)
    assert jobs.tick_app(core, worker) == ["tidy"]
    [got] = handed(app)
    assert got["type"] == "job" and got["data"] == {"name": "tidy"}


def test_a_job_runs_again_only_once_its_interval_has_passed(web, core, worker):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 60}])
    running(core, worker)
    start = now_ms()
    jobs.tick_app(core, worker, now=start)
    [got] = handed(app)
    answer(app, got["id"], None)
    assert jobs.tick_app(core, worker, now=start + 59_000) == []
    assert jobs.tick_app(core, worker, now=start + 60_000) == ["tidy"]


def test_a_job_is_never_queued_twice_while_its_last_run_is_unanswered(web, core, worker):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 5}])
    running(core, worker)
    start = now_ms()
    jobs.tick_app(core, worker, now=start)
    for later in (10_000, 20_000, 30_000):
        assert jobs.tick_app(core, worker, now=start + later) == []
    assert inbox.pending(core, worker) == 1


def test_a_job_that_never_answers_is_given_up_and_recorded_as_not_answering(web, core,
                                                                            worker):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 5}])
    running(core, worker)
    start = now_ms()
    jobs.tick_app(core, worker, now=start)
    [got] = handed(app)
    timeout = core.settings.get("apps.job_timeout_s") * 1000
    inbox.inbox(core).beat(worker, start + timeout)  # still asking for work then
    assert jobs.tick_app(core, worker, now=start + timeout) == ["tidy"]
    [status] = jobs.status(core, worker)
    assert status["ok"] is False and "did not finish" in status["error"]
    # The run given up is not handed over again, and its late answer is not taken.
    assert inbox.answer(core, worker, got["id"], ok=True, result=None) is False


def test_a_jobs_answer_is_kept_as_its_last_run(web, core, worker):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 60}])
    running(core, worker)
    start = now_ms()
    jobs.tick_app(core, worker, now=start)
    [got] = handed(app)
    answer(app, got["id"], error="The app could not do that: the disk is full")
    jobs.tick_app(core, worker, now=start + 1000)
    [status] = jobs.status(core, worker)
    assert status == {"name": "tidy", "every_s": 60, "last_run": start,
                      "finished": start + 1000, "ok": False,
                      "error": "The app could not do that: the disk is full", "running": False}


def test_a_job_every_so_many_minutes_follows_the_owners_setting(web, core, worker):
    from jimmyhq.apps.plugs import settings as app_settings

    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every_setting": "every_minutes"}])
    app_record = jobs.bridge.find_app(core, worker)
    job = {"name": "tidy", "every": None, "every_setting": "every_minutes"}
    assert jobs.every_s(core, app_record, job) == 30 * 60
    app_settings.declare_all(core)
    core.settings.set("app.chores_bot.every_minutes", 2, actor="owner")
    assert jobs.every_s(core, app_record, job) == 2 * 60


def test_an_app_without_the_jobs_permission_cannot_offer_a_job(web, core, tmp_path):
    install_full(core, tmp_path, "lazy")
    app = speak(web, core, "lazy")
    refused = app.put("/plugs", {"jobs": [{"name": "tidy", "every": 60}]}).json()
    assert not refused["ok"] and refused["error"]["code"] == "not_allowed"
    assert "Nothing was kept" in refused["error"]["message"]
    running(core, "lazy")
    assert jobs.tick_app(core, "lazy") == []


def test_jobs_kept_from_before_do_not_run_once_the_grant_is_gone(web, core, worker,
                                                                 monkeypatch):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 60}])
    running(core, worker)
    found = jobs.bridge.find_app(core, worker)
    assert found is not None and jobs.may_run_jobs(found)
    # As an update that dropped the permission leaves it: the jobs are still kept.
    taken_away = dataclasses.replace(found, granted=())
    monkeypatch.setattr(jobs.bridge, "find_app", lambda _core, _id: taken_away)
    assert jobs.tick_app(core, worker) == []
    assert inbox.pending(core, worker) == 0


def test_no_job_is_queued_for_an_app_that_is_switched_off(web, core, worker):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 60}])
    install.disable(core, worker)
    running(core, worker)
    assert jobs.tick_app(core, worker) == []
    assert inbox.pending(core, worker) == 0


def test_no_job_is_queued_for_a_program_that_is_not_asking_for_work(web, core, worker):
    app = speak(web, core, worker)
    offer(app, jobs=[{"name": "tidy", "every": 60}])
    long_ago = now_ms() - 10 * MINUTE
    inbox.inbox(core).beat(worker, long_ago)
    assert jobs.tick_app(core, worker) == []


def test_a_job_due_is_due_at_once_the_first_time_then_after_its_interval():
    assert jobs.is_due(None, 60, 0)
    assert not jobs.is_due(1000, 60, 60_999)
    assert jobs.is_due(1000, 60, 61_000)


def test_a_run_times_out_only_once_its_whole_timeout_has_passed():
    assert not jobs.timed_out(None, 10, 99_999)
    assert not jobs.timed_out(0, 10, 9_999)
    assert jobs.timed_out(0, 10, 10_000)
