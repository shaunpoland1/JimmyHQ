"""Running a full app's program: started, stopped and restarted by JimmyHQ.

Two halves, in two processes, that share two files under ``<data>/apps/``:

- **The web process** writes ``run.json`` (``publish``) whenever what should run changes:
  an app installed, switched on or off, removed, or restarted by the owner. It lists
  every installed, switched-on full app with exactly what to start, and the owner's
  settings the supervisor needs. It reads ``status.json`` back to say how each is
  (``health``).
- **The supervisor** (``AppChildren``, called from ``supervisor.run`` once a second)
  reads ``run.json``, starts what is listed and stops what is not, and writes
  ``status.json``.

**Starting one.** The command is ``<python> -m jimmyhq_app <entry>``, run directly (never
through a shell), in the app's version folder. ``<python>`` is the app's own environment
(``venvs/<version>-<hash8>/bin/python``, ``Scripts\\python.exe`` on Windows) when it has
one, otherwise the interpreter JimmyHQ itself runs on. The environment is rebuilt, not
inherited: the system's basics (path, home, locale, temp, and on Windows the system
folders), then ``JIMMYHQ_SUPERVISED=1``, ``JIMMYHQ_APP_ID``, ``JIMMYHQ_APP_API`` (this
machine's address for the core) and ``JIMMYHQ_APP_DATA``. Nothing else of JimmyHQ's own
environment reaches it.

**The token.** A new app token is minted at each start, which revokes the one before,
and written as the first line of the child's standard input: ``{"token": "jhqa_..."}``.
The pipe then stays open, and the child stops itself when it closes, as every supervised
child does. The token is never in the environment or on the command line, so it is not
shown by a process listing and not inherited by anything the app starts. It is revoked
when the process stops.

**Holding back a crash loop.** A program that exits is started again after the
supervisor's usual backoff. One that exits ``apps.crash_limit`` times in a row, each
within a minute of starting, is held back: shown as "Held back after crashing" and not
started again until the owner restarts it or the app changes. The worker records that on
the Tape (``apps.held_back``) and, by default, puts a card on the Bench to say so
(``plugs.crashes``). ``apps.crash_loop`` can instead keep trying, ever more slowly, or have
the worker switch it off, or roll it back when the crashing began with an update
(``update.crash_rollback``). The supervisor itself only ever holds it back.

**Waiting for the core.** A program talks to the web process, which the supervisor starts
at the same moment, so a program is started (and restarted) only once that API answers
(``api_answers``). If it has not answered within ``READY_WAIT_S`` the supervisor says so
in its log, once, and starts the program anyway; the SDK is patient for a short while
too, and when it still finds no one it exits with ``CORE_UNREACHABLE_EXIT``. Such an exit
is a miss at start-up, never a crash: it is not counted towards holding the app back.

**Stopping one to change its version.** ``pause`` leaves an app out of ``run.json`` while
its version is changed (``update``), and ``wait_stopped`` waits until the supervisor says
its program has gone. ``resume`` lists it again. Removing an app pauses it the same way
and never resumes it unless the removal fails: ``forget_pause`` drops the pause without
listing it, so its program is not started again while its files and data are deleted.

**Output.** Everything the program prints goes to ``<logs>/app-<id>.log``, started afresh
(the old one kept as ``.1``) when it grows past 5 MB.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from jimmyhq.ids import now_ms

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.runtime")

RUN_FILE = "run.json"
STATUS_FILE = "status.json"
LOG_LIMIT = 5 * 1024 * 1024
STOP_GRACE_S = 10.0
KILL_AFTER_S = 3.0
STABLE_S = 60.0
STATUS_EVERY_S = 10.0
KEEP_RESTARTS = 50
SDK_MODULE = "jimmyhq_app"
READY_WAIT_S = 60.0  # how long a start waits for the core's API before going ahead
READY_PATH = "/api/health"
# What the SDK exits with when JimmyHQ never answered its first contact (EX_TEMPFAIL).
# The same number is in the SDK (jimmyhq_app.app.CORE_UNREACHABLE_EXIT).
CORE_UNREACHABLE_EXIT = 75

_APP_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}\Z")
_ENTRY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*\Z")
_VERSION_NAME = re.compile(r"^[0-9A-Za-z.+-]{1,80}-[0-9a-f]{8}\Z")
# What a child is given of the supervisor's own environment: enough for Python and the
# system to work, on each of macOS, Linux and Windows. Nothing JimmyHQ set for itself.
ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
            "LC_MESSAGES", "TZ", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "SYSTEMDRIVE",
            "WINDIR", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
            "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")

# --- the places ---------------------------------------------------------------------------


def apps_root(data: Path) -> Path:
    return Path(data) / "apps"


def run_file(data: Path) -> Path:
    return apps_root(data) / RUN_FILE


def status_file(data: Path) -> Path:
    return apps_root(data) / STATUS_FILE


def log_file(log_dir: Path, app_id: str) -> Path:
    return Path(log_dir) / f"app-{app_id}.log"


def venv_python(venv: Path) -> Path:
    """Where a virtual environment's interpreter is, on this system."""
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def sdk_dir() -> Path | None:
    """The folder holding the Python SDK, ``jimmyhq_app``, that ships with JimmyHQ."""
    from jimmyhq.apps import store  # noqa: PLC0415 - keeps the supervisor's imports light

    return store.bundled("_sdk/python", "sdk/python")


def api_url(host: str, port: int) -> str:
    """This machine's own address for the core, as an app's program should use it."""
    host = (host or "").strip()
    if host in ("", "0.0.0.0", "localhost", "127.0.0.1"):  # noqa: S104 - read, not bound
        return f"http://127.0.0.1:{port}"
    if host in ("::", "::1"):
        return f"http://[::1]:{port}"
    if ":" in host:
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"


def api_answers(api: str, *, timeout_s: float = 1.0) -> bool:
    """Whether the core's API at ``api`` answers an HTTP request now. Any answer below
    500 counts; a refused connection, a timeout or a server error does not. Asked
    directly, never through a proxy."""
    parts = urlsplit(api)
    if parts.scheme != "http" or not parts.hostname or parts.port is None:
        return False
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout_s)
    try:
        conn.request("GET", READY_PATH)
        return conn.getresponse().status < 500
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def counts_as_crash(code: int | None) -> bool:
    """Whether an exit counts towards holding a program back. One that found no core to
    talk to at start-up (``CORE_UNREACHABLE_EXIT``) is a miss, not a crash."""
    return code != CORE_UNREACHABLE_EXIT


def write_json(path: Path, value: Any) -> None:
    """Replace a small file in one step, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(temp, path)
            return
        except PermissionError:  # Windows: a reader has it open this instant
            if attempt == 9:
                with contextlib.suppress(OSError):
                    temp.unlink()
                raise
            time.sleep(0.05)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- the rules for what may be started ----------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """Exactly what to start for one app."""

    id: str
    version: str  # the version folder's name, <version>-<hash8>
    folder: Path  # <data>/apps/<id>/versions/<version>
    python: str
    entry: str  # "module:attribute"
    data: Path  # <data>/apps/<id>/data
    generation: int = 0  # raised by the owner's Restart

    def key(self) -> tuple[Any, ...]:
        return (self.version, str(self.folder), self.python, self.entry, self.generation)


class SpecRefused(ValueError):
    pass


def check_spec(data: Path, raw: Any, *, own_python: str = sys.executable) -> Spec:
    """One entry of ``run.json``, held to what JimmyHQ itself would have written: the
    program runs from inside the app's own version folder, with JimmyHQ's interpreter or
    one inside the app's own environments, and keeps its data in the app's own folder.
    Anything else is refused, whoever wrote the file."""
    if not isinstance(raw, dict):
        raise SpecRefused("an entry is not an object")
    app_id = raw.get("id")
    if not isinstance(app_id, str) or not _APP_ID.match(app_id):
        raise SpecRefused("an entry has no usable app id")
    home = (apps_root(data) / app_id).resolve()
    version = raw.get("version")
    if not isinstance(version, str) or not _VERSION_NAME.match(version):
        raise SpecRefused(f"{app_id}: the version folder's name is not one JimmyHQ makes")
    folder = home / "versions" / version
    if not folder.is_dir():
        raise SpecRefused(f"{app_id}: its version folder is not there")
    entry = raw.get("entry")
    if not isinstance(entry, str) or not _ENTRY.match(entry):
        raise SpecRefused(f"{app_id}: its entry is not module:attribute")
    python = raw.get("python")
    if not isinstance(python, str):
        raise SpecRefused(f"{app_id}: no interpreter is named")
    own = Path(own_python).resolve()
    chosen = Path(python)
    # A venv's interpreter is usually a link to the system's, so where it is named
    # matters here, not where it leads.
    venvs = Path(os.path.abspath(apps_root(data) / app_id / "venvs"))
    if chosen.resolve() != own and not (chosen.is_absolute() and _inside(chosen, venvs)
                                        and chosen.is_file()):
        raise SpecRefused(f"{app_id}: its interpreter is neither JimmyHQ's own nor in the "
                          "app's own environments")
    generation = raw.get("generation", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise SpecRefused(f"{app_id}: its generation is not a whole number")
    return Spec(app_id, version, folder, python, entry, home / "data", generation)


def _inside(path: Path, folder: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(folder)
    except ValueError:
        return False
    return ".." not in path.parts


def child_env(spec: Spec, api: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a program starts with: rebuilt, never inherited whole."""
    base = os.environ if base is None else base
    env = {name: base[name] for name in ENV_KEEP if name in base}
    paths = [str(spec.folder)]
    sdk = sdk_dir()
    if sdk is not None:
        paths.append(str(sdk))
    env |= {
        "JIMMYHQ_SUPERVISED": "1",
        "JIMMYHQ_APP_ID": spec.id,
        "JIMMYHQ_APP_API": api,
        "JIMMYHQ_APP_DATA": str(spec.data),
        "PYTHONPATH": os.pathsep.join(paths),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",  # the version folder stays exactly as consented to
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
    }
    return env


def command(spec: Spec) -> list[str]:
    """The program's command line: an interpreter and arguments, never a shell."""
    return [spec.python, "-m", SDK_MODULE, spec.entry]


def handover(token: str) -> bytes:
    """The first line on the program's standard input."""
    return (json.dumps({"token": token}) + "\n").encode("ascii")


CRASH_LOOP = ("hold_and_tell", "hold", "keep_trying", "switch_off", "roll_back")


def quick_exits_end_retries(failures: int, crash_limit: int, crash_loop: str = "hold") -> bool:
    """Whether a program that exited this many times in a row, each soon after starting,
    should be held back: left stopped until the owner restarts it. Unless the owner chose
    to keep trying (``apps.crash_loop``), when it is started again after ever longer
    waits. Never switched off and never rolled back here: that is the owner's to do."""
    return crash_loop != "keep_trying" and failures >= crash_limit


def backoff(failures: int) -> float:
    from jimmyhq import supervisor  # noqa: PLC0415 - the same backoff as every child

    return supervisor.backoff(failures)


# --- the supervisor's half ---------------------------------------------------------------------


@dataclass
class _Child:
    spec: Spec
    proc: Any = None
    started: float = 0.0  # monotonic
    started_at: int = 0  # unix ms, for the screen
    failures: int = 0
    restart_at: float = 0.0
    state: str = "waiting"  # waiting | running | stopping | crashing
    stopping_since: float = 0.0
    log: Any = field(default=None, repr=False)
    restarts: list[int] = field(default_factory=list)
    last_exit: dict[str, Any] | None = None
    retired: bool = False  # stopping for good: not in run.json any more, or replaced
    waiting_since: float | None = None  # when it first waited for the core's API
    told_unready: bool = False  # the log already said the API has not answered


class AppChildren:
    """Every full app's program, as the supervisor runs them. ``tick`` is called once a
    second and never waits on a child."""

    def __init__(self, data: Path, log_dir: Path, api: str, *,
                 popen: Callable[..., Any] = subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], int] = now_ms,
                 auth: Any = None, grace_s: float = STOP_GRACE_S,
                 stable_s: float = STABLE_S, wait: Callable[[int], float] = backoff,
                 ready: Callable[[], bool] | None = None, ready_wait_s: float = READY_WAIT_S):
        """``ready`` says whether the core's API answers; a program is started only once
        it does, or after ``ready_wait_s``. None starts at once (the supervisor passes
        ``api_answers``)."""
        self.data, self.log_dir, self.api = Path(data), Path(log_dir), api
        self._ready, self.ready_wait_s = ready, ready_wait_s
        self._ready_now: bool | None = None  # asked at most once a tick
        self._popen, self._clock, self._wall, self._wait = popen, clock, wall, wait
        self._auth = auth
        self.grace_s, self.stable_s = grace_s, stable_s
        self.children: dict[str, _Child] = {}
        self.leaving: list[_Child] = []  # stopping, with a successor or none
        self.crash_limit = 5
        self.crash_loop = "hold_and_tell"
        self._seen: tuple[int, int] | None = None
        self._specs: dict[str, Spec] = {}
        self._status_written = 0.0
        self._status_last: Any = None

    # the token store, opened on first use so a supervisor with no apps never opens it
    @property
    def auth(self) -> Any:
        if self._auth is None:
            from jimmyhq.auth import Auth  # noqa: PLC0415

            self._auth = Auth(Path(self.data) / "auth.db")
        return self._auth

    def read_run(self) -> dict[str, Spec]:
        """What ``run.json`` lists now. A file that cannot be read keeps what was listed
        before; an entry that is refused is left out, and said so in the log."""
        path = run_file(self.data)
        try:
            stat = path.stat()
            seen = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            seen = (0, 0)
        if seen == self._seen:
            return self._specs
        raw = read_json(path) if seen != (0, 0) else {"apps": []}
        if not isinstance(raw, dict) or not isinstance(raw.get("apps"), list):
            log.warning("%s could not be read; keeping the apps that were running", path)
            return self._specs
        self._seen = seen
        limit = raw.get("crash_limit", 5)
        if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1:
            self.crash_limit = limit
        loop = raw.get("crash_loop", "hold_and_tell")
        self.crash_loop = loop if loop in CRASH_LOOP else "hold_and_tell"
        specs: dict[str, Spec] = {}
        for entry in raw["apps"]:
            try:
                spec = check_spec(self.data, entry)
            except SpecRefused as exc:
                log.warning("not starting an app from run.json: %s", exc)
                continue
            specs[spec.id] = spec
        self._specs = specs
        return specs

    def tick(self) -> None:
        specs = self.read_run()
        now = self._clock()
        for app_id, child in list(self.children.items()):
            wanted = specs.get(app_id)
            if wanted is None or wanted.key() != child.spec.key():
                self._retire(child, now)
                del self.children[app_id]
                if wanted is not None:  # replaced: start the new one once the old has gone
                    self.children[app_id] = _Child(wanted, restarts=child.restarts)
        for app_id, spec in specs.items():
            self.children.setdefault(app_id, _Child(spec))
        self._ready_now = None
        for child in list(self.leaving):
            self._settle_leaving(child, now)
        for child in self.children.values():
            self._settle(child, now)
        self._write_status()

    def _settle(self, child: _Child, now: float) -> None:
        if child.state == "running" and child.proc.poll() is not None:
            self._exited(child, now)
        if child.state == "waiting" and now >= child.restart_at:
            if any(c.spec.id == child.spec.id for c in self.leaving):
                return  # the old program is still stopping
            if not self._core_ready(child, now):
                return
            self._start(child, now)

    def api_ready(self) -> bool:
        """Whether the core's API answers, asked at most once a tick."""
        if self._ready is None:
            return True
        if self._ready_now is None:
            try:
                self._ready_now = bool(self._ready())
            except Exception:  # noqa: BLE001 - a check that fails says "not yet"
                self._ready_now = False
        return self._ready_now

    def _core_ready(self, child: _Child, now: float) -> bool:
        """Whether to start ``child`` now: once the core's API answers, or once it has
        waited ``ready_wait_s`` for it (said in the log, once)."""
        if self.api_ready():
            child.waiting_since, child.told_unready = None, False
            return True
        if child.waiting_since is None:
            child.waiting_since = now
        if now - child.waiting_since < self.ready_wait_s:
            return False
        if not child.told_unready:
            child.told_unready = True
            log.warning("JimmyHQ's API at %s has not answered for %.0fs; starting app %s "
                        "anyway. If it cannot reach JimmyHQ it stops, and that is not "
                        "counted as a crash.", self.api, now - child.waiting_since,
                        child.spec.id)
        return True

    def _exited(self, child: _Child, now: float) -> None:
        code = child.proc.returncode
        ran_for = now - child.started
        child.last_exit = {"code": code, "at": self._wall(), "ran_s": round(ran_for, 1)}
        self._close(child)
        if not counts_as_crash(code):
            child.state, child.restart_at = "waiting", now + 1.0
            child.waiting_since, child.told_unready = None, False
            log.warning("app %s stopped because it could not reach JimmyHQ; starting it "
                        "again once JimmyHQ answers. This is not counted as a crash.",
                        child.spec.id)
            return
        child.failures = 0 if ran_for >= self.stable_s else child.failures + 1
        if quick_exits_end_retries(child.failures, self.crash_limit, self.crash_loop):
            child.state = "crashing"
            log.warning("app %s exited %s times in a row soon after starting; not starting "
                        "it again. See %s.", child.spec.id, child.failures,
                        log_file(self.log_dir, child.spec.id).name)
            return
        wait = self._wait(child.failures)
        child.state, child.restart_at = "waiting", now + wait
        log.warning("app %s exited with code %s after %.0fs; restarting in %.0fs.",
                    child.spec.id, code, ran_for, wait)

    def _start(self, child: _Child, now: float) -> None:
        spec = child.spec
        spec.data.mkdir(parents=True, exist_ok=True)
        token = self.auth.mint_app_token(spec.id)
        child.log = open_log(self.log_dir, spec.id)
        try:
            child.proc = self._popen(  # noqa: S603 - an interpreter and arguments, no shell
                command(spec), cwd=str(spec.folder), env=child_env(spec, self.api),
                stdin=subprocess.PIPE, stdout=child.log, stderr=subprocess.STDOUT,
                shell=False, close_fds=True)
        except OSError as exc:
            self.auth.revoke_app_tokens(spec.id)
            child.log.close()
            child.log = None
            child.failures += 1
            child.last_exit = {"code": None, "at": self._wall(), "ran_s": 0.0,
                               "error": type(exc).__name__}
            log.warning("app %s could not be started: %s", spec.id, exc)
            if quick_exits_end_retries(child.failures, self.crash_limit, self.crash_loop):
                child.state = "crashing"
            else:
                child.restart_at = now + self._wait(child.failures)
            return
        with contextlib.suppress(OSError, ValueError):  # it may have exited already
            child.proc.stdin.write(handover(token))
            child.proc.stdin.flush()
        child.state, child.started, child.started_at = "running", now, self._wall()
        child.restarts = [*child.restarts, child.started_at][-KEEP_RESTARTS:]
        log.info("started app %s (pid %s)", spec.id, child.proc.pid)

    def _retire(self, child: _Child, now: float) -> None:
        """Begin stopping a program for good: close its pipe, which asks it to stop."""
        child.retired = True
        if child.proc is None or child.state not in ("running",):
            self._close(child)
            return
        child.state, child.stopping_since = "stopping", now
        with contextlib.suppress(OSError, ValueError):
            child.proc.stdin.close()
        self.leaving.append(child)

    def _settle_leaving(self, child: _Child, now: float) -> None:
        if child.proc.poll() is not None:
            self.leaving.remove(child)
            self._close(child)
            return
        waited = now - child.stopping_since
        if waited >= self.grace_s + KILL_AFTER_S:
            with contextlib.suppress(OSError):
                child.proc.kill()
        elif waited >= self.grace_s:
            with contextlib.suppress(OSError):
                child.proc.terminate()

    def _close(self, child: _Child) -> None:
        """A program has stopped: its token stops too, and its log is let go."""
        if child.proc is not None:
            with contextlib.suppress(OSError, ValueError):
                child.proc.stdin.close()
        if child.log is not None:
            child.log.close()
            child.log = None
        if not any(c is not child and c.state == "running" and c.spec.id == child.spec.id
                   for c in self.children.values()):
            self.auth.revoke_app_tokens(child.spec.id)

    def stop_all(self, *, wait: bool = True) -> None:
        """Stop every program: close each pipe, then wait out the grace, then end any
        that is left. With ``wait=False`` only the pipes are closed."""
        now = self._clock()
        for child in list(self.children.values()):
            self._retire(child, now)
        self.children.clear()
        if not wait:
            return
        deadline = time.monotonic() + self.grace_s
        for child in list(self.leaving):
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.proc.wait(max(0.1, deadline - time.monotonic()))
            if child.proc.poll() is None:
                child.proc.terminate()
                try:
                    child.proc.wait(KILL_AFTER_S)
                except subprocess.TimeoutExpired:
                    child.proc.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        child.proc.wait(KILL_AFTER_S)
            self.leaving.remove(child)
            self._close(child)
        self._write_status(force=True)

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for child in self.leaving:  # still stopping, with no successor yet
            out[child.spec.id] = {
                "state": "stopping", "version": child.spec.version,
                "generation": child.spec.generation, "pid": child.proc.pid,
                "started_at": child.started_at or None, "failures": child.failures,
                "restarts": list(child.restarts), "last_exit": child.last_exit,
            }
        for app_id, child in self.children.items():
            if app_id in out and child.state != "running":
                continue  # the one stopping is what matters until it has gone
            out[app_id] = {
                "state": child.state, "version": child.spec.version,
                "generation": child.spec.generation,
                "pid": child.proc.pid if child.state == "running" else None,
                "started_at": child.started_at or None, "failures": child.failures,
                "restarts": list(child.restarts), "last_exit": child.last_exit,
            }
        return out

    def _write_status(self, *, force: bool = False) -> None:
        apps = self.status()
        now = time.monotonic()
        if not force and apps == self._status_last and now - self._status_written < \
                STATUS_EVERY_S:
            return
        try:
            write_json(status_file(self.data), {"written": self._wall(), "apps": apps})
        except OSError as exc:
            log.warning("could not write the apps' status: %s", exc)
            return
        self._status_last, self._status_written = apps, now


def open_log(log_dir: Path, app_id: str):
    """The program's output file, started afresh when it has grown too large."""
    path = log_file(log_dir, app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            os.replace(path, path.with_name(path.name + ".1"))
    return open(path, "ab")  # noqa: SIM115 - held open while the program runs


# --- the web process's half ----------------------------------------------------------------------

_publish_lock = threading.Lock()
_paused: set[str] = set()  # apps left out of run.json while their version is changed


def pause(core: Core, app_id: str) -> int:
    """Leave an app's program out of ``run.json``, so the supervisor stops it. Returns when
    that was asked, in unix ms, for ``wait_stopped``."""
    with _publish_lock:
        _paused.add(app_id)
    asked = now_ms()
    publish(core)
    return asked


def resume(core: Core, app_id: str) -> None:
    """List the app again, as what is installed now says."""
    with _publish_lock:
        _paused.discard(app_id)
    publish(core)


def forget_pause(app_id: str) -> None:
    """Rule ``a_removed_app_is_not_listed_again``: once an app is removed, drop its pause
    without writing ``run.json``. It is no longer installed, so the next ``publish``
    leaves it out anyway; listing it again in between would start its program while its
    files and data are being deleted."""
    with _publish_lock:
        _paused.discard(app_id)


def stopped(found: dict[str, Any], app_id: str, asked: int, *, now: int) -> bool:
    """Whether ``status.json`` shows the app's program gone since ``asked``: no supervisor
    has written it lately, or it was written after the ask without the program in it (or
    with it not running), or it is held back after crashing. One written before the ask
    says nothing more yet."""
    if not found:
        return True
    written = int(found.get("written") or 0)
    if now - written > (STATUS_EVERY_S * 3 + 5) * 1000:
        return True  # no supervisor is running, so neither is the program
    mine = (found.get("apps") or {}).get(app_id)
    if mine is not None and mine.get("state") == "crashing":
        return True  # held back: nothing runs, and nothing starts until it changes
    if written < asked:
        return False
    return mine is None or mine.get("state") not in ("running", "stopping")


def wait_stopped(core: Core, app_id: str, asked: int, *, timeout_s: float | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> bool:
    """Wait until the app's program has stopped. False if it had not in time."""
    limit = float(core.settings.get("apps.stop_wait_s") if timeout_s is None else timeout_s)
    deadline = time.monotonic() + limit
    while True:
        if stopped(statuses(core), app_id, asked, now=now_ms()):
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(0.2)


def python_for(core: Core, app_id: str, version: str) -> str:
    """The app's own environment's interpreter if it has one, otherwise JimmyHQ's."""
    from jimmyhq.apps import store  # noqa: PLC0415

    own = venv_python(store.app_dir(core, app_id) / "venvs" / version)
    return str(own) if own.is_file() else sys.executable


def wanted(core: Core) -> list[dict[str, Any]]:
    """Every installed, switched-on full app whose code is in place, as ``run.json``
    lists it (without its generation)."""
    from jimmyhq.apps import store  # noqa: PLC0415

    out = []
    for app_id, row in sorted(store.installed(core).items()):
        if row.get("tier") != "full" or not row.get("enabled") or app_id in _paused:
            continue
        try:
            manifest = store.manifest_of(core, app_id)
        except store.AppError:
            continue
        if manifest.server is None:
            continue
        version = store.version_name(row["version"], row["tree"])
        out.append({"id": app_id, "version": version, "entry": manifest.server.entry,
                    "python": python_for(core, app_id, version)})
    return out


def publish(core: Core, *, restart: str | None = None) -> dict[str, Any]:
    """Write ``run.json`` from what is installed now. ``restart`` names an app whose
    program should be stopped and started again (its generation goes up)."""
    path = run_file(core.config.data)
    with _publish_lock:
        before = read_json(path) or {}
        generations = {e.get("id"): e.get("generation", 0) for e in before.get("apps", [])
                       if isinstance(e, dict)}
        apps = []
        for entry in wanted(core):
            generation = generations.get(entry["id"], 0)
            if not isinstance(generation, int):
                generation = 0
            if entry["id"] == restart:
                generation += 1
            apps.append(entry | {"generation": generation})
        content = {"apps": apps, "crash_limit": core.settings.get("apps.crash_limit"),
                   "crash_loop": core.settings.get("apps.crash_loop")}
        if {k: before.get(k) for k in content} != content:
            write_json(path, content | {"written": now_ms()})
    return content


def statuses(core: Core) -> dict[str, Any]:
    """What the supervisor last wrote, or empty if it never has."""
    found = read_json(status_file(core.config.data))
    return found if isinstance(found, dict) else {}


LABELS = {"running": "Running", "starting": "Starting", "not_responding": "Not responding",
          "crashing": "Held back after crashing", "restarting": "Restarting", "stopped": "Stopped",
          "not_running": "Not running"}


def health(core: Core, app_id: str, *, now: int | None = None) -> dict[str, Any]:
    """How a full app's program is, for the Apps screen: ``{state, label, ...}``."""
    from jimmyhq.apps import inbox, store  # noqa: PLC0415

    now = now_ms() if now is None else now
    row = store.get(core, app_id)
    base: dict[str, Any] = {"app_id": app_id, "last_beat": inbox.last_beat(core, app_id)}
    if row is None or row.get("tier") != "full":
        return base | {"state": "not_running", "label": LABELS["not_running"]}
    if not row.get("enabled"):
        return base | {"state": "stopped", "label": LABELS["stopped"]}
    found = statuses(core)
    mine = (found.get("apps") or {}).get(app_id)
    stale = not found or now - int(found.get("written") or 0) > \
        (STATUS_EVERY_S * 3 + 5) * 1000
    if mine is None or stale:
        state = "not_running" if stale and found else "starting"
        return base | {"state": state, "label": LABELS[state]}
    out = base | {"pid": mine.get("pid"), "started_at": mine.get("started_at"),
                  "restarts": mine.get("restarts", []), "last_exit": mine.get("last_exit"),
                  "failures": mine.get("failures", 0)}
    state = {"crashing": "crashing", "waiting": "restarting",
             "stopping": "restarting"}.get(mine.get("state"), "running")
    if state == "running":
        limit = core.settings.get("apps.not_responding_s") * 1000
        beat = out["last_beat"]
        started = mine.get("started_at") or now
        if beat is not None and beat >= started - 1000 and now - beat <= limit:
            state = "running"
        elif now - started <= limit:
            state = "starting"
        else:
            state = "not_responding"
    return out | {"state": state, "label": LABELS[state]}


def log_tail(core: Core, app_id: str, lines: int = 50) -> list[str]:
    """The last lines a program printed, newest last."""
    path = log_file(core.config.log_dir, app_id)
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.replace("\r\n", "\n").splitlines()[-max(1, min(lines, 500)):]


def restart(core: Core, app_id: str) -> dict[str, Any]:
    """The owner's Restart: the program is stopped and started again, and a crash loop
    that ended its retries is given another go."""
    from jimmyhq.apps import store  # noqa: PLC0415
    from jimmyhq.apps.store import AppError  # noqa: PLC0415

    row = store.need(core, app_id)
    if row.get("tier") != "full":
        raise AppError(409, f"{row['name']} is a sandboxed app and has no program to "
                            "restart. Nothing was changed. Reload its screen instead.")
    if not row.get("enabled"):
        raise AppError(409, f"{row['name']} is switched off, so there is nothing to restart. "
                            "Nothing was changed. Switch it on instead.")
    from jimmyhq.apps import kinds  # noqa: PLC0415

    was = health(core, app_id)["state"]
    publish(core, restart=app_id)
    core.tape.print("apps.restarted", {"app_id": app_id, "was": was}, actor="owner",
                    subject=kinds.subject(app_id))
    return health(core, app_id)


def build_router(core: Core) -> APIRouter:
    from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: PLC0415

    from jimmyhq.apps import store  # noqa: PLC0415
    from jimmyhq.auth import Principal  # noqa: PLC0415
    from jimmyhq.web.deps import owner  # noqa: PLC0415

    router = APIRouter(prefix="/api/apps", tags=["apps"])

    @router.get("/{app_id}/health")
    def health_route(app_id: str, lines: int = Query(50, ge=0, le=500),  # noqa: B008
                     _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            store.need(core, app_id)
        except store.AppError as error:
            raise HTTPException(error.status, error.detail) from None
        from jimmyhq.apps.plugs import jobs  # noqa: PLC0415

        return health(core, app_id) | {"log": log_tail(core, app_id, lines) if lines else [],
                                       "jobs": jobs.status(core, app_id)}

    @router.post("/{app_id}/restart")
    def restart_route(app_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return restart(core, app_id)
        except store.AppError as error:
            raise HTTPException(error.status, error.detail) from None

    return router
