"""An MCP server that is a program on this machine, spoken to over its standard streams.

One JSON-RPC message per line: requests go to the program's standard input, answers come
back on its standard output. What it writes to standard error is thrown away unread,
because it may hold a saved value and is text nobody here should act on.

The program is one the owner chose to run. It runs as the owner, so this file is careful
about how, not whether:

- It is started from a list of arguments, never through a shell, so nothing in the
  record is read as shell syntax. The one exception is forced by Windows: a ``.cmd`` or
  ``.bat`` file (how ``npx`` and many servers are installed there) can only be run by
  ``cmd.exe``. Then every argument is quoted, and one that cannot be quoted safely is
  refused (``launch``).
- It is given a short list of ordinary environment variables and the ones the owner
  saved for it. JimmyHQ's own environment is not passed on.
- It is started when first needed, stopped after ``idle_s`` without use and when this
  process ends, and there is one of it per provider.
- A start that fails waits longer each time before the next.
- Everything is bounded: a line longer than ``max_message_bytes`` ends the connection.

Reading and writing happen on plain threads, so the same program can serve callers on
any event loop, and nothing here needs a POSIX-only call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future, InvalidStateError
from pathlib import Path
from typing import Any

from jimmyhq.hands.mcp import (
    Config,
    Exited,
    Garbled,
    Lost,
    Reply,
    TimedOut,
    TooLarge,
    Unavailable,
)

log = logging.getLogger("jimmyhq.hands.mcp")

# What a program needs to run at all, on any system. Nothing else is inherited.
INHERITED = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "TEMP", "TMP", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "APPDATA",
    "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
)
BACKOFF_FIRST_S = 1.0
BACKOFF_MAX_S = 60.0
STOP_GRACE_S = 2.0
MAX_JUNK_LINES = 50  # lines on standard output that are not the protocol, before giving up
REFUSAL = {"code": -32601, "message": "This client only lists and calls tools."}
BATCH = (".cmd", ".bat")
# What cmd.exe may read as syntax even inside double quotes, or that would end the
# quotes: a quote, a variable (% and !), an escape (^), and the characters that join or
# redirect commands, which a batch file that unquotes its arguments would set loose.
CMD_UNSAFE = frozenset('"%!^&|<>')


def environment(saved: dict[str, str]) -> dict[str, str]:
    """Rule ``mcp_environment_is_minimal``: what the program is given. The few variables
    any program needs, then the owner's saved ones. Nothing else of this process."""
    wanted = {name.upper() for name in INHERITED}
    base = {k: v for k, v in os.environ.items() if k.upper() in wanted}
    return base | saved


def is_batch(path: str) -> bool:
    return path.lower().endswith(BATCH)


def cmd_quoted(part: str) -> str | None:
    """One argument as cmd.exe and the program behind it will both read it whole, or
    None if no quoting makes it safe. It is put in double quotes, where spaces and
    brackets are plain text. Backslashes at its end are doubled, so the closing quote is
    not taken for an escaped one. Anything in ``CMD_UNSAFE``, and any control character,
    cannot be made safe."""
    if any(ch in CMD_UNSAFE or ord(ch) < 32 or ord(ch) == 127 for ch in part):
        return None
    trailing = len(part) - len(part.rstrip("\\"))
    return f'"{part}{chr(92) * trailing}"'


def launch(path: str, args: tuple[str, ...] | list[str], *, windows: bool,
           shell: str = "cmd.exe") -> list[str] | str:
    """Rule ``mcp_started_without_a_shell``: what to hand the system to start a program.

    Everywhere, a program and a list of arguments: nothing in them is read as shell
    syntax. On Windows a ``.cmd`` or ``.bat`` file is not a program and only ``cmd.exe``
    can run it, so it is given as one command line, ``cmd.exe /d /s /c "..."``, with the
    file and every argument quoted by ``cmd_quoted``. Raises ``Unavailable`` when one of
    them cannot be quoted safely; nothing is started then."""
    if not (windows and is_batch(path)):
        return [path, *args]
    quoted = [cmd_quoted(part) for part in (path, *args)]
    if quoted[0] is None:
        raise Unavailable(
            "This program is a batch file, and its path has a character the Windows "
            "command shell would not read as plain text (one of \" % ! ^ & | < >). Move "
            "it to a folder without one, or give the program it starts instead.")
    bad = next((i for i, part in enumerate(quoted[1:], start=1) if part is None), None)
    if bad is not None:
        raise Unavailable(
            f"Argument {bad} has a character the Windows command shell would not read as "
            "plain text (one of \" % ! ^ & | < >, or a line break), and this program is a "
            "batch file, which only that shell can run. Remove the character, or give "
            "the program the batch file starts (for example node and the path of its "
            "script) so no shell is needed.")
    # /d: no AutoRun commands. /s with the outer quotes: the line inside is taken as it
    # is, so the quotes around each part survive. /c: run it and end.
    return f'"{shell}" /d /s /c "{" ".join(quoted)}"'  # type: ignore[arg-type]


def command_shell(env: dict[str, str]) -> str:
    """Windows' own command shell, from the system folder rather than whatever is first
    on the path."""
    root = env.get("SYSTEMROOT") or env.get("SystemRoot") or os.environ.get("SYSTEMROOT", "")
    found = Path(root) / "System32" / "cmd.exe" if root else None
    return str(found) if found and found.is_file() else (env.get("COMSPEC") or "cmd.exe")


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """End a program for good. On Windows a batch file's program is a grandchild that
    ending ``cmd.exe`` alone would leave running, so the whole tree under it is ended
    with the system's ``taskkill``; elsewhere, and if that fails, the program itself."""
    if os.name == "nt":
        root = os.environ.get("SYSTEMROOT", "")
        tool = str(Path(root) / "System32" / "taskkill.exe") if root else "taskkill"
        try:
            subprocess.run(  # noqa: S603 - a fixed system tool and this child's own id
                [tool, "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=STOP_GRACE_S,
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    proc.kill()


def backoff(failures: int) -> float:
    """Rule ``mcp_restart_backs_off``: seconds to wait before starting again after this
    many failures in a row: 1, 2, 4 and so on, a minute at most."""
    if failures <= 0:
        return 0.0
    return min(BACKOFF_MAX_S, BACKOFF_FIRST_S * 2 ** min(failures - 1, 16))


class StdioServer:
    """One program, as long as this process has use for it."""

    kind = "stdio"

    def __init__(self, config: Config):
        self.command, self.args, self.cwd = config.command, config.args, config.cwd
        self.limits = config.limits
        self._env = dict(config.env)
        self.generation = 0  # goes up with every start
        self.refused = 0  # requests from the server that were turned down
        self.http: Any = None
        self._lock = threading.RLock()
        self._writing = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._pending: dict[Any, Future[dict[str, Any]]] = {}
        self._inflight = 0
        self._last_used = 0.0
        self._timer: threading.Timer | None = None
        self._failures = 0
        self._retry_at = 0.0

    # --- starting and stopping --------------------------------------------------------

    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    async def open(self) -> None:
        """Start the program if it is not running. Raises ``Unavailable`` with what to
        do; nothing has been sent to it then."""
        if not self.running():
            await asyncio.to_thread(self._start)

    def _start(self) -> None:
        with self._lock:
            if self.running():
                return
            self._drop(Exited("The program had stopped."))
            wait = self._retry_at - time.monotonic()
            if wait > 0:
                times = "once" if self._failures == 1 else f"{self._failures} times in a row"
                raise Unavailable(
                    f"The program stopped or failed to start {times}, so it will not be "
                    f"started again for another {math.ceil(wait)} seconds. Check its "
                    "command and its saved values, then try again.")
            env = environment(self._env)
            if self.cwd is not None and not Path(self.cwd).is_dir():
                self._failed()
                raise Unavailable(f"The folder \"{self.cwd}\" does not exist. Change the "
                                  "folder in this server's options.")
            path = shutil.which(self.command, path=env.get("PATH"))
            if path is None:
                self._failed()
                raise Unavailable(
                    f"The program \"{self.command}\" was not found. Install it, or give "
                    "its full path. The command is the program alone: what follows it "
                    "goes in the list of arguments.")
            windows = os.name == "nt"
            try:
                line = launch(path, self.args, windows=windows,
                              shell=command_shell(env) if windows else "")
            except Unavailable:
                self._failed()
                raise
            try:
                proc = subprocess.Popen(  # noqa: S603 - an argument list, never shell=True
                    line, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, cwd=self.cwd, env=env, shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, ValueError) as exc:
                self._failed()
                raise Unavailable(
                    f"The program \"{self.command}\" could not be started "
                    f"({type(exc).__name__}). Check that it can be run from a terminal "
                    "with the same arguments.") from None
            self._proc = proc
            self.generation += 1
            self._last_used = time.monotonic()
            threading.Thread(target=self._read, args=(proc,), daemon=True,
                             name="mcp-stdio-reader").start()
            self._arm(self.limits.idle_s)

    def _failed(self) -> None:
        self._failures += 1
        self._retry_at = time.monotonic() + backoff(self._failures)

    def settled(self, ok: bool) -> None:
        """The opening exchange worked, or it did not: then the program is stopped and
        the next start waits."""
        if ok:
            self._failures, self._retry_at = 0, 0.0
            return
        with self._lock:
            if self._proc is not None:  # else it went by itself and was counted then
                self._failed()
        self._kill()

    def _drop(self, why: Lost) -> subprocess.Popen[bytes] | None:
        """Let go of the program and fail whatever was waiting on it."""
        with self._lock:
            proc, self._proc = self._proc, None
            waiting, self._pending = list(self._pending.values()), {}
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        for future in waiting:
            _settle(future, error=why)
        return proc

    def _kill(self) -> None:
        proc = self._drop(Exited("The program was stopped."))
        if proc is not None:
            self._end(proc, grace=0.0)

    def drop(self) -> None:
        """End the program at once, with no blame: a caller gave up half way through
        starting it, and the next one should find nothing half started."""
        self._kill()

    def close(self) -> None:
        """Stop the program: close its input, wait a moment, then end it."""
        proc = self._drop(Exited("The program was stopped."))
        if proc is not None:
            self._end(proc, grace=STOP_GRACE_S)

    def _end(self, proc: subprocess.Popen[bytes], *, grace: float) -> int | None:
        """Stop a program the way the protocol asks: close its input and wait; if it
        stays, end it. ``terminate`` is the system's own on every platform; the last
        step is ``kill_tree``, which on Windows also ends what a batch file started.

        Its input is only ever closed while holding the writing lock, so a writer that a
        stuck program is holding up can never be closed under: the program is ended
        first, which lets that writer go."""
        def shut(wait: float) -> bool:
            if not self._writing.acquire(timeout=wait):
                return False
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass
            finally:
                self._writing.release()
            return True

        def kill() -> None:
            kill_tree(proc)

        code, closed = proc.poll(), False
        if code is None and grace > 0:
            closed = shut(grace)
            code = _wait(proc, grace) if closed else None
        # With no grace it is ended outright: it failed, or sent what it should not have.
        for step in ((proc.terminate, kill) if grace > 0 else (kill,)):
            if code is not None:
                break
            try:
                step()
            except OSError:
                pass
            code = _wait(proc, STOP_GRACE_S)
        if not closed:
            shut(STOP_GRACE_S)
        return code

    def _arm(self, after: float) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(max(after, 0.05), self._idle, args=(self.generation,))
            self._timer.daemon = True
            self._timer.start()

    def _idle(self, generation: int) -> None:
        """Rule ``mcp_stopped_when_idle``: a program nobody has used for ``idle_s`` is
        stopped. It starts again when next needed."""
        with self._lock:
            if generation != self.generation or self._proc is None:
                return
            quiet = time.monotonic() - self._last_used
            if self._inflight or quiet < self.limits.idle_s:
                self._arm(self.limits.idle_s - (0 if self._inflight else quiet))
                return
        self.close()

    # --- reading ----------------------------------------------------------------------

    def _read(self, proc: subprocess.Popen[bytes]) -> None:
        """Rule ``mcp_message_size_is_bounded``: read answers, a line at a time, until
        the program closes its output or sends a line longer than the limit."""
        limit, junk = self.limits.max_message_bytes, 0
        why: Lost = Exited("The program stopped before it answered.")
        try:
            while True:
                line = proc.stdout.readline(limit + 1)  # type: ignore[union-attr]
                if not line:
                    break
                if len(line) > limit:
                    why = TooLarge(f"The program sent a message larger than the limit of "
                                   f"{limit} bytes, so it was stopped.")
                    break
                try:
                    message = json.loads(line)
                except (ValueError, RecursionError):
                    junk += 1  # something printed that is not the protocol
                    if junk > MAX_JUNK_LINES:
                        why = Garbled("The program keeps writing things that are not the "
                                      "protocol, so it was stopped.")
                        break
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        self._refuse(proc, message)
                    continue  # a notification: progress or a log line, not needed here
                future = self._claim(message.get("id"))
                if future is not None:
                    _settle(future, result=message)
        except (OSError, ValueError):
            pass  # the pipe was closed under the reader: the program is going away
        finally:
            self._ended(proc, why)

    def _claim(self, rid: Any) -> Future[dict[str, Any]] | None:
        if isinstance(rid, bool) or not isinstance(rid, int | str):
            return None
        with self._lock:
            return self._pending.pop(rid, None)

    def _ended(self, proc: subprocess.Popen[bytes], why: Lost) -> None:
        # It closed its output, so it is going. Give it a moment to say how it ended.
        code = _wait(proc, STOP_GRACE_S) if isinstance(why, Exited) else None
        ended = self._end(proc, grace=0.0)
        code = ended if code is None else code
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass
        with self._lock:
            if self._proc is not proc:
                return  # it was stopped on purpose, and whoever did that said why
            if isinstance(why, Exited):
                why = Exited(f"The program stopped (exit code {code}) before it answered. "
                             "Run the same command in a terminal to see why.")
            self._failed()  # it went by itself: the next start waits a little
        self._drop(why)

    def _refuse(self, proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
        """Rule ``mcp_server_requests_refused``: a server that asks this client for
        something (a model's answer, the owner's input, a list of folders) is told no,
        at once, so its tool is not left waiting. Only ``ping`` is answered. A newer
        server may not ask at all; one that does anyway is told no the same way."""
        self.refused += 1
        answer: dict[str, Any] = {"jsonrpc": "2.0", "id": message.get("id")}
        if message.get("method") == "ping":
            answer["result"] = {}
        else:
            answer["error"] = REFUSAL
        try:
            self._write(proc, _line(answer))
        except (OSError, ValueError):
            pass

    # --- writing ----------------------------------------------------------------------

    def _write(self, proc: subprocess.Popen[bytes], data: bytes) -> None:
        with self._writing:
            proc.stdin.write(data)  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]

    async def _send(self, proc: subprocess.Popen[bytes], data: bytes, timeout: float) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(self._write, proc, data), timeout)
        except TimeoutError:
            self._kill()  # it is not reading: nothing more can be said to it
            raise TimedOut("The program did not take the request in the time allowed, so "
                           "it was stopped.") from None
        except (OSError, ValueError):
            raise Exited("The program stopped while the request was being sent.") from None

    async def exchange(self, message: dict[str, Any], *, timeout: float,
                       headers: dict[str, str] | None = None) -> Reply:
        """Send one request and wait for its answer. Raises ``Lost`` (stopped, timed
        out, too large) when the request left and no answer came."""
        data = _line(message)
        if len(data) > self.limits.max_message_bytes:
            raise Unavailable("The request is larger than this server's limit of "
                              f"{self.limits.max_message_bytes} bytes.")
        rid, future = message["id"], Future()
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                raise Unavailable("The program stopped before the request was sent.")
            self._pending[rid] = future
            self._inflight += 1
        deadline = time.monotonic() + timeout
        try:
            await self._send(proc, data, timeout)
            try:
                answer = await asyncio.wait_for(asyncio.wrap_future(future),
                                                max(deadline - time.monotonic(), 0.01))
            except TimeoutError:
                self._cancel(proc, rid)
                raise TimedOut("The program did not answer in the time allowed.") from None
            except asyncio.CancelledError:
                self._cancel(proc, rid)
                raise
            return Reply(answer)
        finally:
            with self._lock:
                self._pending.pop(rid, None)
                self._inflight -= 1
                self._last_used = time.monotonic()

    async def notify(self, message: dict[str, Any], *, timeout: float,
                     headers: dict[str, str] | None = None) -> None:
        with self._lock:
            proc = self._proc
        if proc is None:
            raise Unavailable("The program stopped before the request was sent.")
        await self._send(proc, _line(message), timeout)

    def _cancel(self, proc: subprocess.Popen[bytes], rid: Any) -> None:
        """Tell the program a request is no longer waited for. Best effort, off the
        caller's path: a program that is stuck cannot hold the caller up."""
        note = _line({"jsonrpc": "2.0", "method": "notifications/cancelled",
                      "params": {"requestId": rid, "reason": "no longer waited for"}})

        def tell() -> None:
            try:
                self._write(proc, note)
            except (OSError, ValueError):
                pass

        threading.Thread(target=tell, daemon=True, name="mcp-stdio-cancel").start()


def _line(message: dict[str, Any]) -> bytes:
    # ASCII only and no raw line break: one message is exactly one line.
    return json.dumps(message, separators=(",", ":")).encode("ascii") + b"\n"


def _settle(future: Future[dict[str, Any]], *, result: dict[str, Any] | None = None,
            error: BaseException | None = None) -> None:
    try:
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result or {})
    except InvalidStateError:
        pass  # the caller stopped waiting


def _wait(proc: subprocess.Popen[bytes], seconds: float) -> int | None:
    try:
        return proc.wait(timeout=seconds)
    except (subprocess.TimeoutExpired, OSError):
        return proc.poll()


# Every mechanical rule of this file: (name, the option that holds its number, function).
RULES = [
    ("mcp_environment_is_minimal", None, environment),
    ("mcp_started_without_a_shell", None, launch),
    ("mcp_restart_backs_off", None, backoff),
    ("mcp_stopped_when_idle", "idle_s", StdioServer._idle),
    ("mcp_message_size_is_bounded", "max_message_bytes", StdioServer._read),
    ("mcp_server_requests_refused", None, StdioServer._refuse),
]
