"""The supervisor: starts the web, worker and village processes and restarts any
one that exits. It is what the system service runs. It also runs each switched-on full
app's program, as ``apps.runtime.AppChildren`` describes.

Each child holds a pipe from the supervisor on its standard input. If the
supervisor goes away for any reason, including being killed outright, the pipe
closes and the child stops itself (see ``watch_parent``). That works the same
on macOS, Linux and Windows, where a terminated parent sends no signal.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("jimmyhq.supervisor")

CHILDREN = ("web", "worker", "village")
SUPERVISED_ENV = "JIMMYHQ_SUPERVISED"
_STABLE_S = 60
_MAX_BACKOFF_S = 30
_STOP_GRACE_S = 10
_ERR_LOG_LIMIT = 5 * 1024 * 1024


class AlreadyRunning(RuntimeError):
    pass


def backoff(failures: int) -> float:
    """Seconds to wait before restarting after this many quick failures in a row."""
    return 0.0 if failures <= 0 else float(min(_MAX_BACKOFF_S, 2 ** (failures - 1)))


def take_lock(data: Path):
    """Hold an exclusive lock for this data folder, or raise AlreadyRunning.

    Returns the open file; the lock lasts until it is closed or the process ends.
    """
    handle = open(data / "supervisor.lock", "a+b")  # noqa: SIM115 - held for the process lifetime
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        held = (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, errno.EDEADLK)
        if exc.errno not in held:
            # The file system cannot lock (some network drives). Carry on
            # without the guard rather than refuse to start.
            log.warning("could not lock %s (%s); starting without the single-copy guard",
                        handle.name, exc)
            return handle
        handle.close()
        raise AlreadyRunning(
            "JimmyHQ is already running for this data folder. Stop the other one first, "
            "or use `jimmyhq service` to manage the one that starts at login."
        ) from None
    return handle


def watch_parent(on_gone=None) -> None:
    """In a supervised child: stop when the supervisor's pipe closes."""
    if os.environ.get(SUPERVISED_ENV) != "1":
        return

    # Read the pipe itself, not ``sys.stdin``: a thread waiting inside ``sys.stdin`` holds
    # its buffer's lock, and a child that then exits by itself is aborted by the
    # interpreter at shutdown instead of leaving with its own exit code.
    try:
        pipe = sys.__stdin__.fileno()
    except (AttributeError, OSError, ValueError):
        pipe = 0

    def wait() -> None:
        with contextlib.suppress(OSError):
            while os.read(pipe, 4096):  # empty, or an error, only when the supervisor is gone
                pass
        if on_gone is not None:
            on_gone()
        else:
            with contextlib.suppress(Exception):
                signal.raise_signal(signal.SIGTERM)  # ask for a normal shutdown
        time.sleep(_STOP_GRACE_S)
        os._exit(0)  # the normal shutdown did not finish; do not linger as an orphan

    threading.Thread(target=wait, name="watch-parent", daemon=True).start()


def port_in_use(host: str, port: int) -> str | None:
    """The sentence to stop with when another program already listens on the web
    address, or None when it looks free. Asked once, before any child is started: a web
    process that cannot have its port would otherwise be restarted for ever. Anything
    else that is wrong with the address is left for the web process to report."""
    try:
        family, kind, proto, _, address = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)[0]
    except (OSError, UnicodeError):
        return None
    try:
        probe = socket.socket(family, kind, proto)
    except OSError:
        return None
    try:
        if sys.platform != "win32":
            # As the web server binds it: a port only just let go by an earlier run is
            # free. On Windows this option would let the bind take a port in use.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(address)
    except OSError as exc:
        # 10048 and 10013: Windows' own numbers for "in use" and "held by another program".
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) in (10048, 10013):
            return (f"Port {port} is already in use, so JimmyHQ did not start. Stop the other "
                    "program, or set JIMMYHQ_PORT to a port that is free.")
        return None
    finally:
        probe.close()
    return None


def restart_message(name: str, code: int | None, ran_for: float, wait: float,
                    log_dir: Path) -> str:
    """What the supervisor says when a child has exited. Both of the child's logs are
    named: what it logged is in one, and a crash's last words are in the other."""
    return (f"{name} exited with code {code} after {ran_for:.0f}s; restarting in {wait:.0f}s. "
            f"If this repeats, see {name}.log and {name}.err.log in {log_dir}.")


@dataclass
class _Child:
    name: str
    proc: subprocess.Popen | None = None
    started: float = 0.0
    failures: int = 0
    restart_at: float = 0.0
    log: object = field(default=None, repr=False)


def _err_log(log_dir: Path, name: str):
    """Opened afresh at each start of a child, so it is rotated at each restart."""
    path = log_dir / f"{name}.err.log"
    with contextlib.suppress(OSError):
        if path.exists() and path.stat().st_size > _ERR_LOG_LIMIT:
            os.replace(path, path.with_name(f"{name}.err.log.1"))
    return open(path, "ab")  # noqa: SIM115 - held open while the child runs


def _stop(children: list[_Child]) -> None:
    for child in children:  # closing the pipe asks each child to shut down normally
        if child.proc is not None and child.proc.stdin is not None:
            with contextlib.suppress(OSError):
                child.proc.stdin.close()
    deadline = time.monotonic() + _STOP_GRACE_S
    for child in children:
        if child.proc is None:
            continue
        try:
            child.proc.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.proc.terminate()
            try:
                child.proc.wait(3)
            except subprocess.TimeoutExpired:
                child.proc.kill()
        if child.log is not None:
            child.log.close()


def run(log_dir: Path, data: Path | None = None, api: str | None = None,
        ready_line: str | None = None) -> int:
    """Run the core's children, and with ``data`` and ``api`` every full app's program
    too (``apps.runtime``), until asked to stop. ``ready_line`` is printed once, when the
    web process first answers at ``api``."""
    stop = threading.Event()

    def ask_stop(*_):
        stop.set()

    signal.signal(signal.SIGINT, ask_stop)
    signal.signal(signal.SIGTERM, ask_stop)
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break and console close
        signal.signal(signal.SIGBREAK, ask_stop)

    env = {**os.environ, SUPERVISED_ENV: "1"}
    children = [_Child(name) for name in CHILDREN]
    apps = None
    if data is not None and api is not None:
        from jimmyhq.apps.runtime import AppChildren, api_answers  # noqa: PLC0415

        # An app's program talks to the web process started just below: each is started
        # only once that answers.
        apps = AppChildren(data, log_dir, api, ready=lambda: api_answers(api))
    said_ready = ready_line is None or api is None
    try:
        while not stop.is_set():
            if not said_ready:
                from jimmyhq.apps.runtime import api_answers  # noqa: PLC0415

                if api_answers(api):
                    said_ready = True
                    with contextlib.suppress(OSError, ValueError):  # no terminal to tell
                        print(ready_line, flush=True)
            if apps is not None:
                try:
                    apps.tick()
                except Exception:  # an app's trouble must never stop the core's children
                    log.exception("could not look after the apps' programs")
            now = time.monotonic()
            for child in children:
                if child.proc is not None and child.proc.poll() is not None:
                    ran_for = now - child.started
                    child.failures = 0 if ran_for >= _STABLE_S else child.failures + 1
                    wait = backoff(child.failures)
                    log.warning("%s", restart_message(
                        child.name, child.proc.returncode, ran_for, wait, log_dir))
                    if child.log is not None:
                        child.log.close()
                        child.log = None
                    child.proc = None
                    child.restart_at = now + wait
                if child.proc is None and now >= child.restart_at:
                    child.log = _err_log(log_dir, child.name)
                    child.proc = subprocess.Popen(  # noqa: S603 - our own interpreter and module
                        [sys.executable, "-m", "jimmyhq", child.name],
                        stdin=subprocess.PIPE, stdout=child.log, stderr=child.log, env=env,
                    )
                    child.started = now
                    log.info("started %s (pid %s)", child.name, child.proc.pid)
            stop.wait(1)
    finally:
        if apps is not None:
            apps.stop_all(wait=False)  # their pipes close while the core's children stop
        _stop(children)
        if apps is not None:
            apps.stop_all()
    return 0
