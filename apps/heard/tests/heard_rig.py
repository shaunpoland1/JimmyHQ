"""What Heard's tests share: a Heard app on the SDK's FakeCore, with a clock the test turns
and a spy on every call the app makes of the platform. No JimmyHQ, no network. Every name,
place and sentence is invented: Sam, Alex, Room A, "the plan".
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
REPO = APP_ROOT.parents[1]
for path in (APP_ROOT, REPO / "sdk" / "python"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from heard import config  # noqa: E402
from heard.program import build  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
UTC = ZoneInfo("UTC")


def ms(text: str, zone: ZoneInfo = UTC) -> int:
    """A local time such as ``"2027-01-15 08:00"`` as unix milliseconds."""
    return int(datetime.fromisoformat(text).replace(tzinfo=zone).timestamp() * 1000)


START = ms("2027-01-15 08:00")  # a fixed "now"

# Invented speech, with sentences a test can quote.
PLAN = "I will send Sam the notes about the garden plan tomorrow."
CHAT = [
    {"text": "Good morning, did you see the garden plan?", "speaker": "SPEAKER_01",
     "speaker_id": 1, "is_user": False, "start": 0.0, "end": 3.5},
    {"text": PLAN, "speaker": "SPEAKER_00", "speaker_id": 0, "is_user": True,
     "start": 4.0, "end": 8.0},
]


class Clock:
    def __init__(self, now: int = START):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, by: int) -> None:
        self.now += by


def conversation(**more) -> dict:
    """A finished conversation as Omi's webhook sends it."""
    body = {
        "id": "c-100", "started_at": "2027-01-15T07:30:00+00:00",
        "finished_at": "2027-01-15T07:31:00+00:00", "discarded": False,
        "structured": {"title": "Garden plan", "overview": "Talk about the garden.",
                       "category": "personal", "emoji": "x", "action_items": []},
        "transcript_segments": CHAT,
    }
    body.update(more)
    return body


class Rig:
    """One Heard app on a fake core, with a clock the test turns. ``rig.calls`` is the name
    of every call the app made of the platform, in order, whether or not it was allowed."""

    def __init__(self, tmp_path: Path, *, settings: dict | None = None, timezone: str = "UTC",
                 local_model: bool = True, private: bool = True,
                 permissions: list | None = None):
        values = config.defaults()
        values.update(settings or {})
        self.data = tmp_path / "data"
        granted = ["jobs", "inbound", "model.ask", "tasks.write"] \
            if permissions is None else permissions
        self.core = FakeCore("heard", data_dir=self.data, settings=values, timezone=timezone,
                             permissions=granted, private=private, inbound_arrays=["live"])
        self.core.local_model = local_model
        self.calls: list[str] = []
        real = self.core._call

        def spy(name, args):
            self.calls.append(name)
            return real(name, args)

        self.core._call = spy  # type: ignore[method-assign]
        self.clock = Clock()
        self.app = build(transport=self.core, clock=self.clock)
        self.app.run()  # with a fake core this only registers
        self.ctx = self.app.heard

    def send(self, body: dict | list, *, key_name: str = "Pendant", name: str = "transcript",
             query: dict | None = None) -> None:
        """A message from the recorder, as it would arrive now, to one of the inbound names
        (``transcript`` unless said), with the parameters the sender put after ``?``."""
        self.core.inbound(name, body, key_name=key_name, received_ms=self.clock.now,
                          query=query)

    def call(self, name: str, /, **args):
        return self.core.call(name, args)

    def first_id(self) -> int:
        return self.call("days")["conversations"][0]["id"]

    def wherever_it_could_be_written(self) -> str:
        """Everything the app wrote anywhere but its own store, as one string."""
        return repr([self.core.events, self.core.cards, self.core.emitted, self.core.tasks,
                     self.core.vault, self.core.storage, self.core.fetched])

    def answer(self, *items: dict) -> None:
        """What the model will say to the next question: a to-do reply."""
        self.core.replies.append(json.dumps({"items": list(items)}))

    def settle(self, start: str, check: str, conv_id: int) -> dict:
        """Ask the page's way: ``start``, then ``check`` every moment until the model has
        answered (the question runs on a thread)."""
        started = self.call(start, id=conv_id)
        if started["state"] != "running":
            return started
        for _ in range(500):
            got = self.call(check, id=conv_id)
            if got["state"] != "running":
                return got
            time.sleep(0.01)
        raise AssertionError("the model never answered")

    def summarise(self, conv_id: int) -> dict:
        return self.settle("summarise", "summarise.check", conv_id)

    def todos(self, conv_id: int) -> dict:
        return self.settle("todos.ask", "todos.check", conv_id)
