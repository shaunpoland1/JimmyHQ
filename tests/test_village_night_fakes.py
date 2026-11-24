"""Stand-ins for the parts of the village other packages own: the runtime object the
beat loop passes around, and a model that answers from a script."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jimmyhq import village
from jimmyhq.village import lens
from jimmyhq.village.world import SimClock, World, temperament

AGENT = "agent-a"
START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


class FakeVillage:
    """What the runtime gives a tool or the night: the core, the world, the log, the
    clock and where an agent's folders are."""

    def __init__(self, core: Any, start: datetime = START):
        self.core = core
        self.log = village.log(core)
        self.clock = SimClock(start, tz="UTC")
        self.world = World(village.setting_getter(core), self.log, self.clock)
        self.shared_dir = Path(core.config.data) / "village" / "shared"
        self.configs: dict[str, dict] = {}
        self.woken: list[str] = []

    def agent_dir(self, agent_id: str) -> Path:
        return Path(self.core.config.data) / "village" / "agents" / agent_id

    def cfg(self, agent_id: str) -> dict:
        return self.configs.get(agent_id) or lens.config(self.core, agent_id)

    def owner_name(self) -> str:
        return self.core.settings.get("owner.name") or "the owner"

    def tz(self) -> str:
        return "UTC"

    def wake(self, agent_id: str) -> None:
        self.woken.append(agent_id)

    # --- test conveniences ---

    def add(self, agent_id: str = AGENT, seed: int = 1):
        body = self.world.add_agent(agent_id, temperament.roll(random.Random(seed)))
        self.agent_dir(agent_id).mkdir(parents=True, exist_ok=True)
        return body

    def event(self, kind: str, agent_id: str | None = AGENT, minutes: float = 1, **data):
        """Log an event ``minutes`` after the last one."""
        self.clock.advance(minutes)
        return self.log.append(kind, agent_id, self.clock.now(), **data)

    def owner_says(self, text: str, agent_id: str = AGENT, **kw):
        return self.event("chat", agent_id, sender="owner", text=text, **kw)

    def acts(self, tool: str = "write_file", ok: bool = True, agent_id: str = AGENT, **kw):
        return self.event("action", agent_id, tool=tool, args=kw.pop("args", {"path": "a.md"}),
                          ok=ok, result=kw.pop("result", "done"), **kw)

    def sleeps(self, agent_id: str = AGENT, kind: str = "night"):
        body = self.world.agents[agent_id]
        body.asleep, body.sleep_kind = True, kind
        return self.event("sleep", agent_id, sleep_kind=kind)


class ScriptedBrain:
    """A model that answers from a list. A dict is a JSON answer, a string plain text,
    an exception is raised. Every call's options are kept in ``calls``."""

    def __init__(self, *replies: Any):
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, **options):
        self.calls.append({"messages": messages, **options})
        if not self.replies:
            raise AssertionError("the model was asked more often than the script allows")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, dict):
            return SimpleNamespace(data=reply, text=json.dumps(reply))
        return SimpleNamespace(data=None, text=str(reply))


def use_brain(monkeypatch, *replies: Any) -> ScriptedBrain:
    from jimmyhq.village.memory import consolidate
    fake = ScriptedBrain(*replies)
    monkeypatch.setattr(consolidate, "brain", lambda core: fake)
    return fake
