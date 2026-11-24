"""The door's own listener: a port that answers ``/mcp`` and nothing else.

The app served here is not the JimmyHQ app with routes taken away. It is a few lines
that know one path; every other path is a 404 because there is no code here that could
answer it. So a tunnel pointed at this port exposes the door, and cannot expose the
app, the API or the owner's sign-in, whatever path is asked for.

``Listener`` runs it inside the web process, in a thread of its own, and follows the
settings: switched on, it starts; switched off, or moved to another port, it stops and
starts again, with no restart of JimmyHQ. It listens on this computer only unless the
owner changes ``door.host``. Nothing here opens it to the internet.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from starlette.requests import Request

from jimmyhq.door import protocol
from jimmyhq.door.service import Door

log = logging.getLogger("jimmyhq.door")

POLL_S = 2.0
STOP_WAIT_S = 6.0


class DoorApp:
    """Rule ``door_listener_answers_only_mcp``: an ASGI app with one address."""

    def __init__(self, door: Door):
        self.door = door

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        if scope.get("path") != protocol.PATH:
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-length", b"0"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b""})
            return
        response = await protocol.handle(self.door, Request(scope, receive))
        await response(scope, receive, send)


def wanted(door: Door) -> tuple[str, int] | None:
    """Rule ``door_off_by_default``: where the listener should be right now, or None
    when it should not run: the door is off, or it is mounted on the main address."""
    if not door.enabled() or door.on_main_port():
        return None
    return str(door.S("host")), int(door.S("port"))


class Listener:
    """Keeps the door's own port in step with the settings."""

    def __init__(self, door: Door, proxies: tuple[str, ...] = ()):
        self.door = door
        self.proxies = proxies
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._serving: threading.Thread | None = None
        self._at: tuple[str, int] | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._watch, name="door-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_WAIT_S + POLL_S)

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync()
            except Exception:  # noqa: BLE001 - the web process outlives a bad setting
                log.exception("the door's listener could not be brought in step")
            self._stop.wait(POLL_S)
        self._down()

    def sync(self) -> None:
        want = wanted(self.door)
        if self._serving is not None and not self._serving.is_alive():
            # It stopped by itself: most often the port is taken by something else.
            if self._at is not None and self.door.listening is not None:
                self.door.listen_problem = (
                    f"The door stopped listening on port {self._at[1]}. Another program "
                    "may be using that port: choose another under Settings, Other AI apps.")
            self._serving, self._server, self.door.listening = None, None, None
            if want == self._at:
                return  # do not spin on a port that cannot be had; a new setting retries
        if want == self._at and self._serving is not None:
            return
        if want != self._at:
            self._down()
        if want is not None and self._serving is None:
            self._up(want)

    def _up(self, at: tuple[str, int]) -> None:
        import uvicorn

        config = uvicorn.Config(
            DoorApp(self.door), host=at[0], port=at[1], log_config=None, access_log=False,
            timeout_graceful_shutdown=3, server_header=False,
            proxy_headers=bool(self.proxies), forwarded_allow_ips=list(self.proxies) or None)
        server = uvicorn.Server(config)
        self._server, self._at = server, at
        self.door.listen_problem = None

        def serve() -> None:
            try:
                asyncio.run(server.serve())
            except (SystemExit, Exception) as exc:  # noqa: BLE001 - a taken port ends here
                self.door.listen_problem = (
                    f"The door could not listen on {at[0]} port {at[1]} "
                    f"({type(exc).__name__}). Another program may be using that port: "
                    "choose another under Settings, Other AI apps.")
                log.error("the door could not listen on port %d: %s", at[1],
                          type(exc).__name__)
            finally:
                self.door.listening = None

        self.door.listening = f"{at[0]}:{at[1]}"
        self._serving = threading.Thread(target=serve, name="door-serve", daemon=True)
        self._serving.start()
        log.info("the MCP door is listening on %s port %d", at[0], at[1])

    def _down(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._serving is not None:
            with contextlib.suppress(Exception):
                self._serving.join(STOP_WAIT_S)
            log.info("the MCP door stopped listening")
        self._server, self._serving, self._at = None, None, None
        self.door.listening = None
