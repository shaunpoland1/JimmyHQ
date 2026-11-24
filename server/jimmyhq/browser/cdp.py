"""Talking to the browser: the DevTools protocol over a pipe.

The browser is started with ``--remote-debugging-pipe``. It reads commands from one
inherited pipe and writes answers and events to another, each a JSON object followed by
a zero byte. No port is opened, so nothing else on the machine can attach.

On macOS and Linux the browser expects the two pipes as descriptors 3 and 4; a one-line
shell puts them there and then becomes the browser. On Windows it is told the two
handles by number (``--remote-debugging-io-pipes``) and inherits exactly those.

A thread reads the pipe and hands each message to the event loop; everything else here
runs on the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("jimmyhq.browser.cdp")

Handler = Callable[[str, dict[str, Any], str | None], None]
# Puts the two pipe ends on descriptors 3 and 4, where the browser looks for them, then
# becomes the browser. Python and not a shell: some systems' /bin/sh (dash) cannot
# redirect from a descriptor above 9.
_STEP = ("import os,sys;os.dup2(int(sys.argv[1]),3);os.dup2(int(sys.argv[2]),4);"
         "os.execv(sys.argv[3],sys.argv[3:])")
_HIGH = 10  # the ends are lifted clear of 3 and 4 first, so neither copy clobbers the other


class CDPError(Exception):
    """The browser refused a command, did not answer in time, or is gone."""


def _lifted(fd: int) -> int:
    """The same pipe end on a descriptor that is not one the browser's own are put on."""
    spare = []
    while fd < _HIGH:
        spare.append(fd)
        fd = os.dup(fd)
    for old in spare:
        os.close(old)
    return fd


def spawn(program: Path, args: list[str], stderr: Any = subprocess.DEVNULL
          ) -> tuple[subprocess.Popen, int, int]:
    """Start the browser with its two pipes. Returns the process, the descriptor to
    write commands to and the one to read answers from."""
    to_browser_r, to_browser_w = os.pipe()
    from_browser_r, from_browser_w = os.pipe()
    try:
        if sys.platform == "win32":
            import msvcrt

            handles = [msvcrt.get_osfhandle(to_browser_r), msvcrt.get_osfhandle(from_browser_w)]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            info = subprocess.STARTUPINFO(lpAttributeList={"handle_list": handles})
            proc = subprocess.Popen(  # noqa: S603 - the owner's own browser, fixed arguments
                [str(program), *args,
                 f"--remote-debugging-io-pipes={handles[0]},{handles[1]}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                startupinfo=info, close_fds=True)
        else:
            to_browser_r, from_browser_w = _lifted(to_browser_r), _lifted(from_browser_w)
            proc = subprocess.Popen(  # noqa: S603 - the owner's own browser, fixed arguments
                [sys.executable, "-I", "-c", _STEP, str(to_browser_r), str(from_browser_w),
                 str(program), *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                pass_fds=(to_browser_r, from_browser_w), start_new_session=True)
    except BaseException:
        for fd in (to_browser_r, to_browser_w, from_browser_r, from_browser_w):
            with contextlib.suppress(OSError):
                os.close(fd)
        raise
    os.close(to_browser_r)
    os.close(from_browser_w)
    return proc, to_browser_w, from_browser_r


class CDP:
    """One browser process and the conversation with it."""

    def __init__(self, proc: subprocess.Popen, write_fd: int, read_fd: int):
        self.proc = proc
        self._write_fd, self._read_fd = write_fd, read_fd
        self._loop = asyncio.get_running_loop()
        self._next = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._handlers: list[Handler] = []
        self._write_lock = threading.Lock()
        self.closed = False
        self._thread = threading.Thread(target=self._read, name="jimmyhq-browser-pipe",
                                        daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ reading
    def _read(self) -> None:
        buffer = b""
        try:
            while True:
                data = os.read(self._read_fd, 1 << 16)
                if not data:
                    break
                buffer += data
                *whole, buffer = buffer.split(b"\0")
                for raw in whole:
                    if raw:
                        self._hand(self._got, raw)
        except OSError:
            pass
        self._hand(self._gone)

    def _hand(self, fn: Callable[..., None], *args: Any) -> None:
        with contextlib.suppress(RuntimeError):  # the loop has ended: nobody is listening
            self._loop.call_soon_threadsafe(fn, *args)

    def _got(self, raw: bytes) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            return
        waiting = self._waiting.pop(message.get("id", -1), None)
        if waiting is not None:
            if not waiting.done():
                if "error" in message:
                    waiting.set_exception(CDPError(str(message["error"].get("message"))[:300]))
                else:
                    waiting.set_result(message.get("result") or {})
            return
        method = message.get("method")
        if method:
            for handler in list(self._handlers):
                try:
                    handler(method, message.get("params") or {}, message.get("sessionId"))
                except Exception:  # noqa: BLE001 - one handler's fault must not stop the pipe
                    log.exception("a browser event handler failed on %s", method)

    def _gone(self) -> None:
        self.closed = True
        for waiting in self._waiting.values():
            if not waiting.done():
                waiting.set_exception(CDPError("the browser has stopped"))
        self._waiting.clear()

    # ------------------------------------------------------------------ sending
    def on(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def post(self, method: str, params: dict[str, Any] | None = None,
             session: str | None = None) -> asyncio.Future:
        """Send a command without waiting; the future holds the answer."""
        future: asyncio.Future = self._loop.create_future()
        if self.closed:
            future.set_exception(CDPError("the browser has stopped"))
            return future
        self._next += 1
        message: dict[str, Any] = {"id": self._next, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self._waiting[self._next] = future
        data = json.dumps(message).encode("utf-8") + b"\0"
        try:
            with self._write_lock:
                view = memoryview(data)
                while view:
                    view = view[os.write(self._write_fd, view):]
        except OSError:
            self._waiting.pop(self._next, None)
            future.set_exception(CDPError("the browser has stopped"))
        return future

    def fire(self, method: str, params: dict[str, Any] | None = None,
             session: str | None = None) -> None:
        """Send a command whose answer nobody waits for."""
        self.post(method, params, session).add_done_callback(
            lambda f: f.cancelled() or f.exception())

    async def send(self, method: str, params: dict[str, Any] | None = None,
                   session: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
        future = self.post(method, params, session)
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise CDPError(f"the browser did not answer {method} in time") from None

    # ------------------------------------------------------------------ stopping
    def kill(self) -> None:
        """Stop the browser now. Safe from any thread, and more than once."""
        self.closed = True
        for fd in (self._write_fd,):
            with contextlib.suppress(OSError):
                os.close(fd)
        self._write_fd = -1
        if self.proc.poll() is None:
            with contextlib.suppress(OSError):
                self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    self.proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.proc.wait(3)
        self._thread.join(2)
        if not self._thread.is_alive():  # never close a descriptor a thread is reading
            with contextlib.suppress(OSError):
                os.close(self._read_fd)

    async def close(self) -> None:
        if not self.closed and self.proc.poll() is None:
            with contextlib.suppress(CDPError):
                await self.send("Browser.close", timeout=3)
            for _ in range(30):
                if self.proc.poll() is not None:
                    break
                await asyncio.sleep(0.1)
        await asyncio.get_running_loop().run_in_executor(None, self.kill)
