"""Synthetic scenarios and a stand-in policy (no model) for the simulator.

The policy is deliberately dumb: it does whatever the mode picker says and keeps
trying its current task. The point is to exercise the body, not to be clever.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from jimmyhq.village.world import World


@dataclass
class Task:
    id: str
    steps: int
    kind: str = "normal"  # normal | trap | scroll | bench
    fail_rate: float = 0.1
    available_day: int = 1
    progress: int = 0
    trap_at: int = 2
    trapped: bool = False
    done: bool = False
    last_result: str | None = None
    last_evts: list[str] = field(default_factory=list)
    variant: int = 0


@dataclass
class Scenario:
    name: str
    description: str
    days: int = 7
    n_agents: int = 2
    onboarding: bool = True
    daily_tasks: int = 4
    trap_share: float = 0.0
    scroll_share: float = 0.0
    bench_share: float = 0.15
    # Hours until the owner answers a bench item. None: the owner never answers.
    bench_response_hours: tuple[float, float] | None = (2, 30)
    expect_set_asides: bool | None = None  # True: must happen on traps; False: must not


SCENARIOS = {
    "onboarding_week": Scenario(
        "onboarding_week",
        "Two agents, a first-days list then a trickle of ordinary work; the owner answers "
        "within 2 to 30 hours."),
    "loop_trap": Scenario(
        "loop_trap",
        "One agent; a third of tasks contain a trap (same failure no matter what). "
        "Set-asides must fire.",
        n_agents=1, trap_share=0.35, expect_set_asides=True),
    "healthy_repetition": Scenario(
        "healthy_repetition",
        "Many tasks are long scroll-throughs: same action, new result each time. No "
        "set-asides, no refusals.",
        n_agents=1, scroll_share=0.7, bench_share=0.0, expect_set_asides=False),
    "slow_owner": Scenario(
        "slow_owner",
        "Half the work goes to the bench and the owner never answers. Waiting must not "
        "wreck the village.",
        n_agents=2, bench_share=0.5, bench_response_hours=None),
    "empty_list": Scenario(
        "empty_list",
        "The first-days list only, then nothing to do. Purpose should fall and the body "
        "should say so honestly.",
        n_agents=1, daily_tasks=0, days=4),
}

# A new agent's first-days list, so purpose has something real to fill it before any
# work for the owner exists: (id, steps, goes to the bench, first day it is offered).
ONBOARDING: tuple[tuple[str, int, bool, int], ...] = (
    ("explore-computer", 6, False, 1),
    ("first-diary", 2, False, 1),
    ("read-village-docs", 5, False, 1),
    ("follow-a-curiosity", 6, False, 1),
    ("make-something", 4, False, 1),
    ("meet-coworkers", 3, False, 2),
    ("read-starter-notes", 4, False, 3),
    ("first-questions", 3, True, 3),
    ("priorities-draft", 6, True, 3),
    ("propose-role", 2, True, 3),
)


def onboarding_tasks() -> list[Task]:
    return [Task(task_id, steps, "bench" if bench else "normal", 0.05, day)
            for task_id, steps, bench, day in ONBOARDING]


def make_tasks(sc: Scenario, rng: random.Random) -> list[Task]:
    tasks = onboarding_tasks() if sc.onboarding else []
    n = 0
    for day in range(1, sc.days + 1):
        for _ in range(sc.daily_tasks):
            n += 1
            r = rng.random()
            if r < sc.trap_share:
                kind = "trap"
            elif r < sc.trap_share + sc.scroll_share:
                kind = "scroll"
            elif r < sc.trap_share + sc.scroll_share + sc.bench_share:
                kind = "bench"
            else:
                kind = "normal"
            steps = rng.randint(12, 30) if kind == "scroll" else rng.randint(3, 10)
            tasks.append(Task(f"task-{n}-{kind}", steps, kind, 0.1,
                              max(day, 3 if sc.onboarding else 1), trap_at=rng.randint(1, 3)))
    return tasks


class Policy:
    """Stand-in for the brain: turns a mode into one action per beat."""

    def __init__(self, world: World, agent_id: str, tasks: list[Task], sc: Scenario,
                 rng: random.Random, others: list[str]):
        self.w, self.id, self.tasks, self.sc = world, agent_id, tasks, sc
        self.rng, self.others = rng, others
        self.counter = 0
        self.pending_bench: list[tuple[float, str]] = []  # (respond_at, item)

    def _uid(self) -> int:
        self.counter += 1
        return self.counter

    def _next_task(self, day: int) -> Task | None:
        a = self.w.agents[self.id]
        cur = next((t for t in self.tasks if t.id == a.current_task and not t.done), None)
        if cur and cur.id not in a.parked:
            return cur
        for t in self.tasks:
            if not t.done and t.available_day <= day and t.id not in a.parked:
                return t
        return None

    def beat(self, day: int) -> None:
        a = self.w.agents[self.id]
        now = self.w.clock.now()
        for at, item in list(self.pending_bench):
            if now >= at:
                self.pending_bench.remove((at, item))
                self.w.respond(self.id, item, "approved")
        if a.asleep:
            return
        mode = self.w.pick_mode(a)
        if mode == "work":
            t = self._next_task(day)
            if t:
                self._work(t)
        elif mode == "social":
            other = self.rng.choice(self.others) if self.others else "owner"
            self.w.message(self.id, other, topic_match=self.rng.random() < 0.35,
                           venting=a.needs["frustration"] > 40)
        elif mode == "explore":
            u = self._uid()
            self.w.act(self.id, "browse", {"url": f"https://example.org/wander/{u}"},
                       before=None, result=f"page {u} about something new", ok=True,
                       category="explore")
        elif mode == "outlet":
            u = self._uid()
            self.w.act(self.id, "play", {"game": "puzzle", "move": u}, before=None,
                       result=f"puzzle state {u}", ok=True, category="outlet")

    def _work(self, t: Task) -> None:
        a = self.w.agents[self.id]
        if t.kind == "trap" and t.progress >= t.trap_at and not t.trapped and t.variant >= 0:
            t.trapped = True
        if t.trapped:
            # Same failure whatever it tries. It cycles between a few variants, the way a
            # real agent in a rut does.
            t.variant = (t.variant + 1) % 3
            ev = self.w.act(self.id, "click", {"element": f"submit-{t.variant}"},
                            before=t.last_result, result="error: element not found",
                            ok=False, task=t.id)
            if ev:
                t.last_result = "error: element not found"
            if t.id in a.parked:  # it was just set aside; the resume note may help next time
                t.trapped = False
                t.variant = -1 if self.rng.random() < 0.7 else 0
            return
        if t.kind == "scroll":
            u = self._uid()
            ev = self.w.act(self.id, "scroll", {"direction": "down"}, before=t.last_result,
                            result=f"{t.id} rows {u * 20}-{u * 20 + 19}", ok=True, task=t.id)
        else:
            ok = self.rng.random() >= t.fail_rate
            u = self._uid()
            res = f"{t.id} step {t.progress} result {u}" if ok else f"{t.id} error {u}"
            tool = self.rng.choice(["files.write", "type", "click", "read_page"])
            ev = self.w.act(self.id, tool, {"target": f"{t.id}-{t.progress}-{u}"},
                            before=t.last_result, result=res, ok=ok, task=t.id)
        if ev is None:
            return
        t.last_result = ev.data["result"]
        if ev.data["ok"]:
            t.progress += 1
            t.last_evts = (t.last_evts + [ev.id])[-3:]
        if t.progress >= t.steps:
            t.done = True
            if t.kind == "bench":
                item = f"bench-{self.id}-{t.id}"
                self.w.submit(self.id, item, task=t.id)
                a.current_task = None
                rh = self.sc.bench_response_hours
                if rh:
                    self.pending_bench.append(
                        (self.w.clock.now() + self.rng.uniform(*rh) * 3600, item))
            else:
                self.w.complete(self.id, t.id, verified_by=t.last_evts)
