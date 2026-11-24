"""A full app's program is started once JimmyHQ's API answers, and a program that found
no one to talk to at start-up is never counted as crashing. The SDK's first contact is
patient for a short, bounded while.

Real child processes use this interpreter, and the one HTTP server is on 127.0.0.1 in a
thread of the test, so these run the same on macOS, Linux and Windows."""

from __future__ import annotations

import http.server
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from apps_full_helpers import install_full
from jimmyhq.apps import runtime, store

SDK = Path(__file__).resolve().parents[1] / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from jimmyhq_app import App  # noqa: E402
from jimmyhq_app import app as sdk_app  # noqa: E402

PATIENCE_S = 60.0

# Stands in for a program whose SDK found no JimmyHQ: it says it started, then exits as
# the SDK does in that case.
UNREACHABLE = '''
import os, sys
from pathlib import Path

with open(Path(os.environ["JIMMYHQ_APP_DATA"]) / "starts.log", "a", encoding="utf-8") as f:
    f.write("start\\n")
sys.exit(75)


def app():
    pass
'''


class FakeProc:
    made: list[FakeProc] = []

    def __init__(self, args, **kw):
        FakeProc.made.append(self)
        self.stdin = open(os.devnull, "wb")  # noqa: SIM115
        self.pid, self.returncode = 1000 + len(FakeProc.made), None

    def poll(self):
        return self.returncode


@pytest.fixture
def fakes():
    FakeProc.made = []
    yield FakeProc
    for proc in FakeProc.made:
        proc.stdin.close()


@pytest.fixture
def probe(core, tmp_path):
    install_full(core, tmp_path, "probe", module=UNREACHABLE)
    runtime.publish(core)
    return "probe"


def kids_for(core, tmp_path, now, ready, **more):
    return runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth, popen=FakeProc, clock=lambda: now[0],
                               ready=ready, **more)


def test_a_program_is_not_started_until_the_core_answers(core, probe, tmp_path, fakes):
    now, up = [0.0], [False]
    kids = kids_for(core, tmp_path, now, lambda: up[0])
    for _ in range(10):
        kids.tick()
        now[0] += 1
    assert fakes.made == []
    up[0] = True
    kids.tick()
    assert len(fakes.made) == 1 and kids.children["probe"].state == "running"
    kids.stop_all(wait=False)


def test_the_wait_for_the_core_is_bounded_and_said_once_in_the_log(core, probe, tmp_path,
                                                                   fakes, caplog):
    now = [0.0]
    kids = kids_for(core, tmp_path, now, lambda: False, ready_wait_s=30)
    with caplog.at_level(logging.WARNING, logger="jimmyhq.apps.runtime"):
        for _ in range(29):
            kids.tick()
            now[0] += 1
        assert fakes.made == []
        for _ in range(5):
            kids.tick()
            now[0] += 1
    assert len(fakes.made) == 1
    said = [r.getMessage() for r in caplog.records if "has not answered" in r.getMessage()]
    assert len(said) == 1 and "starting app probe anyway" in said[0]
    kids.stop_all(wait=False)


def test_the_core_is_asked_at_most_once_a_tick(core, probe, tmp_path, fakes):
    asked = []
    kids = kids_for(core, tmp_path, [0.0], lambda: asked.append(1) or False)
    kids.tick()
    assert len(asked) == 1


def test_a_check_that_fails_means_not_yet(core, probe, tmp_path, fakes):
    def broken():
        raise OSError("no")

    kids = kids_for(core, tmp_path, [0.0], broken)
    kids.tick()
    assert fakes.made == []


def test_a_start_up_miss_is_never_counted_towards_the_crash_loop(core, probe, tmp_path, fakes):
    now = [0.0]
    kids = kids_for(core, tmp_path, now, lambda: True)
    limit = core.settings.get("apps.crash_limit")
    for _ in range(limit * 4):
        kids.tick()
        fakes.made[-1].returncode = runtime.CORE_UNREACHABLE_EXIT
        now[0] += 2
    kids.tick()
    child = kids.children["probe"]
    assert len(fakes.made) >= limit * 2  # far past where a crash loop is held back
    assert child.failures == 0 and child.state != "crashing"
    kids.stop_all(wait=False)


def test_a_real_crash_still_counts():
    assert runtime.counts_as_crash(1)
    assert runtime.counts_as_crash(None)
    assert not runtime.counts_as_crash(runtime.CORE_UNREACHABLE_EXIT)


def test_the_sdk_and_the_runtime_agree_on_the_exit_for_no_core():
    assert sdk_app.CORE_UNREACHABLE_EXIT == runtime.CORE_UNREACHABLE_EXIT


def test_a_real_program_that_finds_no_core_is_started_again_and_never_held_back(core, probe,
                                                                               tmp_path):
    core.settings.set("apps.crash_limit", 2, actor="owner")
    runtime.publish(core)
    kids = runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth, wait=lambda failures: 0.0)
    log = store.data_dir(core, "probe") / "starts.log"
    try:
        deadline = time.monotonic() + PATIENCE_S
        while time.monotonic() < deadline:
            kids.tick()
            if log.exists() and log.read_text(encoding="utf-8").count("start") >= 4:
                break
            time.sleep(0.05)
        assert log.read_text(encoding="utf-8").count("start") >= 4
        child = kids.children["probe"]
        assert child.state != "crashing" and child.failures == 0
        assert child.last_exit["code"] == runtime.CORE_UNREACHABLE_EXIT
    finally:
        kids.stop_all()


# --- is the core answering? ----------------------------------------------------------------


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the standard library's name
        self.send_response(200 if self.path == runtime.READY_PATH else 404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def served():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(10)


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_core_answers_when_its_health_route_does(served):
    assert runtime.api_answers(served, timeout_s=10)


def test_a_port_no_one_listens_on_is_not_an_answer():
    assert not runtime.api_answers(f"http://127.0.0.1:{_closed_port()}", timeout_s=10)
    assert not runtime.api_answers("not an address")
    assert not runtime.api_answers("https://127.0.0.1:1")


# --- the SDK's first contact ---------------------------------------------------------------


class Flaky:
    """A transport that refuses the connection a few times, then answers."""

    def __init__(self, app_holder, refusals: int):
        self.refusals, self.asked, self.app_holder = refusals, [], app_holder

    def request(self, method, path, *, body=None, params=None, timeout=None):
        self.asked.append((method, path))
        if path == "/inbox":
            self.app_holder[0].stop.set()
            return {"deliveries": []}
        if self.refusals > 0:
            self.refusals -= 1
            raise httpx.ConnectError("refused")
        return {}


def test_the_sdks_first_contact_waits_out_a_core_still_starting(monkeypatch):
    monkeypatch.delenv("JIMMYHQ_SUPERVISED", raising=False)
    holder: list = []
    transport = Flaky(holder, refusals=3)
    app = App(transport=transport)
    holder.append(app)
    app.run()
    assert transport.asked.count(("PUT", "/plugs")) == 4  # three refused, then it answered
    assert ("GET", "/inbox") in transport.asked


def test_the_sdks_first_contact_gives_up_after_a_bounded_time_with_its_own_exit():
    holder: list = []
    transport = Flaky(holder, refusals=10_000)
    app = App(transport=transport)
    holder.append(app)
    started = time.monotonic()
    with pytest.raises(SystemExit) as stopped:
        app.first_contact(patience_s=1.0)
    assert stopped.value.code == sdk_app.CORE_UNREACHABLE_EXIT
    assert time.monotonic() - started < PATIENCE_S
    assert len(transport.asked) >= 2  # it tried again before giving up


def test_any_other_refusal_is_not_waited_out():
    from jimmyhq_app import CallError  # noqa: PLC0415

    class Refuses:
        def request(self, *a, **k):
            raise CallError("invalid", "No.")

    with pytest.raises(CallError):
        App(transport=Refuses()).first_contact(patience_s=30)
