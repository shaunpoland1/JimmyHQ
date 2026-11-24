"""What Packages' tests share: a Packages app on the SDK's FakeCore, with a clock the test
turns and a stand-in for 17TRACK. No JimmyHQ, no network. (Named so it cannot be mistaken
for another folder's test helpers when every folder is collected in one run.)

Every app here is made with ``transport=core``, never through ``FakeCore.installed()``,
so nothing is left registered between tests. Every tracking number and label is invented.
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

from jimmyhq_app.testing import FakeCore  # noqa: E402
from packages import config  # noqa: E402
from packages.program import build  # noqa: E402
from packages.trackers.seventeentrack import HOST  # noqa: E402
from packages_service import KEY, Service  # noqa: E402

START = 1_800_000_000_000  # a fixed "now" in unix ms (2027-01-15 08:00 UTC)
MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR

HOSE = "ZX100200300QQ"  # invented numbers
RAKE = "ZX100200301QQ"
SEEDS = "ZX100200302QQ"


class Clock:
    def __init__(self, now: int = START):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


class Rig:
    """One Packages app on a fake core, with a clock the test turns. By default the owner
    chose 17TRACK and typed a key, and ``rig.service`` plays 17TRACK."""

    def __init__(self, tmp_path: Path, *, settings: dict | None = None,
                 tracker: str = "seventeentrack", timezone: str = "UTC",
                 secrets: dict | None = None, permissions: list | None = None,
                 trackers: dict | None = None):
        values = config.defaults()
        values["tracker"] = tracker
        values.update(settings or {})
        granted = permissions if permissions is not None else [
            "jobs", "bench.file", f"net:{HOST}"]
        self.core = FakeCore(
            "packages", data_dir=tmp_path, settings=values, timezone=timezone,
            permissions=granted, cards=["update"], card_subjects=["package"], private=True,
            secrets={"seventeentrack_key": KEY} if secrets is None else secrets)
        self.clock = Clock()
        self.slept: list[float] = []
        self.app = build(transport=self.core, clock=self.clock, sleep=self.slept.append,
                         trackers=trackers)
        self.app.run()  # with a fake core this only registers
        self.ctx = self.app.packages
        self.service = Service(self.core)

    @property
    def store(self):
        return self.ctx.store

    def add(self, label: str = "Garden hose", number: str = HOSE, **more) -> dict:
        return self.core.call("package.add", {"label": label, "number": number, **more})

    def package(self, package_id: int) -> dict:
        return self.store.package(package_id)

    def sync(self) -> str:
        return self.core.run_job("sync")

    def cards(self) -> list[dict]:
        return [c for c in self.core.cards.values() if not c.get("withdrawn")]

    def wherever_it_could_be_written(self) -> str:
        """Everything the app wrote anywhere but its own store: the record, the cards, the
        nudges, the settings the owner sees, as one string."""
        return repr([self.core.events, self.core.cards, self.core.emitted,
                     self.core.tasks, self.core.vault, self.core.storage])
