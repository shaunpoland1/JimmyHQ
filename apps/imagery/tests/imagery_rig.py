"""What Imagery's tests share: an Imagery app on the SDK's FakeCore, with a clock the test
turns, no worker thread (the test runs the queue itself) and a stand-in for the image
services. No JimmyHQ, no network. Every description is invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
REPO = APP_ROOT.parents[1]
for path in (APP_ROOT, REPO / "sdk" / "python"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from imagery import config  # noqa: E402
from imagery.program import build  # noqa: E402
from imagery.providers.gemini import BASE  # noqa: E402
from imagery_service import GEMINI_HOST, KEY, OWN_HOST, Service  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

START = 1_800_000_000_000  # a fixed "now" in unix ms (2027-01-15 08:00 UTC)
HOUR = 3_600_000
DAY = 24 * HOUR
MODEL = config.defaults()["gemini_model"]
GEMINI_URL = f"{BASE}/models/{MODEL}:generateContent"
OWN_BASE = f"https://{OWN_HOST}/v1"


class Clock:
    def __init__(self, now: int = START):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


class Rig:
    """One Imagery app on a fake core. By default the owner chose Practice. Pass
    ``provider="gemini"`` to have Gemini with a key typed in, or ``"openai_compatible"``
    to have a server of their own granted with a key."""

    def __init__(self, tmp_path: Path, *, settings: dict | None = None, provider: str = "practice",
                 timezone: str = "UTC", secrets: dict | None = None,
                 permissions: list | None = None):
        values = config.defaults()
        values["provider"] = provider
        if provider == "openai_compatible":
            values["openai_base"] = OWN_BASE
        values.update(settings or {})
        granted = permissions if permissions is not None else [
            "agents.tools", f"net:{GEMINI_HOST}"]
        self.core = FakeCore(
            "imagery", data_dir=tmp_path, settings=values, timezone=timezone,
            permissions=granted,
            secrets={"gemini_key": KEY} if secrets is None else secrets,
            owner_hosts={"purpose": "Reach your own image server.", "max": 2})
        if provider == "openai_compatible":
            self.core.grant_host(OWN_HOST, secrets={"compatible_key": KEY})
        self.clock = Clock()
        self.app = build(transport=self.core, clock=self.clock, threaded=False)
        self.app.run()  # with a fake core this only registers
        self.ctx = self.app.imagery
        self.service = Service(self.core)

    @property
    def store(self):
        return self.ctx.store

    def make(self, prompt: str = "A red kite over a hill", *, aspect: str = "1:1",
             count: int = 1, **more) -> dict:
        """The owner presses Make; the worker then does the run."""
        started = self.core.call("make", {"prompt": prompt, "aspect": aspect, "count": count,
                                          **more})
        self.ctx.maker.drain()
        return started

    def pictures(self, **args) -> list[dict]:
        return self.core.call("pictures.list", args)["rows"]

    def overview(self) -> dict:
        return self.core.call("overview", {})

    def last_run(self) -> dict:
        return self.store.all("SELECT * FROM run ORDER BY id DESC LIMIT 1")[0]

    def files(self) -> list[Path]:
        folder = Path(self.core.data_dir) / "uploads"
        return sorted(p for p in folder.glob("*") if p.is_file()) if folder.is_dir() else []

    def wherever_it_could_be_written(self) -> str:
        """Everything the app wrote anywhere but its own store: the record, the cards, the
        nudges, the settings the owner sees, as one string."""
        return repr([self.core.events, self.core.cards, self.core.emitted, self.core.tasks,
                     self.core.vault, self.core.storage])
