"""The running village: one world, its agents' folders, and the seams to the rest.

Exactly one process builds a ``Village`` (``jimmyhq village``), because the bodies live
in its memory. Everything an agent's beat needs hangs off it: the world, the event log,
the clock, where an agent's files are, its configuration, and who the owner is.

Data, under ``<data>/village/``:

    agents/<id>/state.json   the body, rewritten after every beat
    agents/<id>/home/        its home; its own files are in ``files`` there
    shared/<id>/             what it shares with coworkers
    world.json               when the world last moved

The other parts of the village (tools, talk, memory) are reached by name through
``Village.seam`` and imported only when used, so the beat runs, and can be tested,
without them.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jimmyhq import village as _village
from jimmyhq.village.rules import Refusal, rule
from jimmyhq.village.settings import full_key
from jimmyhq.village.world import World
from jimmyhq.village.world.agent import Agent
from jimmyhq.village.world.events import Event
from jimmyhq.village.world.state import dump_agent, load_agent, load_json, save_json

log = logging.getLogger("jimmyhq.village")

STEP_MIN = 5.0
PAUSE_AFTER_MIN = 15.0  # a longer gap means the village was not running: a pause, not lived time
# Kept with the body in state.json although the world's own save does not know them.
EXTRA_STATE = ("set_aside_floor", "call_until", "waited_answered")


def display_name(agent_id: str) -> str:
    return agent_id.replace("-", " ").title()


def from_ms(ts_ms: Any) -> float:
    """A stored time (unix milliseconds) as the world's clock counts it (seconds). The
    world, the log and everything an agent's view reads are in seconds; tables, files
    and Tape payloads are in milliseconds, and are converted here, by name."""
    return float(ts_ms or 0) / 1000


class VillageWorld(World):
    """The world, with what the runtime adds around it. The rules of the body are the
    world's own and unchanged; these are the cases it could not see by itself."""

    def __init__(self, get: Callable[[str], Any], log: Any, clock: Any):
        super().__init__(get, log, clock)
        # Whether the owner's urgent message to this agent is still unanswered.
        self.call_open: Callable[[str], bool] = lambda agent_id: False
        self.call_awake_min: Callable[[], float] = lambda: 60.0
        self._answered = 0.0

    def S(self, key: str) -> Any:  # noqa: N802
        value = super().S(key)
        if key == "frustration.wait_cap_total" and self._answered:
            return max(0.0, value - self._answered)
        return value

    # An urgent message must keep the agent up, not only get it up: woken inside the
    # sleep window, the very next minute would put it back to bed unanswered.
    @rule("call_keeps_awake", "village.beat.call_awake_minutes")
    def held_awake(self, a: Agent) -> bool:
        until = getattr(a, "call_until", None)
        return bool(until and self.clock.now() < until and self.call_open(a.id))

    def wake_for_call(self, agent_id: str, call: Event) -> Event | None:
        self.agents[agent_id].call_until = self.clock.now() + self.call_awake_min() * 60
        return super().wake_for_call(agent_id, call)

    def _maybe_sleep(self, a: Agent, h: float, ev: list[str]) -> None:
        if self.held_awake(a) and a.needs["energy"] > self.S("sleep.forced_sleep_energy"):
            return
        super()._maybe_sleep(a, h, ev)

    @rule("resume_needs_parked")
    def resume_by_choice(self, agent_id: str, task: str) -> Event:
        if task not in self.agents[agent_id].parked:
            raise Refusal("resume_needs_parked",
                          f"You have not set {task} aside, so nothing was picked back up. "
                          "To work on it, use focus_on with its reference from your list.",
                          frustrate=False)
        return super().resume_by_choice(agent_id, task)

    # The cap on frustration from waiting is for the day's waiting as a whole. Counted
    # only over what is still waiting, every answer would make room for more.
    @rule("waiting_cap_remembers", "village.frustration.wait_cap_total")
    def answered_today(self, a: Agent) -> float:
        kept = getattr(a, "waited_answered", None) or {}
        today = self.clock.local_time().date().isoformat()
        return float(kept.get("caused", 0.0)) if kept.get("day") == today else 0.0

    def respond(self, agent_id: str, item: str, verdict: str,
                acknowledged: bool = True) -> Event:
        a = self.agents[agent_id]
        caused = float((a.waiting.get(item) or {}).get("caused", 0.0))
        if caused:
            a.waited_answered = {"day": self.clock.local_time().date().isoformat(),
                                 "caused": self.answered_today(a) + caused}
        return super().respond(agent_id, item, verdict, acknowledged)

    def _advance_agent(self, a: Agent, m: float) -> None:
        self._answered = self.answered_today(a)
        try:
            super()._advance_agent(a, m)
        finally:
            self._answered = 0.0


@rule("floor_saved")
def dump_state(a: Agent) -> dict[str, Any]:
    """The body as saved. The margin a chosen task was given is part of it: lost in a
    restart, the next tick would set that task aside again."""
    d = dump_agent(a)
    for key in EXTRA_STATE:
        d[key] = getattr(a, key, None)
    return d


def load_state(d: dict[str, Any]) -> Agent:
    a = load_agent(d)
    for key in EXTRA_STATE:
        setattr(a, key, d.get(key))
    return a


@dataclass
class BeatState:
    """What one agent's loop remembers between beats. None of it is the body."""

    heartbeat: float = 60.0
    last_result: tuple[str, str] | None = None  # (tool, whole answer) to show next beat
    rest_heard: str | None = None  # the owner message last answered from rest
    handled_set_asides: set[str] | None = None
    busy: bool = False  # a beat is running: commands for this agent wait
    trace: list[str] = field(default_factory=list)  # the stages of the last beat, in order
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class Village:
    def __init__(self, core: Any, *, clock: Any = None, log: Any = None,
                 fakes: dict[str, Any] | None = None, only_fakes: bool = False):
        self.core = core
        self.fakes = dict(fakes or {})
        self.only_fakes = only_fakes
        self.log = log if log is not None else _village.log(core)
        self.clock = clock if clock is not None else _village.clock(core)
        self.world = VillageWorld(_village.setting_getter(core), self.log, self.clock)
        self.world.standing = self._standing
        self.world.owner_channel_open = lambda: True  # every agent can write to the owner
        self.world.call_open = self._call_open
        self.world.call_awake_min = lambda: self.beat_s("call_awake_minutes")
        self.data = Path(core.config.data) / "village"
        self.shared_dir = self.data / "shared"
        self.beats: dict[str, BeatState] = {}
        self.last_tick: float | None = None
        self._cfg: dict[str, dict[str, Any]] = {}
        self._born: dict[str, float] = {}

    # ------------------------------------------------------------- settings
    def S(self, short: str) -> Any:  # noqa: N802
        """A world setting by its short key, such as ``social.full_at``."""
        return self.core.settings.get(full_key(short))

    def beat_s(self, name: str) -> Any:
        return self.core.settings.get("village.beat." + name)

    def ctx_s(self, name: str) -> Any:
        return self.core.settings.get("village.context." + name)

    def owner_name(self) -> str:
        return str(self.core.settings.get("owner.name") or "").strip() or "the owner"

    def tz(self) -> str:
        return str(self.core.settings.get("owner.timezone") or "")

    def when(self, ts: float) -> datetime:
        """A world time (seconds) on the owner's clock. Display only."""
        tz = getattr(self.clock, "tz", None) or self.tz()
        if tz:
            return datetime.fromtimestamp(ts, ZoneInfo(tz))
        return datetime.fromtimestamp(ts, UTC).astimezone()

    # --------------------------------------------------------------- agents
    def agent_dir(self, agent_id: str) -> Path:
        return self.data / "agents" / agent_id

    def files_dir(self, agent_id: str) -> Path:
        return self.agent_dir(agent_id) / "home" / "files"

    def state(self, agent_id: str) -> BeatState:
        if agent_id not in self.beats:
            self.beats[agent_id] = BeatState()
        return self.beats[agent_id]

    def wake(self, agent_id: str) -> None:
        """End this agent's wait between beats now."""
        self.state(agent_id).wake.set()

    def name(self, agent_id: str) -> str:
        store = self.seam("memory.store")
        locked = store.MemoryStore(self.agent_dir(agent_id)).locked_name() if store else None
        return locked or display_name(agent_id)

    def born_at(self, agent_id: str) -> float:
        if agent_id not in self._born:
            born = self.log.query(agent=agent_id, kinds=["born"])
            if not born:
                return self.clock.now()
            self._born[agent_id] = born[0].ts
        return self._born[agent_id]

    def cfg(self, agent_id: str) -> dict[str, Any]:
        """The agent's configuration: its role, the models for its beat and its night,
        whether it may only use models on this computer, and its capabilities."""
        if agent_id not in self._cfg:
            found: dict[str, Any] = {}
            try:
                from jimmyhq.village import lens
                found = lens.config(self.core, agent_id)
            except Exception:  # noqa: BLE001 - an unreadable record must not stop a beat
                log.exception("could not read the configuration of %s", agent_id)
            self.set_cfg(agent_id, found)
        return self._cfg[agent_id]

    def forget_cfg(self, agent_id: str) -> None:
        """The configuration changed on the Tape: read it again when next asked."""
        self._cfg.pop(agent_id, None)

    def set_cfg(self, agent_id: str, payload: dict[str, Any]) -> None:
        base = {"agent_id": agent_id, "role": "", "beat": {"role": "default"},
                "night": {"role": "smart"}, "local_only": False, "capabilities": []}
        self._cfg[agent_id] = base | {k: v for k, v in payload.items() if v is not None}

    def _standing(self, agent_id: str) -> set[str]:
        """What does not keep a tired agent up: its standing priorities, which never
        finish. A one-off task still does. The list knows which refs those are."""
        try:
            return set(self.ask("comms.tasklist", "standing", agent_id, default=()) or ())
        except Exception:  # noqa: BLE001 - a bad list must not stop the clock
            return set()

    def _call_open(self, agent_id: str) -> bool:
        try:
            return bool(self.ask("comms.chat", "open_call", agent_id))
        except Exception:  # noqa: BLE001 - the clock must keep going
            return False

    # ---------------------------------------------------------------- seams
    def seam(self, name: str) -> Any:
        """Another part of the village, by its module name under ``jimmyhq.village``
        (``comms.chat``), or None when it is not there."""
        if name in self.fakes:
            return self.fakes[name]
        if self.only_fakes:
            return None
        full = f"jimmyhq.village.{name}"
        try:
            return importlib.import_module(full)
        except ModuleNotFoundError as e:
            if e.name and (full == e.name or full.startswith(e.name + ".")):
                return None
            raise

    def ask(self, module: str, fn: str, *args: Any, default: Any = None, **kw: Any) -> Any:
        """Call ``fn(self, *args)`` in a seam, or return ``default`` when the seam, or
        the function, is not there."""
        f = getattr(self.seam(module), fn, None)
        return default if f is None else f(self, *args, **kw)

    def tail(self, agent_id: str | None, kinds: list[str], n: int,
             since: float | None = None) -> list[Event]:
        """The newest ``n`` matching events, oldest first."""
        try:
            return self.log.query(agent=agent_id, kinds=kinds, since=since, limit=n)
        except TypeError:  # a log without ``limit`` (the in-memory one)
            return self.log.query(agent=agent_id, kinds=kinds, since=since)[-n:]

    def tools(self) -> list[Any]:
        return list(getattr(self.seam("tools"), "ALL", []) or [])

    def brain(self) -> Any:
        if "brain" in self.fakes:
            return self.fakes["brain"]
        from jimmyhq.models import brain
        return brain(self.core)

    def close(self) -> None:
        """Let go of what this village opened in ``village.db``."""
        self.ask("comms.benchlink", "close")

    # ----------------------------------------------------------- load, save
    def load(self) -> list[str]:
        """Put back every body saved under ``agents/``. Returns who is here."""
        root = self.data / "agents"
        for folder in sorted(root.iterdir()) if root.exists() else []:
            if folder.name in self.world.agents:
                continue
            try:
                saved = load_json(folder / "state.json")
                if saved:
                    self.world.restore_agent(load_state(saved))
            except Exception:  # noqa: BLE001 - one unreadable body must not keep the rest out
                log.exception("could not load the saved state of %s", folder.name)
        for agent_id in list(self.world.agents):
            try:  # what each body waits on is whatever the cards table says is open
                self.ask("comms.benchlink", "rebuild_waiting", agent_id)
            except Exception:  # noqa: BLE001 - a bad table must not keep the village down
                log.exception("could not rebuild what %s is waiting on", agent_id)
        meta = load_json(self.data / "world.json") or {}
        if self.last_tick is None and meta.get("last_tick_ms"):
            self.last_tick = meta["last_tick_ms"] / 1000
        return list(self.world.agents)

    def save(self, agent_id: str | None = None) -> None:
        for aid in ([agent_id] if agent_id else list(self.world.agents)):
            if aid in self.world.agents:
                save_json(self.agent_dir(aid) / "state.json", dump_state(self.world.agents[aid]))

    # ----------------------------------------------------------------- time
    def tick(self) -> None:
        """Bring every body up to now. A long gap is logged as a pause: the bodies did
        not live that time."""
        now = self.clock.now()
        if self.last_tick is None:
            self.last_tick = now
        gap = (now - self.last_tick) / 60
        if gap > PAUSE_AFTER_MIN:
            for aid in self.world.agents:
                self.log.append("world_paused", aid, now, minutes=round(gap, 1),
                                note="the village was not running; the body did not live "
                                     "this time")
        else:
            remaining, t0 = gap, self.last_tick
            while remaining > 1e-6:
                step = min(STEP_MIN, remaining)
                t0 += step * 60
                if hasattr(self.clock, "set"):
                    self.clock.set(t0)
                self.world.advance(step)
                remaining -= step
        self.last_tick = now
        self.save()
        save_json(self.data / "world.json", {"last_tick_ms": round(now * 1000)})
