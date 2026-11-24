"""The world core.

Owns every body. The brain can only take actions; the world applies their effects.
Every need change is logged with a concrete cause and the event ids that justify it
(the honest body rule).

Settings are read through a getter the caller passes in, by short key such as
``energy.drain_per_min``, so the world runs the same against the settings registry, a
plain dict or the simulator. Each value is read when it is used, so a changed setting
takes effect on the next beat.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import feelings as _feelings
from .agent import Agent
from .body import NEEDS, compute_mood, starting_needs
from .clock import Clock, local_hour
from .defaults import Getter
from .events import Event, EventLog
from .guards import RepeatGuard, action_key, obs_hash
from .tools import DESKTOP_TOOLS, MAKING_TOOLS, SAME_RESULT_TOOLS, WALL_EXEMPT

__all__ = ["Agent", "World"]


class World:
    def __init__(self, get: Getter, log: EventLog, clock: Clock):
        self._get = get
        self.log = log
        self.clock = clock
        self.agents: dict[str, Agent] = {}
        # Which of an agent's priorities are standing (they never finish). Set by the
        # runtime, which knows them; the bare world (simulator, tests) has none.
        self.standing: Callable[[str], set[str]] = lambda agent_id: set()
        # Whether agents have a way to talk with the owner. Set by the runtime; the bare
        # world has no such channel.
        self.owner_channel_open: Callable[[], bool] = lambda: False

    # ------------------------------------------------------------------ setup
    def S(self, key: str) -> Any:  # noqa: N802
        return self._get(key)

    def _opt(self, key: str, default: Any) -> Any:
        """A setting the runtime may declare. The bare world (simulator, tests) does
        not know it and uses the tuned value."""
        try:
            return self._get(key)
        except (KeyError, ValueError):
            return default

    def add_agent(self, agent_id: str, temperament: dict[str, Any],
                  needs: dict[str, float] | None = None) -> Agent:
        a = Agent(id=agent_id, temperament=temperament, needs=dict(needs or starting_needs()),
                  guard=RepeatGuard(int(self.S("guards.repeat_window"))))
        self.agents[agent_id] = a
        ev = self.log.append("born", agent_id, self.clock.now(), temperament=temperament,
                             needs=dict(a.needs))
        a.last_wake_evt = ev.id
        return a

    def restore_agent(self, a: Agent) -> Agent:
        """Put back a body loaded from saved state. Nothing is logged: it was never gone."""
        self.agents[a.id] = a
        return a

    def _guard(self, a: Agent) -> RepeatGuard:
        """The agent's repeat guard, resized if the window setting changed."""
        window = int(self.S("guards.repeat_window"))
        if a.guard.window != window:
            a.guard.resize(window)
        return a.guard

    def _needs(self, a: Agent) -> dict[str, float]:
        return {k: round(v, 1) for k, v in a.needs.items()}

    # ------------------------------------------------------------- body core
    def mood(self, a: Agent) -> tuple[float, float]:
        return compute_mood(a.needs, a.temperament["mood_weights"])

    def _change(self, a: Agent, need: str, delta: float, cause: str,
                evidence: list[str]) -> Event | None:
        assert need in NEEDS and evidence, "every need change needs a cause and evidence"
        old = a.needs[need]
        new = max(0.0, min(100.0, old + delta))
        if abs(new - old) < 1e-9:
            return None
        a.needs[need] = new
        ev = self.log.append("need_change", a.id, self.clock.now(), need=need,
                             delta=round(new - old, 4), value=round(new, 3), cause=cause,
                             evidence=list(evidence))
        if need == "frustration" and new > old:
            self._check_set_aside(a, ev)
        return ev

    def _frustrate(self, a: Agent, base: float, cause: str,
                   evidence: list[str]) -> Event | None:
        tol = a.temperament["frustration_tolerance"]
        fatigue = 1 + self.S("frustration.fatigue_factor") * max(0.0, 50 - a.needs["energy"]) / 50
        return self._change(a, "frustration", base / tol * fatigue, cause, evidence)

    def _relieve(self, a: Agent, amount: float, cause: str,
                 evidence: list[str]) -> Event | None:
        """Relief lowers frustration, but never below the resting floor (calm is not zero)."""
        floor = self.S("frustration.relief_floor")
        room = a.needs["frustration"] - floor
        if room <= 0:
            return None
        return self._change(a, "frustration", -min(amount, room), cause, evidence)

    # ----------------------------------------------------------- time passing
    def _hour(self, a: Agent) -> float:
        """The hour by this agent's own body clock."""
        return (local_hour(self.clock) - a.temperament.get("sleep_offset_minutes", 0) / 60) % 24

    def _circadian(self, h: float) -> float:
        late = self.S("energy.late_day_multiplier")
        if h < 5:
            return late
        return 1 + (late - 1) * max(0.0, min(1.0, (h - 15) / 8))

    def advance(self, minutes: float) -> None:
        """Called after the clock moved forward `minutes`."""
        for a in self.agents.values():
            self._advance_agent(a, minutes)

    def _advance_agent(self, a: Agent, m: float) -> None:
        h = self._hour(a)
        tick = self.log.append("tick", a.id, self.clock.now(), minutes=m, mode=a.mode,
                               asleep=a.asleep, hour=round(h, 2))
        ev = [tick.id]
        t = a.temperament
        if a.asleep:
            self._change(a, "energy", self.S("energy.sleep_gain_per_min") * m, "sleep", ev)
            d = self.S("frustration.sleep_baseline") - a.needs["frustration"]
            step = min(abs(d), self.S("frustration.sleep_settle_per_min") * m)
            if step:
                self._change(a, "frustration", step if d > 0 else -step, "sleep", ev)
            self._maybe_wake(a, h, ev)
        else:
            if a.mode == "rest":
                self._change(a, "energy", self.S("energy.rest_gain_per_min") * m, "resting", ev)
            else:
                self._change(a, "energy", -self.S("energy.drain_per_min")
                             * t["energy_drain_rate"] * self._circadian(h) * m,
                             "time_awake", ev)
            if a.mode != "social" and self.has_company(a):
                self._change(a, "social",
                             -self.S("social.drain_per_min") * t["social_capacity"] * m,
                             "time_alone",
                             ev + ([a.last_message_evt] if a.last_message_evt else []))
            if a.mode == "work":
                self._change(a, "curiosity", self.S("curiosity.routine_gain_per_min")
                             * t["boredom_rate"] * m, "routine_work", ev)
            elif a.mode != "explore":
                self._change(a, "curiosity", self.S("curiosity.idle_gain_per_min")
                             * t["boredom_rate"] * m, "time", ev)
            # Purpose drains steadily, then levels off below 50 so it never saturates at 0.
            p_level = min(1.0, a.needs["purpose"] / 50)
            self._change(a, "purpose", -self.S("purpose.drain_per_min") * p_level * m,
                         "time_since_contribution",
                         ev + ([a.last_completion_evt] if a.last_completion_evt else []))
            cap = self.S("frustration.wait_cap_per_item")
            room_total = (self.S("frustration.wait_cap_total")
                          - sum(w["caused"] for w in a.waiting.values()))
            for item, w in a.waiting.items():
                add = min(self.S("frustration.wait_per_hour") * m / 60, cap - w["caused"],
                          room_total)
                if add > 0:
                    room_total -= add
                    w["caused"] += add
                    self._change(a, "frustration", add, f"waiting_on:{item}",
                                 [w["evt"], tick.id])
            self._maybe_sleep(a, h, ev)
        self._check_resume(a)

    # ------------------------------------------------------------------ sleep
    def _in_sleep_window(self, h: float) -> bool:
        start = self.S("sleep.start_hour") % 24
        wake = self.S("sleep.wake_hour")
        return h >= start or h < wake if start > wake else start <= h < wake

    def _maybe_sleep(self, a: Agent, h: float, ev: list[str]) -> None:
        today = self.clock.local_time().date().isoformat()
        if a.needs["energy"] <= self.S("sleep.forced_sleep_energy"):
            self._sleep(a, "night" if self._in_sleep_window(h) else "forced", ev)
        elif self._in_sleep_window(h):
            self._sleep(a, "night", ev)
        elif self._early_night(a, h):
            self._sleep(a, "night", ev)
        elif (self._opt("sleep.nap_from_hour", 12) <= h < self._opt("sleep.nap_until_hour", 18)
              and a.needs["energy"] < self.S("sleep.nap_below_energy")
              and a.napped_on != today):
            a.napped_on = today
            a.nap_until = self.clock.now() + self.S("sleep.nap_minutes") * 60
            self._sleep(a, "nap", ev)

    def _early_night(self, a: Agent, h: float) -> bool:
        """In the evening, rest or a tired agent with nothing to do goes to bed.

        Without this an evening rest runs for hours with no beats and frustration stuck
        where it was, and a tired agent whose work is done keeps writing goodnight notes
        because nothing lets it end the day. Night sleep restores energy faster, settles
        frustration and runs consolidation."""
        if not self.S("sleep.early_night_hour") <= h < 24:
            return False
        if a.mode == "rest":
            return True
        # A standing priority never finishes, so being on one is not "having a task" at
        # bedtime. Counted as a task, it keeps a tired agent up until frustration sets
        # the priority aside for it.
        idle = a.current_task is None or a.current_task in self.standing(a.id)
        return idle and a.needs["energy"] < self.S("sleep.early_night_energy")

    def _sleep(self, a: Agent, kind: str, ev: list[str]) -> None:
        a.asleep, a.sleep_kind, a.mode = True, kind, "sleep"
        a.rest_until = None
        a.wall_streak = 0
        self.log.append("sleep", a.id, self.clock.now(), sleep_kind=kind,
                        needs=self._needs(a), evidence=ev)

    def _maybe_wake(self, a: Agent, h: float, ev: list[str]) -> None:
        wake = False
        if a.sleep_kind == "nap":
            wake = self.clock.now() >= (a.nap_until or 0)
        elif a.sleep_kind == "forced":
            wake = a.needs["energy"] >= self._opt("sleep.forced_wake_energy", 50)
        else:
            w0, extra = self.S("sleep.wake_hour"), self.S("sleep.latest_wake_extra_h")
            if w0 <= h < 12:
                wake = a.needs["energy"] >= self.S("sleep.min_energy_to_wake") or h >= w0 + extra
        if wake:
            a.asleep, a.sleep_kind, a.nap_until, a.mode = False, None, None, "work"
            a.guard.clear()
            e = self.log.append("wake", a.id, self.clock.now(), needs=self._needs(a),
                                evidence=ev)
            a.last_wake_evt = e.id

    def wake_for_call(self, agent_id: str, call: Event) -> Event | None:
        """The owner called (an urgent message): wake from a nap at once, and from night
        sleep once that night's consolidation has succeeded, so a call never costs the
        agent its memories. Otherwise it sees the call the moment it wakes."""
        a = self.agents[agent_id]
        if not a.asleep:
            return None
        if a.sleep_kind not in ("nap", "forced"):
            slept = self.log.last(agent=a.id, kinds=["sleep"])
            since = slept.ts if slept else 0
            done = [e for e in self.log.query(agent=a.id, kinds=["consolidation"], since=since)
                    if not e.data.get("failed")]
            if not done:
                return None
        a.asleep, a.sleep_kind, a.nap_until, a.mode = False, None, None, "work"
        a.guard.clear()
        e = self.log.append("wake", a.id, self.clock.now(), needs=self._needs(a),
                            cause="urgent_call", evidence=[call.id])
        a.last_wake_evt = e.id
        return e

    def has_company(self, a: Agent) -> bool:
        """Anyone to talk to at all: another agent, or a channel to the owner. Without
        that the social need is dormant; draining it with no way to fill it would be a
        body lie."""
        return len(self.agents) > 1 or bool(self.owner_channel_open())

    def company_now(self, a: Agent) -> bool:
        """Someone awake to talk to right now, and no recent empty-room cooldown."""
        if a.social_cooldown_until and self.clock.now() < a.social_cooldown_until:
            return False
        others_awake = any(o.id != a.id and not o.asleep for o in self.agents.values())
        return others_awake or bool(self.owner_channel_open())

    def social_unanswered(self, agent_id: str, evidence: list[str]) -> Event:
        """Nobody answered in the break room. Go back to other things for a while."""
        a = self.agents[agent_id]
        a.social_cooldown_until = (self.clock.now()
                                   + self.S("social.empty_room_cooldown_min") * 60)
        ev = self.log.append("social_unanswered", a.id, self.clock.now(), evidence=evidence,
                             until=a.social_cooldown_until)
        if a.mode == "social":
            self.log.append("mode_change", a.id, self.clock.now(), old="social", new="work",
                            needs=self._needs(a), cause="nobody answered")
            a.mode = "work"
        return ev

    # ------------------------------------------------------------- mode pick
    def pick_mode(self, a: Agent) -> str:
        n = a.needs
        if a.asleep:
            return "sleep"
        if a.rest_until is not None:
            if a.mode == "rest" and self.clock.now() < a.rest_until:
                return "rest"
            a.rest_until = None
        rest_below = self.S("modes.rest_below_energy")
        outlet_at = self.S("modes.outlet_at_frustration")
        if a.mode == "social" and n["social"] < self.S("social.full_at") and self.company_now(a):
            mode = "social"
        elif a.mode == "rest" and n["energy"] < rest_below + self._opt("modes.rest_margin", 20):
            mode = "rest"
        elif (a.mode == "outlet"
              and n["frustration"] > outlet_at - self._opt("modes.outlet_margin", 20)
              and n["energy"] >= rest_below):
            mode = "outlet"
        elif n["energy"] < rest_below:
            mode = "rest"
        elif n["frustration"] >= outlet_at:
            mode = "outlet"
        elif n["social"] < self.S("modes.social_low") and self.company_now(a):
            mode = "social"
        elif (a.mode == "explore" and n["curiosity"]
              > self.S("modes.curiosity_high") - self._opt("modes.explore_margin", 25)):
            mode = "explore"
        elif n["curiosity"] >= self.S("modes.curiosity_high"):
            mode = "explore"
        else:
            mode = "work"
        if mode != a.mode:
            self.log.append("mode_change", a.id, self.clock.now(), old=a.mode, new=mode,
                            needs=self._needs(a))
            a.mode = mode
        return mode

    # --------------------------------------------------------------- actions
    def check(self, agent_id: str, tool: str, args: dict[str, Any] | None, before: Any,
              task: str | None = None) -> Event | None:
        """The guard, before anything happens. Returns the rejection event, or None if
        allowed."""
        a = self.agents[agent_id]
        key = action_key(tool, args)
        bh = obs_hash(before) if before is not None else None
        reason = None
        if task and task in a.parked:
            reason = "task_set_aside"
        elif self.at_wall(a) and tool in DESKTOP_TOOLS and tool not in WALL_EXEMPT:
            reason = "page_wall"
        elif self._guard(a).is_repeat(key, bh):
            reason = "repeat_same_state"
        return self.refuse(agent_id, tool, args, reason, task) if reason else None

    def at_wall(self, a: Agent) -> bool:
        return a.wall_streak >= int(self.S("guards.wall_after"))

    def _count_wall(self, a: Agent, tool: str, fruitful: bool) -> None:
        if tool not in DESKTOP_TOOLS:
            return
        a.wall_streak = 0 if fruitful else a.wall_streak + 1

    def refuse(self, agent_id: str, tool: str, args: dict[str, Any] | None, reason: str,
               task: str | None = None, frustrate: bool = True) -> Event:
        a = self.agents[agent_id]
        rej = self.log.append("validator_reject", a.id, self.clock.now(), tool=tool, args=args,
                              task=task, reason=reason)
        a.last_refusal = rej
        self._count_wall(a, tool, fruitful=False)
        if frustrate:
            self._frustrate(a, self.S("frustration.guard_block"), "validator_reject", [rej.id])
        return rej

    def act(self, agent_id: str, tool: str, args: dict[str, Any] | None, before: Any,
            result: Any, ok: bool, task: str | None = None, category: str = "work",
            checked: bool = False) -> Event | None:
        """Validate (unless already checked) and record one action. None if refused."""
        a = self.agents[agent_id]
        key = action_key(tool, args)
        bh = obs_hash(before) if before is not None else None
        if not checked and self.check(agent_id, tool, args, before, task) is not None:
            return None
        guard = self._guard(a)
        rh = obs_hash(result)
        no_new = tool not in SAME_RESULT_TOOLS and guard.no_new_result(key, rh)
        making = ok and tool in MAKING_TOOLS
        ev = self.log.append("action", a.id, self.clock.now(), tool=tool, args=args, task=task,
                             category=category, ok=ok, making=making, before_hash=bh,
                             result_hash=rh, no_new_result=no_new, result=str(result)[:200])
        guard.remember(key, bh, rh)
        self._count_wall(a, tool, fruitful=ok and not no_new)
        if category in ("work", "outlet"):
            # Effort is charged for working time: action_cost covers effort_full_after_min
            # of work. Quicker actions cost proportionally less, so pace does not change
            # tiredness per hour.
            now, full = self.clock.now(), self.S("energy.effort_full_after_min")
            share = 1.0 if a.last_effort_at is None or full <= 0 else \
                max(0.05, min(1.0, (now - a.last_effort_at) / 60 / full))
            a.last_effort_at = now
            self._change(a, "energy", -self.S("energy.action_cost") * share
                         * a.temperament["energy_drain_rate"], "effort", [ev.id])
        if not ok:
            self._frustrate(a, self.S("frustration.failed_action"), "failed_action", [ev.id])
        if no_new:
            self._frustrate(a, self.S("frustration.same_no_new_result"),
                            "same_action_no_new_result", [ev.id])
        if ok and not no_new:
            if category == "explore":
                self._change(a, "curiosity", -self.S("curiosity.explore_relief"), "explored",
                             [ev.id])
            elif category == "outlet":
                self._relieve(a, self.S("frustration.outlet_relief"), "outlet", [ev.id])
            elif category == "work" and making:
                # A real file change is a small win. Without this, work with no task on
                # the list never calms the agent, so every small refusal stacks up until
                # it is pushed into outlet mode every half hour or so.
                self._relieve(a, self.S("frustration.progress_relief"), "progress", [ev.id])
        if task:
            a.task_history[task].append({"evt": ev.id, "tool": tool, "args": args, "ok": ok,
                                         "no_new_result": no_new, "result": str(result)[:120]})
            if task not in a.parked:
                a.current_task = task
        return ev

    def purpose_from(self, agent_id: str, setting: str, cause: str,
                     evidence: list[str]) -> Event | None:
        """Purpose from the bench: a submission (the agent did its part) or the owner's
        approval. `setting` is the short key of the amount, such as purpose.submission."""
        return self._change(self.agents[agent_id], "purpose", self.S(setting), cause, evidence)

    def complete(self, agent_id: str, task: str, verified_by: list[str] | None) -> Event:
        """A task finished. Only a completion that cites observations counts."""
        a = self.agents[agent_id]
        if not verified_by:
            return self.log.append("claim_unverified", a.id, self.clock.now(), task=task)
        ev = self.log.append("task_done", a.id, self.clock.now(), task=task,
                             verified_by=verified_by)
        self._change(a, "purpose", self.S("purpose.verified_completion"), "verified_completion",
                     [ev.id])
        self._relieve(a, self.S("frustration.small_win"), "small_win", [ev.id])
        a.last_completion_evt = ev.id
        if a.current_task == task:
            a.current_task = None
        a.task_history.pop(task, None)
        return ev

    def submit(self, agent_id: str, item: str, task: str | None = None,
               **data: Any) -> Event:
        """The agent handed something in and now waits on someone. A little purpose at
        once (it did its part); the waiting costs a little frustration as hours pass,
        within the per-item and total caps. ``data`` is kept on the logged event."""
        a = self.agents[agent_id]
        ev = self.log.append("bench_submitted", a.id, self.clock.now(), item=item, task=task,
                             **data)
        self._change(a, "purpose", self.S("purpose.submission"), "submitted_to_bench", [ev.id])
        a.waiting[item] = {"evt": ev.id, "since": ev.ts, "caused": 0.0}
        a.last_completion_evt = ev.id
        return ev

    def wait_again(self, agent_id: str, item: str, ev: Event,
                   instead_of: str | None = None) -> None:
        """Waiting that is not a new submission: a revision the other side must look at
        again, or an item that takes another's place. No purpose is added. An item
        already waited on keeps its count, and a replacement takes over the count of
        what it replaces, so sending something again never resets its cap."""
        a = self.agents[agent_id]
        old = a.waiting.pop(instead_of, None) if instead_of else None
        if item not in a.waiting:
            a.waiting[item] = old or {"evt": ev.id, "since": ev.ts, "caused": 0.0}

    def respond(self, agent_id: str, item: str, verdict: str,
                acknowledged: bool = True) -> Event:
        a = self.agents[agent_id]
        ev = self.log.append("bench_response", a.id, self.clock.now(), item=item,
                             verdict=verdict)
        a.waiting.pop(item, None)
        if acknowledged:
            self._change(a, "purpose", self.S("purpose.acknowledgement"), "acknowledged",
                         [ev.id])
        return ev

    def message(self, agent_id: str, other: str, topic_match: bool = False,
                forced: bool = False, venting: bool = False, heard: bool = False,
                evidence: list[str] | None = None) -> Event:
        """A message exchanged. The speaker fills fully; someone who was spoken to
        (heard=True) fills by social.heard_share of that, since conversation fills both
        ways."""
        a = self.agents[agent_id]
        ev = self.log.append("message", a.id, self.clock.now(), other=other,
                             topic_match=topic_match, forced=forced, venting=venting,
                             heard=heard, evidence=evidence or [])
        mult = (self.S("social.topic_match_mult") if topic_match else 1.0) * \
               (self.S("social.forced_mult") if forced else 1.0) * \
               (self.S("social.heard_share") if heard else 1.0)
        fill = self.S("social.message_fill") * mult / a.temperament["social_capacity"] ** 0.5
        self._change(a, "social", fill, "conversation", [ev.id])
        if venting and not heard:
            self._relieve(a, self.S("frustration.vent_relief"), "vented", [ev.id])
        a.last_message_evt = ev.id
        return ev

    # -------------------------------------------------------- set-aside guard
    def _check_set_aside(self, a: Agent, cause_ev: Event) -> None:
        task, f, at = a.current_task, a.needs["frustration"], self.S("frustration.set_aside_at")
        if f < at:
            a.set_aside_floor = None  # calm again: the usual threshold applies
        if f < max(at, a.set_aside_floor or 0) or not task or task in a.parked:
            return
        tried = a.task_history.get(task, [])[-5:]
        # next_guess is where the brain may later write its best guess at a way forward.
        note = {"trying": task, "tried": tried, "next_guess": None}
        a.parked[task] = {"since": self.clock.now(), "resume_note": note}
        a.current_task = None
        self.log.append("set_aside", a.id, self.clock.now(), task=task, resume_note=note,
                        evidence=[cause_ev.id])

    def rest(self, agent_id: str, minutes: float) -> str | None:
        """The agent chose to stop for a while. Returns why not, or None once it is resting.

        An agent needs an action that means "stop". Without one it tries to end its day
        dozens of times by other means (keys pressed at a blank screen, closing notes,
        rereading its own list) and each refusal adds frustration. Rest is mechanical (no
        beats), restores energy, and from the early-night hour it becomes bed. Whether a
        message from the owner gets it up again is the runtime's decision."""
        a = self.agents[agent_id]
        h = self._hour(a)
        if (h < self.S("sleep.chosen_rest_from_hour")
                and a.needs["energy"] >= self._opt("sleep.rest_refusal_energy", 40)):
            # A bare refusal here is followed by a beat with no action at all. A finished
            # list on a rested morning is the cue for work of its own, so say that.
            # Give the real time and the real time rest opens for this agent, not its
            # body-clock hour: an agent whose clock runs late is otherwise told a time
            # that is not on the wall and an opening hour that has already passed.
            opens = (self.S("sleep.chosen_rest_from_hour")
                     + a.temperament.get("sleep_offset_minutes", 0) / 60) % 24
            return (f"it's {self.clock.local_time():%H:%M} and you have energy; rest is for the "
                    f"evening (for you, from {int(opens):02d}:{round(opens % 1 * 60) % 60:02d}) "
                    "or when you're tired. If everything you owe is done or waiting on the "
                    "owner, the day is open for work of your own: pick the next useful thing "
                    "nobody asked for and start_own_task it")
        if a.current_task and a.current_task not in self.standing(agent_id):
            return f"you're on {a.current_task}: finish it or set_aside it first"
        minutes = max(float(self._opt("sleep.rest_min_minutes", 20)),
                      min(float(self._opt("sleep.rest_max_minutes", 180)), float(minutes)))
        a.rest_until = self.clock.now() + minutes * 60
        if a.mode != "rest":
            self.log.append("mode_change", a.id, self.clock.now(), old=a.mode, new="rest",
                            needs=self._needs(a), cause="chose_rest", minutes=minutes)
            a.mode = "rest"
        return None

    def set_aside(self, agent_id: str, task: str, note: str) -> Event:
        """The agent chose to put a task down. Same mechanics as a forced set-aside."""
        a = self.agents[agent_id]
        resume = {"trying": task, "tried": a.task_history.get(task, [])[-5:],
                  "next_guess": note, "chosen": True}
        a.parked[task] = {"since": self.clock.now(), "resume_note": resume}
        if a.current_task == task:
            a.current_task = None
        return self.log.append("set_aside", a.id, self.clock.now(), task=task,
                               resume_note=resume, chosen=True, evidence=[])

    def choose_task(self, agent_id: str, task: str) -> None:
        """The agent picks a task on purpose. If it is already frustrated, the task gets a
        margin before the body forces it aside. Without the margin a request taken on
        while frustrated is parked by the next minute's tick and then locked away."""
        a = self.agents[agent_id]
        a.current_task = task
        if a.needs["frustration"] >= self.S("frustration.set_aside_at"):
            a.set_aside_floor = max(
                a.set_aside_floor or 0,
                a.needs["frustration"] + self.S("frustration.chosen_resume_margin"))

    def resume_by_choice(self, agent_id: str, task: str) -> Event:
        """The agent picked a set-aside task back up by its own choice."""
        a = self.agents[agent_id]
        p = a.parked.pop(task)
        a.current_task = task
        # Picked up while still somewhat frustrated: give it a real chance before the body
        # forces it down again (otherwise the next minute's tick would park it at once).
        a.set_aside_floor = a.needs["frustration"] + self.S("frustration.chosen_resume_margin")
        return self.log.append("resume", a.id, self.clock.now(), task=task,
                               resume_note=p["resume_note"],
                               parked_minutes=round((self.clock.now() - p["since"]) / 60, 1),
                               chosen=True)

    def _check_resume(self, a: Agent) -> None:
        if a.needs["frustration"] >= self.S("frustration.resume_below"):
            return
        for task in list(a.parked):
            p = a.parked.pop(task)
            self.log.append("resume", a.id, self.clock.now(), task=task,
                            resume_note=p["resume_note"],
                            parked_minutes=round((self.clock.now() - p["since"]) / 60, 1))

    def parked_tasks(self, agent_id: str) -> set[str]:
        return set(self.agents[agent_id].parked)

    # ------------------------------------------------------------- feelings
    def feelings(self, agent_id: str, window_min: float = 60) -> list[dict[str, Any]]:
        """Feeling phrases generated only from logged causes. No evidence, no phrase."""
        return _feelings.feelings(self, self.agents[agent_id], window_min)
