"""The village process: the world's clock and every agent's loop.

It runs beside the web and worker processes (``jimmyhq village``) and is the only one
that holds the bodies. With nobody in the village it idles: it keeps its heartbeat,
watches for a birth, and does nothing else.

    every second       take in what the web process left (chat, decisions, commands)
    every minute       move the world forward
    per agent, forever beat, wait, beat

One agent's trouble never stops the process or another agent: a beat that raises is
logged and waited out, and a loop that somehow ends is started again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jimmyhq.apps import sync as apps_sync
from jimmyhq.core import Core, open_core
from jimmyhq.village import intake
from jimmyhq.village.beat import pace
from jimmyhq.village.beat.run import safe_beat
from jimmyhq.village.runtime import Village

log = logging.getLogger("jimmyhq.village")

NAME = "village"
POLL_S = 1.0  # also how quickly a stop is noticed
BEAT_S = 5.0  # the process's own sign of life
TICK_S = 60.0


async def agent_loop(v: Village, agent_id: str,
                     pause: Callable[[Any, float], Awaitable[Any]] = pace.wait) -> None:
    """Beat, wait, beat, for as long as the agent lives here."""
    settle_in = getattr(v.seam("docs"), "settle_in", None)
    if settle_in is not None:
        try:
            settle_in(v, agent_id)
        except Exception:  # noqa: BLE001 - missing documents must not keep an agent from living
            log.exception("could not give %s its documents", agent_id)
    while agent_id in v.world.agents:
        seconds = await safe_beat(v, agent_id)
        await pause(v.state(agent_id), seconds)


def _guarded(what: str, fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - the process outlives any one failure
        log.exception("%s failed", what)


def reconcile(v: Village, loops: dict[str, asyncio.Task]) -> None:
    """One running loop for everyone who lives here, none for anyone who has left."""
    for agent_id in list(loops):
        task = loops[agent_id]
        if agent_id not in v.world.agents:
            task.cancel()
            del loops[agent_id]
        elif task.done():
            if not task.cancelled() and task.exception() is not None:
                log.error("the loop of %s ended: %r; starting it again", agent_id,
                          task.exception())
            del loops[agent_id]
    for agent_id in v.world.agents:
        if agent_id not in loops:
            loops[agent_id] = asyncio.create_task(agent_loop(v, agent_id),
                                                  name=f"village:{agent_id}")


async def main(stop: threading.Event, core: Core, v: Village | None = None,
               poll_s: float = POLL_S, tick_s: float = TICK_S) -> None:
    v = v or Village(core)
    _guarded("loading the village", v.load)
    loops: dict[str, asyncio.Task] = {}
    last_beat = last_tick = float("-inf")
    log.info("village started with %d agents", len(v.world.agents))
    try:
        while not stop.is_set():
            now = time.monotonic()
            if now - last_beat >= BEAT_S:
                last_beat = now
                _guarded("the heartbeat", lambda: core.metrics.beat(NAME))
            _guarded("intake", lambda: intake.poll(v))
            _guarded("keeping the apps in step", lambda: apps_sync.keep_in_step(core))
            if now - last_tick >= tick_s:
                last_tick = now
                _guarded("the world tick", v.tick)
            reconcile(v, loops)
            await asyncio.sleep(poll_s)
    finally:
        for task in loops.values():
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*loops.values(), return_exceptions=True)
        _guarded("saving the village", v.save)
        v.close()


def run(stop: threading.Event | None = None, core: Core | None = None) -> None:
    stop = stop or threading.Event()
    core = core or open_core()
    try:
        asyncio.run(main(stop, core))
    finally:
        core.close()
