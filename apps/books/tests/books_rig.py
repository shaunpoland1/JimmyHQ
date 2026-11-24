"""What Books' tests share: a Books app on the SDK's FakeCore, with a clock the test turns.
No JimmyHQ, no network. (Named so it cannot be mistaken for another folder's helpers when
every folder is collected in one run.)

Every name and amount here is invented: Example Bank, Sam, the Corner Shop.
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

from books import config  # noqa: E402
from books.program import build  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

START = 1_800_000_000_000  # a fixed "now" in unix ms: 2027-01-15 08:00 UTC
TODAY = "2027-01-15"
DAY = 24 * 60 * 60 * 1000


class Clock:
    def __init__(self, now: int = START):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


class Rig:
    """One Books app on a fake core. ``rig.asked`` lists every call the app made to the
    core, by name, so a test can say what was never asked."""

    def __init__(self, tmp_path: Path, *, settings: dict | None = None,
                 timezone: str = "UTC"):
        values = config.defaults()
        values.update(settings or {})
        self.core = FakeCore("books", data_dir=tmp_path, settings=values, timezone=timezone,
                             permissions=["jobs", "bench.file"], cards=["due"],
                             card_subjects=["reminder"], private=True)
        self.asked: list[str] = []
        real = self.core._call

        def spy(name, args):
            self.asked.append(name)
            return real(name, args)
        self.core._call = spy  # type: ignore[method-assign]
        self.clock = Clock()
        self.app = build(transport=self.core, clock=self.clock)
        self.app.run()  # with a fake core this only registers
        self.ctx = self.app.books

    @property
    def store(self):
        return self.ctx.store

    def call(self, name: str, args: dict | None = None):
        return self.core.call(name, args or {})

    def account(self, name: str = "Example Bank", kind: str = "checking",
                opening: str = "1,000.00", opening_date: str = "2027-01-01") -> int:
        return self.call("account.add", {"name": name, "kind": kind, "opening": opening,
                                         "opening_date": opening_date})["id"]

    def add(self, account: int, date: str, payee: str = "Corner Shop", *,
            payment: str = "", deposit: str = "", category: str = "", memo: str = "") -> int:
        return self.call("txn.add", {"account": account, "date": date, "payee": payee,
                                     "payment": payment, "deposit": deposit,
                                     "category": category, "memo": memo})["id"]

    def rows(self, account: int, **more) -> list[dict]:
        return self.call("register", {"account": account, **more})["rows"]

    def balances(self, account: int) -> list[tuple[str, str]]:
        """(payee, balance) oldest first."""
        return [(r["payee"], r["balance"]) for r in reversed(self.rows(account))]

    def cards(self) -> list[dict]:
        return [c for c in self.core.cards.values() if not c.get("withdrawn")]

    def upload(self, text: str) -> str:
        token = self.call("upload.start")["token"]
        step = 200_000
        for i in range(max(1, -(-len(text) // step))):
            self.call("upload.part", {"token": token, "part": i,
                                      "text": text[i * step:(i + 1) * step]})
        return token
