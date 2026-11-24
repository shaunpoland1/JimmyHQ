"""What Posts' tests share: a Posts app on the SDK's FakeCore, with a clock the test
turns. No JimmyHQ, no network. (Named so it cannot be mistaken for the repository's own
test helpers when both folders are collected in one run.)

Every app here is made with ``transport=core``, never through ``FakeCore.installed()``,
so nothing is left registered between tests (on Windows they all share one process).
Sample data uses example.com and invented text only.
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

from jimmyhq_app.testing import FakeCore  # noqa: E402
from posts import config  # noqa: E402
from posts.program import build  # noqa: E402

START = 1_800_000_000_000  # a fixed "now" in unix ms (2027-01-15 08:00 UTC)
ARTICLE = "https://news.example.com/2027/solar-roofs"

ARTICLE_TEXT = (
    "# Solar roofs spread across the valley\n\n"
    "The valley council said on Monday that 1,240 homes added solar panels last year. "
    "That is 38% more than the year before. "
    "Officials expect the total to pass 5,000 homes by 2030.\n\n"
    "\"We have never seen demand like this,\" said the council's energy officer. "
    "Installers report waiting lists of up to 14 weeks. "
    "The average system now costs 7,900 dollars after the grant. "
    "Most owners say the panels paid for themselves in under nine years.\n\n"
    "Critics argue the grant favours larger houses. "
    "The council will review the scheme in the spring, and a report is due in May."
)


def word_vector(text: str) -> list[float]:
    """A stand-in embedding: counts of each word, folded into 97 places by a fixed sum,
    so texts that share words point the same way and unrelated texts do not."""
    vector = [0.0] * 97
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vector[sum(ord(ch) * (n + 1) for n, ch in enumerate(word)) % 97] += 1.0
    return vector


class Clock:
    def __init__(self, now: int = START):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


class Rig:
    """One Posts app on a fake core, with a clock the test turns."""

    def __init__(self, tmp_path: Path, *, settings: dict | None = None,
                 schedulers: dict | None = None, timezone: str = "UTC",
                 roles: dict | None = None, secrets: dict | None = None,
                 reads_reader: bool = False, reads_withdrawn: bool | None = None):
        values = config.defaults()
        values.update(settings or {})
        granted = ["jobs", "bench.file", "model.ask", "web", "net:socialbu.com",
                   "net:api.buffer.com"]
        if reads_reader:  # the owner let Posts read the articles Reader offers
            granted.append("feed:reader.articles")
        if reads_reader if reads_withdrawn is None else reads_withdrawn:
            granted.append("feed:reader.withdrawn")  # and what Reader took back
        if roles is not None:  # agents may hold Posts' roles: {"writer": "Wren", ...}
            granted.append("agents.tools")
        self.core = FakeCore("posts", data_dir=tmp_path, settings=values, timezone=timezone,
                             permissions=granted, cards=["draft", "overlap", "notice"],
                             roles=roles, secrets=secrets)
        self.core.embedder = word_vector
        self.clock = Clock()
        self.app = build(transport=self.core, clock=self.clock, schedulers=schedulers)
        self.app.run()  # with a fake core this only registers
        self.ctx = self.app.posts

    @property
    def store(self):
        return self.ctx.store

    def page(self, **given) -> dict:
        return self.core.call("pages.save", {"name": "Garden notes", "register": "Warm, "
                                             "plain and practical.", "hours": ["08:00"],
                                             **given})

    def link(self, page: dict, url: str = ARTICLE, text: str = ARTICLE_TEXT, **more) -> dict:
        self.core.pages[url] = text
        return self.core.call("new.link", {"page_id": page["id"], "url": url, **more})

    def through(self, page: dict, body: str, *, url: str = ARTICLE, text: str = ARTICLE_TEXT,
                claims=None, points=None, **more) -> dict:
        """A piece from a link to its draft card: read, overlap, draft, check."""
        piece = self.link(page, url, text, **more)
        self.core.replies += [draft_reply(body, claims=claims), ship(points)]
        self.advance(4)
        return self.store.piece(piece["id"])

    def advance(self, times: int = 1) -> list:
        """Run the job, a second apart, as the worker would."""
        said = []
        for _ in range(times):
            self.clock.advance(1000)
            said.append(self.core.run_job("advance"))
        return said

    def cards_of(self, kind: str) -> list[dict]:
        return [c for c in self.core.cards.values() if c["kind"] == f"app.posts.{kind}"
                and not c.get("withdrawn") and not c.get("decided")]


def draft_reply(body: str, *, title: str = "Solar roofs", claims=None, links=None,
                gloss: str = "Drafted from the article.") -> dict:
    return {"title": title, "body": body, "claims": claims if claims is not None else [],
            "links": links if links is not None else [1], "gloss": gloss}


def ship(points=None) -> dict:
    return {"verdict": "ship", "points": points or [], "gloss": "Nothing to raise."}
