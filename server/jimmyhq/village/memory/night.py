"""When the night's sorting runs: once per sleep, with spaced retries.

A sleep gets up to ``village.night.tries`` tries, ``retry_gap_min`` apart, so a model
that is down for an hour does not cost the agent its day. The first
``smart_only_tries`` wait for the night model; later ones may fall back to the agent's
everyday model, and say so. A nap is sorted by the everyday model.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq.village.memory import gather
from jimmyhq.village.memory.consolidate import consolidate
from jimmyhq.village.settings_night import night_setting

log = logging.getLogger(__name__)

NAP = "nap"


def due(now: float, attempts: list[tuple[float, bool]], tries: int, gap_s: float) -> bool:
    """Should a pass run now? ``attempts`` are this sleep's passes so far, oldest first,
    as (when, succeeded). Not once one has succeeded, not past ``tries``, and not
    within ``gap_s`` of the last try."""
    if any(ok for _, ok in attempts):
        return False
    if len(attempts) >= tries:
        return False
    return not attempts or now - attempts[-1][0] >= gap_s


def fallback_allowed(attempts_so_far: int, smart_only_tries: int) -> bool:
    """May this try settle for the everyday model? Only after the first
    ``smart_only_tries`` tries have waited for the night model."""
    return attempts_so_far >= smart_only_tries


def attempts_this_sleep(event_log: Any, agent_id: str) -> list[tuple[float, bool]] | None:
    """This sleep's passes, or None if the agent has never slept."""
    slept = event_log.last(agent=agent_id, kinds=["sleep"])
    if slept is None:
        return None
    return [(e.ts, gather.succeeded(e))
            for e in event_log.query(agent=agent_id, kinds=[gather.CONSOLIDATION],
                                     since=slept.ts)]


async def run_if_due(v: Any, agent_id: str) -> dict[str, Any] | None:
    """Sort the sleeping agent's day if a pass is due. Returns what the pass did, or
    None when none ran. Never raises: a fault is logged and counts as a failed try."""
    agent = v.world.agents.get(agent_id)
    if agent is None or not agent.asleep:
        return None
    attempts = attempts_this_sleep(v.log, agent_id)
    if attempts is None:
        return None
    core = v.core
    if not due(v.clock.now(), attempts, night_setting(core, "tries"),
               night_setting(core, "retry_gap_min") * 60.0):
        return None
    nap = agent.sleep_kind == NAP
    try:
        return await consolidate(
            v, agent_id, nap=nap,
            allow_fallback=fallback_allowed(len(attempts),
                                            night_setting(core, "smart_only_tries")))
    except Exception as exc:  # noqa: BLE001  (the beat loop must never die of a bad night)
        log.exception("night pass failed for %s", agent_id)
        now = v.clock.now()
        v.log.append("platform_fault", agent_id, now, what="consolidation failed",
                     detail=f"{type(exc).__name__}: {exc}"[:300])
        ev = v.log.append(gather.CONSOLIDATION, agent_id, now, failed=True, nap=nap,
                          why="the pass raised an error", ops=0, results=[])
        return {"event": ev.id, "ok": False, "by": None, "results": [], "snapshot": None,
                "notes": "the pass raised an error"}
