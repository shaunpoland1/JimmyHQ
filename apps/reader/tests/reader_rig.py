"""What Reader's tests share: a Reader app on the SDK's FakeCore, with a clock the test
turns. No JimmyHQ, no network. (Named so it cannot be mistaken for another folder's test
helpers when every folder is collected in one run.)

Every app here is made with ``transport=core``, never through ``FakeCore.installed()``,
so nothing is left registered between tests (on Windows they all share one process).
Sample text is invented and every address is on example.com.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
REPO = APP_ROOT.parents[1]
for path in (APP_ROOT, REPO / "sdk" / "python"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from jimmyhq_app import CallError  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402
from reader import config  # noqa: E402
from reader.program import build  # noqa: E402

START = 1_800_000_000_000  # a fixed "now" in unix ms (2027-01-15 08:00 UTC)
HOUR = 3_600_000
DAY = 24 * HOUR

SOIL = "https://news.example.com/2027/soil-water"
SOIL_TEXT = (
    "# Soil that holds water through a dry summer\n\n"
    "A three-year field trial in the valley found that beds mulched with wood chips held "
    "41% more water in August than bare beds. "
    "The trial covered 120 plots on four farms. "
    "Researchers measured moisture every week at two depths.\n\n"
    "The mulched beds also needed watering half as often. "
    "Yields of beans and squash were the same in both kinds of bed. "
    "The team says the chips should be at least 8 centimetres deep to work.\n\n"
    "Growers in the trial said the chips took a season to settle. "
    "A report with the full figures is due in the spring."
)
BEES = "https://garden.example.com/bees-in-winter"
BEES_TEXT = (
    "# Where bees go in winter\n\n"
    "Most wild bees spend the winter alone, underground or inside hollow stems. "
    "Only honey bees keep a warm cluster through the cold months. "
    "A survey of 300 gardens counted more nesting bees where old stems were left standing "
    "until late spring.\n\n"
    "Cutting everything back in autumn removes the places where next year's bees are "
    "sleeping. "
    "Leaving a patch of bare soil helps the bees that nest in the ground. "
    "Gardeners in the survey who left stems saw more flowers visited the next year."
)
TOMATO = "https://blog.example.com/tomato-blight"
TOMATO_TEXT = (
    "# Spotting blight early on tomatoes\n\n"
    "Late blight shows first as dark patches on the lower leaves after warm, wet nights. "
    "Removing the lowest leaves keeps splashing soil off the plant. "
    "Spacing plants 60 centimetres apart lets the leaves dry faster after rain. "
    "Watering at the base in the morning helps for the same reason.\n\n"
    "Resistant varieties are now widely sold, and most do as well as older ones. "
    "Blighted plants should be bagged, not composted."
)


def word_vector(text: str) -> list[float]:
    """A stand-in embedding: counts of each word, folded into 97 places by a fixed sum,
    so texts that share words point the same way and unrelated texts do not."""
    vector = [0.0] * 97
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vector[sum(ord(ch) * (n + 1) for n, ch in enumerate(word)) % 97] += 1.0
    return vector


def no_embeddings(text: str) -> list[float]:
    raise CallError("unavailable", "You have not chosen a model for the embeddings role.")


class Clock:
    def __init__(self, now: int = START):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


class Rig:
    """One Reader app on a fake core, with a clock the test turns."""

    def __init__(self, tmp_path: Path, *, settings: dict | None = None,
                 timezone: str = "UTC", roles: dict | None = None, reads_posts: bool = False):
        values = config.defaults()
        values.update(settings or {})
        granted = ["jobs", "bench.file", "model.ask", "web", "memory.source"]
        if reads_posts:  # the owner let Reader read Posts' feed of what went out
            granted.append("feed:posts.posted")
        if roles is not None:  # an agent may hold the researcher role
            granted.append("agents.tools")
        self.core = FakeCore("reader", data_dir=tmp_path, settings=values, timezone=timezone,
                             permissions=granted, cards=["article", "notice"], roles=roles)
        self.core.embedder = word_vector
        self.clock = Clock()
        self.app = build(transport=self.core, clock=self.clock)
        self.app.run()  # with a fake core this only registers
        self.ctx = self.app.reader

    @property
    def store(self):
        return self.ctx.store

    def topic(self, **given) -> dict:
        return self.core.call("topics.save", {"name": "Garden soil",
                                              "phrasings": ["soil water mulch"],
                                              **given})

    def results(self, query: str, *pages: tuple[str, str], title: str = "") -> None:
        """What a search for ``query`` finds: each (address, text) is a page to read."""
        self.core.search_results[query] = [
            {"url": url, "title": title or text.split("\n", 1)[0].lstrip("# "),
             "snippet": text[:80]} for url, text in pages]
        for url, text in pages:
            self.core.pages[url] = text

    def pick(self, *numbers: int, why: str = "Worth reading.") -> None:
        self.core.replies.append({"picks": [{"number": n, "why": why} for n in numbers]})

    def search(self) -> str:
        return self.core.run_job("search")

    def advance(self) -> str:
        self.clock.advance(60_000)
        return self.core.run_job("advance")

    def cards_of(self, kind: str = "article") -> list[dict]:
        return [c for c in self.core.cards.values() if c["kind"] == f"app.reader.{kind}"
                and not c.get("withdrawn") and not c.get("decided")]

    def articles(self, state: str | None = None) -> list[dict]:
        if state:
            return self.store.all("SELECT * FROM article WHERE state=? ORDER BY id", (state,))
        return self.store.all("SELECT * FROM article ORDER BY id")
