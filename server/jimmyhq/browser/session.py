"""One agent's browser while it is running, and the keeper of all of them.

A browser is started the first time its agent opens a page and stopped after
``browser.idle_stop_s`` without an action. Everything it keeps is under the agent's own
folder, beside its files and not among them, so the agent cannot read or edit it:

    <agent>/browser/profile/     the browser's own profile, this agent's alone
    <agent>/browser/status.json  what the owner's Browser tab shows
    <agent>/browser/latest.png   the newest picture; the next one replaces it

The keeper lives in the village process. The web process only reads the two files.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jimmyhq.browser import config, engine
from jimmyhq.browser.fence import Fence, FenceProxy
from jimmyhq.browser.page import ChromeDriver, Driver, DriverError, Look, Tab

log = logging.getLogger("jimmyhq.browser")

STATUS, PICTURE, PROFILE = "status.json", "latest.png", "profile"
IDLE_CHECK_S = 5.0
TITLE_CHARS = 200


def folder(agent_dir: Path) -> Path:
    """Where an agent's browser keeps its things: outside its files."""
    return Path(agent_dir) / "browser"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def read_status(agent_dir: Path) -> dict[str, Any]:
    try:
        found = json.loads((folder(agent_dir) / STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


class Session:
    def __init__(self, core: Any, agent_id: str, agent_dir: Path, driver: Driver,
                 fence: Fence, proxy: FenceProxy | None):
        self.core, self.agent_id, self.dir = core, agent_id, folder(agent_dir)
        self.driver, self.fence, self.proxy = driver, fence, proxy
        self.used = time.monotonic()
        self.seen: str | None = None  # a fingerprint of the page as the agent last saw it
        self.picture: bytes | None = None  # the newest picture, for the agent's next view
        self.lock = asyncio.Lock()
        self.watch: asyncio.Task | None = None

    def touch(self) -> None:
        self.used = time.monotonic()

    def record(self, look: Look | None, tabs: list[Tab], picture: bytes | None) -> None:
        """Leave what the owner's Browser tab shows. Addresses and titles only."""
        now = int(time.time() * 1000)
        status = {"running": True, "updated_ms": now, "picture_ms": None,
                  "tabs": [{"url": t.url[:2000], "title": t.title[:TITLE_CHARS],
                            "active": t.active} for t in tabs],
                  "refused": self.fence.refused_total,
                  "refused_last": [{"ts_ms": r.ts_ms, "host": r.host, "rule": r.rule}
                                   for r in list(self.fence.refused)[-10:]]}
        try:
            if picture:
                _write(self.dir / PICTURE, picture)
                status["picture_ms"] = now
            else:
                with contextlib.suppress(OSError):
                    (self.dir / PICTURE).unlink()
            _write(self.dir / STATUS, json.dumps(status).encode("utf-8"))
        except OSError:
            log.exception("could not write the browser status of %s", self.agent_id)

    def record_stopped(self) -> None:
        with contextlib.suppress(OSError):
            status = read_status(self.dir.parent) | {
                "running": False, "tabs": [], "updated_ms": int(time.time() * 1000)}
            _write(self.dir / STATUS, json.dumps(status).encode("utf-8"))


class NotInstalled(Exception):  # noqa: N818 - an answer for the agent and the owner
    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


class Manager:
    """Every running browser, by agent. ``driver_factory``, ``resolver`` and
    ``also_allow`` exist for the tests: a driver that needs no browser, names that
    resolve from a table, and the one loopback server a test serves pages from."""

    def __init__(self, core: Any):
        self.core = core
        self.sessions: dict[str, Session] = {}
        self.driver_factory: Callable[[Session | None, Fence, Path], Driver] | None = None
        self.resolver: Any = None
        self.also_allow: tuple[tuple[str, int], ...] = ()

    def S(self, name: str) -> Any:  # noqa: N802
        return self.core.settings.get("browser." + name)

    def peek(self, agent_id: str) -> Session | None:
        session = self.sessions.get(agent_id)
        return session if session is not None and session.driver.alive() else None

    def _fence(self, agent_id: str) -> Fence:
        more: dict[str, Any] = {"also_allow": self.also_allow}
        if self.resolver is not None:
            more["resolver"] = self.resolver
        return Fence(self.core, allow_only=lambda: config.allow_hosts(self.core, agent_id),
                     **more)

    async def session(self, agent_id: str, agent_dir: Path) -> Session:
        """This agent's running browser, started now if it is not."""
        session = self.sessions.get(agent_id)
        if session is not None and session.driver.alive():
            session.touch()
            self._watch(session)
            return session
        if session is not None:
            await self.stop(agent_id)
        fence, proxy = self._fence(agent_id), None
        base = folder(agent_dir)
        if self.driver_factory is not None:
            driver = self.driver_factory(None, fence, base)
        else:
            program = engine.find(self.core)
            if program is None:
                raise NotInstalled(engine.problem(self.core) or "")
            proxy = FenceProxy(fence)
            port = await proxy.start()
            driver = ChromeDriver(
                program, base / PROFILE, port, width=int(self.S("viewport_width")),
                height=int(self.S("viewport_height")), max_tabs=int(self.S("max_tabs")),
                page_timeout_s=float(self.S("page_timeout_s")),
                settle_ms=int(self.S("settle_ms")),
                start_timeout_s=float(self.S("start_timeout_s")))
        try:
            await driver.start()
        except DriverError:
            if proxy is not None:
                await proxy.stop()
            raise
        session = Session(self.core, agent_id, agent_dir, driver, fence, proxy)
        self.sessions[agent_id] = session
        self._watch(session)
        return session

    def _watch(self, session: Session) -> None:
        if session.watch is None or session.watch.done():
            session.watch = asyncio.get_running_loop().create_task(self._idle(session))

    async def _idle(self, session: Session) -> None:
        """Rule ``browser_idle_stop``: close a browser nobody has used for a while."""
        while self.sessions.get(session.agent_id) is session and session.driver.alive():
            if time.monotonic() - session.used >= float(self.S("idle_stop_s")):
                session.watch = None
                await self.stop(session.agent_id)
                return
            await asyncio.sleep(IDLE_CHECK_S)

    async def stop(self, agent_id: str) -> None:
        session = self.sessions.pop(agent_id, None)
        if session is None:
            return
        with contextlib.suppress(Exception):
            await session.driver.stop()
        if session.proxy is not None:
            with contextlib.suppress(Exception):
                await session.proxy.stop()
        session.record_stopped()
        watch = session.watch
        if watch is not None and watch is not asyncio.current_task():
            watch.cancel()

    def close(self) -> None:
        """The process is ending: no browser outlives it."""
        for session in list(self.sessions.values()):
            kill = getattr(session.driver, "kill", None)
            with contextlib.suppress(Exception):
                if kill is not None:
                    kill()
            session.record_stopped()
        self.sessions.clear()
