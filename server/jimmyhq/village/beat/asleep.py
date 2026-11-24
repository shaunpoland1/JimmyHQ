"""The first stage: a sleeping agent has no beat. Sleep is when the night's memory
work runs, so that is the one thing checked."""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq.village.rules import rule

log = logging.getLogger("jimmyhq.village.beat")

@rule("asleep_no_beat", "village.beat.idle_wait_s")
def asleep_wait(a: Any, idle_s: float) -> float | None:
    """A sleeping agent makes no model call; look again after the idle wait."""
    return idle_s if a.asleep else None


@rule("night_tries", "village.night.tries")
async def night_check(v: Any, agent_id: str) -> None:
    """Once per sleep the day is sorted into memory. Whether it is due, and how many
    tries a night gets, is the night's own rule; here it is only asked on every
    sleeping beat, and a failure is logged so the next beat can ask again."""
    run_if_due = getattr(v.seam("memory.night"), "run_if_due", None)
    if run_if_due is None:
        return
    try:
        await run_if_due(v, agent_id)
    except Exception as e:  # noqa: BLE001 - a failed night must not stop the loop
        log.exception("the night's memory work failed for %s", agent_id)
        v.log.append("platform_fault", agent_id, v.clock.now(), what="night failed",
                     detail=f"{type(e).__name__}: {e}"[:300])
