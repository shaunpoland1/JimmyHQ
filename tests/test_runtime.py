from __future__ import annotations

import threading

import pytest

from jimmyhq import service, supervisor, worker
from jimmyhq.scheduler import Scheduler


def test_a_job_runs_when_due_and_not_again_until_its_interval_has_passed(core):
    runs = []
    scheduler = Scheduler(core.metrics)
    scheduler.every("hourly", 3600, lambda: runs.append(1))
    assert scheduler.tick() == ["hourly"]
    assert scheduler.tick() == []
    assert runs == [1]


def test_a_job_interval_is_measured_from_its_last_run_even_after_a_restart(core):
    Scheduler(core.metrics).every("hourly", 3600, lambda: None)
    first = Scheduler(core.metrics)
    first.every("hourly", 3600, lambda: None)
    first.tick()
    after_restart = Scheduler(core.metrics)
    after_restart.every("hourly", 3600, lambda: None)
    assert after_restart.due() == []


def test_a_failing_job_is_recorded_and_does_not_stop_the_others(core):
    ran = []
    scheduler = Scheduler(core.metrics)
    scheduler.every("a_broken", 60, lambda: 1 / 0)
    scheduler.every("b_fine", 60, lambda: ran.append(1))
    assert scheduler.tick() == ["a_broken", "b_fine"]
    jobs = {j["name"]: j for j in core.metrics.jobs()}
    assert jobs["a_broken"]["ok"] is False and "ZeroDivisionError" in jobs["a_broken"]["error"]
    assert jobs["b_fine"]["ok"] is True and ran == [1]


def test_the_worker_beats_and_stops_when_asked(cfg):
    from jimmyhq.core import open_core
    from jimmyhq.metrics import Metrics

    stop = threading.Event()
    thread = threading.Thread(target=worker.run, args=(stop, open_core(cfg)))
    thread.start()
    metrics = Metrics(cfg.metrics_db)
    try:
        for _ in range(100):
            if metrics.processes(("worker",))[0]["ok"]:
                break
            threading.Event().wait(0.05)
        assert metrics.processes(("worker",))[0]["ok"]
    finally:
        stop.set()
        thread.join(5)
        metrics.close()
    assert not thread.is_alive()


def test_restart_backoff_grows_and_is_capped():
    assert [supervisor.backoff(n) for n in (0, 1, 2, 3, 4, 5, 6, 20)] == [0, 1, 2, 4, 8, 16, 30, 30]


def test_a_service_definition_is_rendered_for_each_operating_system(tmp_path):
    mac = service.render("darwin", tmp_path, env={})
    assert mac.path == tmp_path / "Library" / "LaunchAgents" / "ai.jimmyhq.plist"
    assert "<string>jimmyhq</string>" in mac.content and "<string>run</string>" in mac.content
    assert "bootstrap" in mac.enable and "bootout" in mac.disable
    linux = service.render("linux", tmp_path, env={})
    assert linux.path.name == "jimmyhq.service" and "Restart=always" in linux.content
    windows = service.render("win32", tmp_path, env={}, account="BOX\\sam")
    assert windows.path.name == "jimmyhq-task.xml" and windows.encoding == "utf-16"
    # Scoped to the person installing it, so no administrator rights are needed.
    assert windows.content.count("<UserId>BOX\\sam</UserId>") == 2
    assert "cmd.exe" not in windows.content and "/End" in windows.disable
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in windows.content
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in windows.content
    assert "/XML" in windows.enable and '\\"' not in windows.enable  # no shell-specific quoting
    for spec in (mac, linux, windows):
        assert str(tmp_path) not in spec.content  # nothing about the user's home is baked in


def test_a_custom_data_folder_set_at_install_time_is_carried_into_the_service(tmp_path):
    env = {"JIMMYHQ_DATA": "/srv/jimmy data", "JIMMYHQ_SUPERVISED": "1", "PATH": "/bin"}
    mac = service.render("darwin", tmp_path, env=env)
    assert "<key>JIMMYHQ_DATA</key><string>/srv/jimmy data</string>" in mac.content
    linux = service.render("linux", tmp_path, env=env)
    assert 'Environment="JIMMYHQ_DATA=/srv/jimmy data"' in linux.content
    windows = service.render("win32", tmp_path, env=env, account="BOX\\sam")
    (saved,) = windows.extra_files.values()
    assert '"JIMMYHQ_DATA": "/srv/jimmy data"' in saved
    for text in (mac.content, linux.content, windows.content, saved):
        assert "JIMMYHQ_SUPERVISED" not in text and "PATH" not in text


def test_settings_saved_for_the_windows_task_are_applied_without_overriding_the_shell(
    tmp_path, monkeypatch
):
    folder = tmp_path / "AppData" / "Local" / "JimmyHQ"
    folder.mkdir(parents=True)
    (folder / "service-env.json").write_text(
        '{"JIMMYHQ_DATA": "saved", "JIMMYHQ_PORT": "9000", "OTHER": "ignored"}')
    monkeypatch.delenv("JIMMYHQ_DATA", raising=False)
    monkeypatch.setenv("JIMMYHQ_PORT", "8000")
    monkeypatch.delenv("OTHER", raising=False)
    service.load_saved_env(tmp_path)
    import os

    assert os.environ["JIMMYHQ_DATA"] == "saved"
    assert os.environ["JIMMYHQ_PORT"] == "8000"
    assert "OTHER" not in os.environ
    service.load_saved_env(tmp_path / "nowhere")  # a missing file is not an error


def test_running_as_a_module_passes_on_the_exit_code(tmp_path):
    import os
    import subprocess
    import sys

    (tmp_path / "config.toml").write_text("[server\n", encoding="utf-8")
    env = {**os.environ, "JIMMYHQ_DATA": str(tmp_path)}
    result = subprocess.run([sys.executable, "-m", "jimmyhq", "data-dir"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 2 and "cannot start" in result.stderr


def test_an_existing_data_folder_keeps_the_permissions_its_owner_set(tmp_path, monkeypatch):
    import sys

    from jimmyhq import config

    if sys.platform == "win32":
        return
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o750)
    monkeypatch.setenv("JIMMYHQ_DATA", str(shared))
    config.data_dir()
    assert shared.stat().st_mode & 0o777 == 0o750
    fresh = tmp_path / "fresh"
    monkeypatch.setenv("JIMMYHQ_DATA", str(fresh))
    config.data_dir()
    assert fresh.stat().st_mode & 0o777 == 0o700


def test_a_second_supervisor_on_the_same_data_folder_is_refused(tmp_path):
    first = supervisor.take_lock(tmp_path)
    try:
        with pytest.raises(supervisor.AlreadyRunning, match="already running"):
            supervisor.take_lock(tmp_path)
    finally:
        first.close()
    supervisor.take_lock(tmp_path).close()  # free again once the first one stops


def test_a_child_stops_when_its_supervisor_is_killed_outright(tmp_path):
    """The parent holds the child's stdin; when the parent dies the child must exit."""
    import os
    import signal
    import subprocess
    import sys
    import time

    child_code = (
        "import os, sys, time; os.environ['JIMMYHQ_SUPERVISED']='1';"
        "from jimmyhq.supervisor import watch_parent;"
        "watch_parent(lambda: os._exit(0)); print(os.getpid(), flush=True); time.sleep(60)"
    )
    parent_code = (
        "import subprocess, sys, time;"
        f"p = subprocess.Popen([sys.executable, '-c', {child_code!r}], stdin=subprocess.PIPE);"
        "time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE)
    try:
        child_pid = int(parent.stdout.readline())
    finally:
        parent.kill()  # no chance to clean up, like kill -9 or Task Manager
        parent.wait()
    if sys.platform == "win32":
        return  # liveness check below is POSIX; the mechanism itself is the same
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(child_pid, signal.SIGKILL)
    raise AssertionError("the child outlived its supervisor")


def test_a_config_file_saved_with_a_byte_order_mark_still_loads(tmp_path, monkeypatch):
    from jimmyhq import config

    monkeypatch.setenv("JIMMYHQ_DATA", str(tmp_path))
    monkeypatch.delenv("JIMMYHQ_PORT", raising=False)
    (tmp_path / "config.toml").write_text(
        '[server]\nport = 9001\nallowed_hosts = ["Box.Example"]\n', encoding="utf-8-sig")
    cfg = config.load()
    assert (cfg.port, cfg.allowed_hosts, cfg.remote_setup) == (9001, ("box.example",), False)


def test_a_broken_config_file_is_reported_by_name_instead_of_a_traceback(tmp_path, monkeypatch):
    from jimmyhq import cli

    monkeypatch.setenv("JIMMYHQ_DATA", str(tmp_path))
    (tmp_path / "config.toml").write_text("[server\nport = ", encoding="utf-8")
    assert cli.main(["data-dir"]) == 2


def test_two_processes_can_open_a_fresh_data_folder_at_the_same_moment(tmp_path):
    import subprocess
    import sys

    code = ("import sys, time; from jimmyhq import config; from jimmyhq.core import open_core;"
            "t=float(sys.argv[2]); time.sleep(max(0, t-time.time()));"
            "open_core(config.Config(data=__import__('pathlib').Path(sys.argv[1]),"
            "host='127.0.0.1', port=0)).close()")
    import time

    for attempt in range(5):
        folder = tmp_path / str(attempt)
        folder.mkdir()
        start = str(time.time() + 0.6)
        procs = [subprocess.Popen([sys.executable, "-c", code, str(folder), start],
                                  stderr=subprocess.PIPE) for _ in range(3)]
        for p in procs:
            _, err = p.communicate()
            assert p.returncode == 0, err.decode()


def test_a_supervised_child_that_exits_by_itself_returns_its_own_code():
    """Its supervisor is still there, holding the pipe; the child just leaves."""
    import os
    import subprocess
    import sys

    code = ("import sys, time; from jimmyhq.supervisor import watch_parent;"
            "watch_parent(); time.sleep(0.3); sys.exit(3)")  # the watcher is waiting by now
    env = {**os.environ, supervisor.SUPERVISED_ENV: "1"}
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
    try:
        err = child.stderr.read()  # ends when the child has gone; the pipe stays open
        assert child.wait() == 3, err.decode(errors="replace")
        assert b"Fatal Python error" not in err
    finally:
        child.stdin.close()
        child.stderr.close()


def _a_listener():
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener, listener.getsockname()[1]


def test_a_port_another_program_listens_on_is_named_with_what_to_do():
    listener, port = _a_listener()
    try:
        sentence = supervisor.port_in_use("127.0.0.1", port)
    finally:
        listener.close()
    assert sentence is not None and f"Port {port} is already in use" in sentence
    assert "JIMMYHQ_PORT" in sentence and "did not start" in sentence
    assert supervisor.port_in_use("127.0.0.1", port) is None  # free once it is let go


def test_an_address_that_cannot_be_looked_up_is_left_for_the_web_process_to_report(monkeypatch):
    import socket

    def no_answer(*_args, **_kwargs):
        raise socket.gaierror("no such name")

    monkeypatch.setattr(socket, "getaddrinfo", no_answer)
    assert supervisor.port_in_use("nowhere.invalid", 8440) is None


def test_starting_on_a_taken_port_ends_with_the_sentence_and_exit_1(tmp_path):
    import os
    import subprocess
    import sys

    listener, port = _a_listener()
    try:
        env = {**os.environ, "JIMMYHQ_DATA": str(tmp_path), "JIMMYHQ_HOST": "127.0.0.1",
               "JIMMYHQ_PORT": str(port)}
        done = subprocess.run([sys.executable, "-m", "jimmyhq", "run"], env=env,
                              capture_output=True, text=True, timeout=120)
    finally:
        listener.close()
    assert done.returncode == 1
    assert f"Port {port} is already in use" in done.stderr and "JIMMYHQ_PORT" in done.stderr
    assert "Traceback" not in done.stderr and "is starting" not in done.stdout
    assert not list(tmp_path.glob("logs/web*"))  # no child was started


def test_the_restart_message_names_both_of_the_childs_logs(tmp_path):
    said = supervisor.restart_message("web", 1, 3.2, 4.0, tmp_path)
    assert "web exited with code 1 after 3s; restarting in 4s" in said
    assert "web.log" in said and "web.err.log" in said and str(tmp_path) in said


def test_the_start_lines_do_not_say_jimmyhq_is_up_before_it_is():
    from jimmyhq import cli
    from jimmyhq.web.dist import NOT_BUILT

    first, *rest = cli.start_lines("http://127.0.0.1:8440", "somewhere", built=True)
    assert "will be at http://127.0.0.1:8440 once it is ready" in first
    assert " is at " not in first and "running" not in first
    assert rest == ["Data: somewhere"]
    assert cli.start_lines("http://127.0.0.1:8440", "somewhere", built=False)[-1] == NOT_BUILT


def test_port_0_is_refused_with_a_sentence_wherever_it_is_set(tmp_path, monkeypatch):
    from jimmyhq import cli, config

    monkeypatch.setenv("JIMMYHQ_DATA", str(tmp_path))
    monkeypatch.setenv("JIMMYHQ_PORT", "0")
    with pytest.raises(config.ConfigError, match="between 1 and 65535, not 0"):
        config.load()
    assert cli.main(["data-dir"]) == 2
    monkeypatch.delenv("JIMMYHQ_PORT")
    (tmp_path / "config.toml").write_text("[server]\nport = 0\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="JIMMYHQ_PORT"):
        config.load()
