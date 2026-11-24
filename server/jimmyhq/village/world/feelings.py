"""Feeling words, read off the body and the log. No evidence, no phrase."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import Agent
    from .world import World

NO_TASK = "(no task)"


def feelings(world: World, a: Agent, window_min: float = 60) -> list[dict[str, Any]]:
    """The phrases that are true of this agent now, each with the events that justify it.
    Every phrase returned is also logged as a `feeling` event."""
    log = world.log
    now = world.clock.now()
    recent = log.query(agent=a.id, since=now - window_min * 60)
    changes = [e for e in recent if e.kind == "need_change"]
    out: list[tuple[str, list[str | None]]] = []
    n = a.needs
    if n["energy"] < 30 and a.last_wake_evt:
        out.append(("tired", [a.last_wake_evt]))
    # Stuck = 3+ fruitless attempts in a row on one task, with no success in between.
    # Actions outside any task count too: otherwise hours of refused clicks with no task
    # named never feel like anything.
    streak: dict[str, list[str]] = defaultdict(list)
    for e in recent:
        if e.kind not in ("action", "validator_reject"):
            continue
        task = e.data.get("task") or NO_TASK
        fruitless = (e.kind == "validator_reject" or not e.data["ok"]
                     or e.data["no_new_result"])
        if fruitless:
            streak[task].append(e.id)
        else:
            streak[task] = []
            # A refusal logged without a task (a daily limit, an empty call) isn't "on"
            # any task, so real progress on any task breaks its streak. Otherwise a few
            # such refusals spread over an hour of good work still read as "stuck", and
            # the agent takes that as its cue to stop working.
            streak[NO_TASK] = []
    # Only a streak still running counts. A streak broken by a good action must stop
    # reading as "stuck" at once, not for the rest of the window.
    stuck = max(streak.values(), key=len, default=[])
    if len(stuck) >= 3:
        out.append(("stuck", list(stuck)))
    waits = [w["evt"] for w in a.waiting.values() if now - w["since"] > 3600]
    if waits:
        out.append(("waiting_on_others", waits))
    if n["social"] < 30:
        out.append(("lonely", [a.last_message_evt or a.last_wake_evt]))
    routine = [e.id for e in changes if e.data["cause"] == "routine_work"]
    if n["curiosity"] > 70 and routine:
        out.append(("restless", routine[-3:]))
    contributed = log.count(agent=a.id, kinds=["task_done", "bench_submitted"],
                            since=now - 7200)
    progressing = [e for e in recent if e.kind == "action" and e.data.get("category") == "work"
                   and e.data["ok"] and not e.data["no_new_result"]]
    if n["purpose"] < 30 and not contributed and not progressing:
        out.append(("aimless", [a.last_completion_evt or a.last_wake_evt]))
    done = [e.id for e in recent if e.kind == "task_done"]
    if done and n["purpose"] > 60:
        out.append(("useful", done))
    if not out and world.mood(a)[0] > 0.2:
        out.append(("settled", [changes[-1].id] if changes else [a.last_wake_evt]))
    result = []
    for phrase, cited in out:
        evidence = [e for e in cited if e]
        if not evidence:
            continue
        log.append("feeling", a.id, now, phrase=phrase, evidence=evidence, source="body")
        result.append({"phrase": phrase, "evidence": evidence})
    return result
