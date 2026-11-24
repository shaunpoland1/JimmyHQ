"""Run scenarios on an accelerated clock and gather what the exit tests score."""

from __future__ import annotations

import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from jimmyhq.village.world import Getter, MemoryLog, SimClock, World, divergence
from jimmyhq.village.world.body import NEEDS
from jimmyhq.village.world.temperament import roll_group

from .scenarios import SCENARIOS, Policy, Scenario, make_tasks

STEP_MIN = 5
PINNED_HOURS = 2.0
TZ = "UTC"  # any zone gives the same run: only the local hour matters
START = (2026, 11, 9, 9, 0)  # a Monday, 9 in the morning


def simulate(sc: Scenario, seed: int, get: Getter) -> dict[str, Any]:
    rng = random.Random(seed)
    start = datetime(*START, tzinfo=ZoneInfo(TZ))
    clock = SimClock(start, TZ)
    log = MemoryLog()
    world = World(get, log, clock)
    ids = [f"agent-{chr(97 + i)}" for i in range(sc.n_agents)]
    for aid, temp in zip(ids, roll_group(rng, sc.n_agents), strict=True):
        world.add_agent(aid, temp)
    policies = {aid: Policy(world, aid, make_tasks(sc, random.Random(rng.random())), sc,
                            random.Random(rng.random()), [o for o in ids if o != aid])
                for aid in ids}
    awake_min: dict[str, float] = defaultdict(float)
    mode_min: dict[str, Counter] = defaultdict(Counter)
    pinned_run: dict[tuple, float] = defaultdict(float)  # (agent, need, bound) -> minutes
    pinned_max: dict[tuple, float] = defaultdict(float)
    samples: dict[tuple, list[float]] = defaultdict(list)
    wait_max: dict[str, float] = defaultdict(float)
    steps = sc.days * 24 * 60 // STEP_MIN
    for i in range(steps):
        clock.advance(STEP_MIN)
        world.advance(STEP_MIN)
        day = int((clock.now() - start.timestamp()) // 86400) + 1
        for aid, pol in policies.items():
            pol.beat(day)
            a = world.agents[aid]
            mode_min[aid][a.mode] += STEP_MIN
            if a.asleep:
                continue
            awake_min[aid] += STEP_MIN
            wait_max[aid] = max(wait_max[aid], sum(w["caused"] for w in a.waiting.values()))
            for n in NEEDS:
                v = a.needs[n]
                samples[(aid, n)].append(v)
                for bound in (0.0, 100.0):
                    k = (aid, n, bound)
                    if abs(v - bound) < 0.5:
                        pinned_run[k] += STEP_MIN
                        pinned_max[k] = max(pinned_max[k], pinned_run[k])
                    else:
                        pinned_run[k] = 0.0
        if (i + 1) % (60 // STEP_MIN) == 0:
            for aid in ids:
                if not world.agents[aid].asleep:
                    world.feelings(aid)
    flags = []
    for aid in ids:
        flags += divergence.run(log, aid, start.timestamp(), clock.now(), get)
    tasks_by_id = {t.id: t for p in policies.values() for t in p.tasks}
    set_asides = [e for e in log.events if e.kind == "set_aside"]
    rejects = [e for e in log.events if e.kind == "validator_reject"]

    def kind_of(e) -> str | None:
        task = tasks_by_id.get(e.data["task"])
        return task.kind if task else None

    return {
        "scenario": sc.name, "seed": seed, "days": sc.days, "agents": ids,
        "temperaments": {aid: world.agents[aid].temperament for aid in ids},
        "awake_hours": {aid: awake_min[aid] / 60 for aid in ids},
        "mode_share": {aid: {m: round(v / sum(mode_min[aid].values()), 3)
                             for m, v in mode_min[aid].items()} for aid in ids},
        "pinned": {f"{k[0]}:{k[1]}@{int(k[2])}": round(v / 60, 2)
                   for k, v in pinned_max.items() if v / 60 >= PINNED_HOURS},
        "need_stats": {f"{aid}:{n}": _stats(samples[(aid, n)]) for aid in ids for n in NEEDS},
        "completions": sum(1 for e in log.events if e.kind == "task_done"),
        "submissions": sum(1 for e in log.events if e.kind == "bench_submitted"),
        "set_asides": len(set_asides),
        "set_asides_on_traps": sum(1 for e in set_asides if kind_of(e) == "trap"),
        "set_asides_on_healthy": sum(1 for e in set_asides
                                     if kind_of(e) not in (None, "trap")),
        "traps": sum(1 for t in tasks_by_id.values()
                     if t.kind == "trap" and t.progress >= t.trap_at),
        "rejects": Counter(e.data["reason"] for e in rejects),
        "rejects_on_scroll": sum(1 for e in rejects if e.data["tool"] == "scroll"),
        "sleeps": Counter(e.data["sleep_kind"] for e in log.events if e.kind == "sleep"),
        "flags": Counter(f["type"] for f in flags),
        "feelings": Counter(e.data["phrase"] for e in log.events if e.kind == "feeling"),
        "events": len(log.events),
        "wait_frustration_max": {aid: round(wait_max[aid], 2) for aid in ids},
        "unexplained_changes": sum(1 for e in log.events if e.kind == "need_change"
                                   and (not e.data.get("cause") or not e.data.get("evidence"))),
    }


def _stats(xs: list[float]) -> dict[str, float]:
    if not xs:
        return {}
    return {"min": round(min(xs), 1), "mean": round(statistics.fmean(xs), 1),
            "max": round(max(xs), 1)}


def run_all(get: Getter, seeds: int = 20) -> dict[str, list[dict[str, Any]]]:
    return {name: [simulate(sc, s, get) for s in range(seeds)]
            for name, sc in SCENARIOS.items()}
