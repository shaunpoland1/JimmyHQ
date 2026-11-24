"""How long until the next beat, and the wait itself."""

from __future__ import annotations

import asyncio
from typing import Any

from jimmyhq.village.rules import rule

PACED_MODES = ("work", "explore", "outlet")


@rule("heartbeat", "village.heartbeat_min_s")
def heartbeat_seconds(lo: float, hi: float, ok: bool, mode: str, frustration: float,
                      energy: float) -> int:
    """Engaged and going well, the next beat comes as soon as the fastest pace allows.
    A frustrated or tired agent slows down. (Effort is charged by working time, not per
    action, so a quick pace does not tire an agent faster per hour.)"""
    x = 0.0 if ok and mode in PACED_MODES else 0.1
    x += max(0.0, frustration - 50) / 150
    x += max(0.0, 40 - energy) / 100
    return round(lo + (hi - lo) * max(0.0, min(1.0, x)))


def heartbeat(v: Any, a: Any, mode: str, ok: bool) -> int:
    return heartbeat_seconds(v.S("village.heartbeat_min_s"), v.S("village.heartbeat_max_s"),
                             ok, mode, a.needs["frustration"], a.needs["energy"])


async def wait(st: Any, seconds: float) -> bool:
    """Wait for the next beat. True when something ended the wait early."""
    try:
        await asyncio.wait_for(st.wake.wait(), timeout=max(0.0, seconds))
        early = True
    except TimeoutError:
        early = False
    st.wake.clear()
    return early
