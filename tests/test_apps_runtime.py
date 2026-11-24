"""Running a full app's program: started with no shell, handed its token on a pipe, with
a rebuilt environment, held back when it keeps crashing, stopped when it should not run.

These start real child processes with this interpreter, so they run the same on macOS,
Linux and Windows. Waits are generous for a slow machine, and every wait polls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from apps_full_helpers import client_for, full_app, install_full
from apps_install_helpers import example, install_folder
from jimmyhq.apps import inbox, install, runtime, store

PATIENCE_S = 60.0

# Stands in for an app's program. It writes what it was given, then waits for the
# supervisor's pipe to close, unless its data folder says to crash or to ignore that.
PROBE = '''
import json, os, sys, time
from pathlib import Path

data = Path(os.environ["JIMMYHQ_APP_DATA"])
first = sys.stdin.readline()
with open(data / "starts.log", "a", encoding="utf-8") as starts:
    starts.write("start\\n")
(data / "probe.json").write_text(json.dumps({
    "first": first, "env": dict(os.environ), "argv": sys.argv, "cwd": os.getcwd(),
    "pid": os.getpid()}), encoding="utf-8")
print("probe started", flush=True)
mode = (data / "mode").read_text(encoding="utf-8") if (data / "mode").exists() else ""
if mode == "crash":
    sys.exit(3)
if mode == "stubborn":
    time.sleep(600)
sys.stdin.read()
print("probe stopping", flush=True)


def app():
    pass
'''


def wait_for(check, what: str, children=None, timeout: float = PATIENCE_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if children is not None:
            children.tick()
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"gave up waiting for {what}")


@pytest.fixture
def probe(core, tmp_path):
    install_full(core, tmp_path, "probe", module=PROBE)
    runtime.publish(core)
    return "probe"


@pytest.fixture
def children(core, tmp_path):
    kids = runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth, grace_s=5)
    yield kids
    kids.stop_all()


def data_of(core, app_id: str = "probe") -> Path:
    return store.data_dir(core, app_id)


def seen(core, app_id: str = "probe") -> dict | None:
    path = data_of(core, app_id) / "probe.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def starts(core, app_id: str = "probe") -> int:
    path = data_of(core, app_id) / "starts.log"
    return path.read_text(encoding="utf-8").count("start") if path.exists() else 0


# --- what should run ---------------------------------------------------------------------


def test_only_switched_on_full_apps_are_listed_to_run(core, tmp_path):
    install_full(core, tmp_path, "alpha")
    install_full(core, tmp_path, "beta")
    install_folder(core, example(tmp_path, "water"))
    install.disable(core, "beta")
    listed = json.loads(runtime.run_file(core.config.data).read_text(encoding="utf-8"))
    assert [a["id"] for a in listed["apps"]] == ["alpha"]
    assert listed["apps"][0]["python"] == sys.executable
    assert listed["crash_limit"] == core.settings.get("apps.crash_limit")
    install.remove(core, "alpha")
    listed = json.loads(runtime.run_file(core.config.data).read_text(encoding="utf-8"))
    assert listed["apps"] == []


def test_the_owners_restart_raises_the_generation_and_nothing_else(core, probe):
    before = runtime.publish(core)["apps"][0]
    after = runtime.publish(core, restart="probe")["apps"][0]
    assert after == before | {"generation": before["generation"] + 1}


def test_a_sandboxed_or_switched_off_app_cannot_be_restarted(core, tmp_path, probe):
    install_folder(core, example(tmp_path, "water"))
    with pytest.raises(store.AppError):
        runtime.restart(core, "water")
    install.disable(core, "probe")
    with pytest.raises(store.AppError):
        runtime.restart(core, "probe")


@pytest.mark.parametrize("change", [
    {"id": "../../etc"},
    {"version": "../../../elsewhere-0123abcd"},
    {"version": "1.0.0-0123abcd"},  # a folder that is not there
    {"entry": "os:system; rm"},
    {"python": "/bin/sh"},
    {"python": "python"},
    {"generation": -1},
])
def test_an_entry_in_run_json_that_jimmyhq_would_not_write_is_not_started(core, probe, change):
    entry = runtime.publish(core)["apps"][0]
    with pytest.raises(runtime.SpecRefused):
        runtime.check_spec(core.config.data, entry | change)


def test_an_interpreter_inside_the_apps_own_environments_is_accepted(core, probe):
    entry = runtime.publish(core)["apps"][0]
    venv = store.app_dir(core, "probe") / "venvs" / entry["version"]
    own = runtime.venv_python(venv)
    own.parent.mkdir(parents=True)
    own.write_bytes(b"")
    assert runtime.python_for(core, "probe", entry["version"]) == str(own)
    assert runtime.check_spec(core.config.data, entry | {"python": str(own)}).python == str(own)


# --- how a program is started -----------------------------------------------------------------


def test_a_program_is_started_with_this_interpreter_and_never_a_shell(core, probe, tmp_path):
    calls = []

    class Recorder:
        def __init__(self, args, **kw):
            calls.append((args, kw))
            self.stdin = open(os.devnull, "wb")  # noqa: SIM115
            self.pid, self.returncode = 1, None

        def poll(self):
            return None

    kids = runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth, popen=Recorder)
    kids.tick()
    [(args, kw)] = calls
    assert args == [sys.executable, "-m", "jimmyhq_app", "probe_main:app"]
    assert kw["shell"] is False and kw["stdin"] == subprocess.PIPE
    assert Path(kw["cwd"]).name.startswith("1.0.0-")
    kids.children["probe"].proc.stdin.close()
    kids.children["probe"].log.close()  # Windows cannot delete an open file


def test_the_program_gets_its_token_on_its_pipe_and_nowhere_else(core, probe, children):
    got = wait_for(lambda: seen(core), "the probe to start", children)
    handed = json.loads(got["first"])["token"]
    assert core.auth.check_app_token(handed).actor == "app:probe"
    assert handed not in json.dumps(got["env"]) and handed not in " ".join(got["argv"])


def test_the_programs_environment_is_rebuilt_not_inherited(core, probe, children, monkeypatch):
    monkeypatch.setenv("JIMMYHQ_TEST_SECRET", "should-not-pass")
    got = wait_for(lambda: seen(core), "the probe to start", children)
    env = got["env"]
    assert "JIMMYHQ_TEST_SECRET" not in env
    assert env["JIMMYHQ_APP_ID"] == "probe" and env["JIMMYHQ_SUPERVISED"] == "1"
    assert env["JIMMYHQ_APP_API"] == "http://127.0.0.1:9"
    assert Path(env["JIMMYHQ_APP_DATA"]) == data_of(core).resolve()
    assert "JIMMYHQ_APP_TOKEN" not in env
    assert Path(got["cwd"]).resolve() == runtime.check_spec(
        core.config.data, runtime.publish(core)["apps"][0]).folder


def test_what_a_program_prints_is_kept_in_its_log(core, probe, children):
    wait_for(lambda: seen(core), "the probe to start", children)
    path = runtime.log_file(children.log_dir, "probe")
    wait_for(lambda: "probe started" in path.read_text(encoding="utf-8", errors="replace"),
             "the probe's output", children)


def test_a_new_start_revokes_the_token_of_the_one_before(core, probe, children):
    first = json.loads(wait_for(lambda: seen(core), "the first start", children)["first"])
    runtime.publish(core, restart="probe")
    wait_for(lambda: starts(core) >= 2, "the second start", children)
    # the second start's own record can land a moment after the count turns over
    def later():
        got = seen(core)
        told = json.loads(got["first"]) if got else None
        return told if told and told["token"] != first["token"] else None

    second = wait_for(later, "the second start's record", children)
    assert core.auth.check_app_token(first["token"]) is None
    assert core.auth.check_app_token(second["token"]) is not None


# --- stopping ----------------------------------------------------------------------------------


def test_a_program_that_should_no_longer_run_is_stopped_and_its_token_revoked(core, probe,
                                                                             children):
    token = json.loads(wait_for(lambda: seen(core), "the probe to start", children)["first"])
    install.disable(core, "probe")
    wait_for(lambda: not children.children and not children.leaving, "the probe to stop",
             children)
    assert core.auth.check_app_token(token["token"]) is None
    log = runtime.log_file(children.log_dir, "probe").read_text(encoding="utf-8")
    assert "probe stopping" in log  # it stopped itself when its pipe closed


def test_a_program_that_ignores_its_pipe_closing_is_ended_after_the_grace(core, probe,
                                                                         children):
    data_of(core).mkdir(parents=True, exist_ok=True)
    (data_of(core) / "mode").write_text("stubborn", encoding="utf-8")
    children.grace_s = 0.5
    wait_for(lambda: seen(core), "the probe to start", children)
    install.disable(core, "probe")
    wait_for(lambda: not children.leaving and not children.children, "the probe to be ended",
             children)


def test_stopping_everything_ends_every_program(core, probe, children):
    wait_for(lambda: seen(core), "the probe to start", children)
    proc = children.children["probe"].proc
    children.stop_all()
    assert proc.poll() is not None


# --- crashing ------------------------------------------------------------------------------------


def test_a_program_that_keeps_exiting_soon_after_starting_is_held_back(core, probe, tmp_path):
    class Crasher:
        made = 0

        def __init__(self, args, **kw):
            Crasher.made += 1
            self.stdin = open(os.devnull, "wb")  # noqa: SIM115
            self.pid, self.returncode = 100 + Crasher.made, 3

        def poll(self):
            return 3

    now = [0.0]
    kids = runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth, popen=Crasher, clock=lambda: now[0])
    for _ in range(200):
        kids.tick()
        now[0] += 5
    limit = core.settings.get("apps.crash_limit")
    assert Crasher.made == limit
    assert kids.children["probe"].state == "crashing"
    status = json.loads(runtime.status_file(core.config.data).read_text(encoding="utf-8"))
    assert status["apps"]["probe"]["state"] == "crashing"
    assert runtime.health(core, "probe")["label"] == "Held back after crashing"
    runtime.publish(core, restart="probe")  # the owner's Restart gives it another go
    kids.tick()
    assert Crasher.made == limit + 1


def test_a_program_that_ran_a_while_before_exiting_is_not_counted_as_crashing():
    assert not runtime.quick_exits_end_retries(4, 5)
    assert runtime.quick_exits_end_retries(5, 5)


def test_a_real_program_that_crashes_is_restarted_with_backoff(core, probe, tmp_path):
    data_of(core).mkdir(parents=True, exist_ok=True)
    (data_of(core) / "mode").write_text("crash", encoding="utf-8")
    core.settings.set("apps.crash_limit", 2, actor="owner")
    runtime.publish(core)
    kids = runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth, wait=lambda failures: 0.0)
    try:
        wait_for(lambda: kids.children.get("probe") is not None
                 and kids.children["probe"].state == "crashing", "the probe to be held", kids)
        assert starts(core) == 2
    finally:
        kids.stop_all()


# --- how it is, for the screen -------------------------------------------------------------


def test_health_follows_the_programs_heartbeat(core, probe, children):
    wait_for(lambda: seen(core), "the probe to start", children)
    children.tick()
    assert runtime.health(core, "probe")["state"] == "starting"
    inbox.take(core, "probe")  # the program's long-poll
    assert runtime.health(core, "probe")["label"] == "Running"
    later = int(time.time() * 1000) + (core.settings.get("apps.not_responding_s") + 5) * 1000
    status = json.loads(runtime.status_file(core.config.data).read_text(encoding="utf-8"))
    status["written"] = later
    runtime.write_json(runtime.status_file(core.config.data), status)
    assert runtime.health(core, "probe", now=later)["label"] == "Not responding"


def test_the_owner_reads_health_and_the_log_and_a_token_cannot(core, probe, children):
    wait_for(lambda: seen(core), "the probe to start", children)
    path = runtime.log_file(children.log_dir, "probe")
    wait_for(lambda: path.exists() and "probe started" in path.read_text(encoding="utf-8"),
             "the probe's output", children)
    with client_for(core) as web:
        token = core.auth.mint_app_token("probe")
        assert web.get("/api/apps/probe/health",
                       headers={"authorization": f"Bearer {token}"}).status_code == 401
        assert web.post("/api/auth/setup", json={"name": "Sam",
                                                 "password": "correct horse battery"}).is_success
        assert web.get("/api/apps/probe/health").json()["state"] in ("starting", "running")


def test_the_api_address_is_this_machines_own():
    assert runtime.api_url("0.0.0.0", 8440) == "http://127.0.0.1:8440"  # noqa: S104
    assert runtime.api_url("::", 8440) == "http://[::1]:8440"
    assert runtime.api_url("192.0.2.4", 8440) == "http://192.0.2.4:8440"


def test_the_full_app_example_names_an_entry_the_runtime_accepts(core, tmp_path):
    folder = full_app(tmp_path, "entrytest")
    install_folder(core, folder)
    [entry] = runtime.publish(core)["apps"]
    assert runtime.check_spec(core.config.data, entry).entry == "entrytest_main:app"
