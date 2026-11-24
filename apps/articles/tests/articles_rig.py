"""What Articles' tests share: an Articles app on the SDK's FakeCore, with a clock the test
turns, invented sources on example.com, and model answers in the shapes asked for. No
JimmyHQ, no network. (Named so it cannot be mistaken for another folder's helpers when
every folder is collected in one run.)

Every app here is made with ``transport=core``, never through ``FakeCore.installed()``,
and every database is closed after the test (on Windows all tests share one process).
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

from articles import config  # noqa: E402
from articles.program import build  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

# What the manifest declares, so the fake plays it: the owner may give Articles his own site.
OWNER_HOSTS = __import__("json").loads(config.MANIFEST.read_text(encoding="utf-8"))[
    "owner_hosts"]
START = 1_800_000_000_000  # a fixed "now" in unix ms (2027-01-15 08:00 UTC)

SURVEY_URL = "https://survey.example.com/2026/garden-plots"
SURVEY = [
    "A city survey of 1,200 households found that families with a garden plot spent 18% "
    "less on vegetables.",
    "The survey ran from March to October in 2026.",
    "Dr. Rivera, who led the survey, said: “Most plots paid for their seeds within six "
    "weeks.”",
    "Gardeners grew an average of 41 kilograms of produce on each plot.",
    "The council funds 340 plots across nine sites.",
    "Waiting lists for a plot grew by 25% in a year.",
    "A plot costs 30 dollars a season to rent.",
    "The survey also found that 12% of plots were left unused by August.",
    "Older gardeners reported the largest savings.",
    "The full figures are in the council's annual report.",
]
NEWS_URL = "https://news.example.com/garden-plots-save-money"
NEWS = SURVEY[:8] + ["Local growers welcomed the findings.",
                     "The council meets again in the spring."]
SOIL_URL = "https://soil.example.com/compost-and-yields"
SOIL = [
    "Plots that were mulched with compost held more water through the summer.",
    "A trial on 60 plots found yields rose by 9% with compost.",
    "The trial was run by the Valley Growers Network over two seasons.",
    "Compost made on site cost almost nothing beyond labour.",
    "Gardeners said the main cost was their time.",
    "Some plots on clay soil saw no change at all.",
    "The network plans a third season with more plots.",
    "Its report recommends testing soil before adding compost.",
]
COOK_URL = "https://kitchen.example.com/cooking-from-the-plot"
COOK = [
    "Families who grow vegetables cook at home more often, a kitchen study found.",
    "In the study, 64 families kept a food diary for eight weeks.",
    "Those with a plot ate vegetables on five days a week on average.",
    "Families without one ate them on three days.",
    "The researchers cautioned that the families chose to take part.",
    "Cooking classes at two garden sites were full every week.",
    "Most families said they wasted less food.",
    "The study did not measure spending.",
]
FORUM_URL = "https://forum.example.com/thread/88"
FORUM = [
    "Someone on here said gardens save you a fortune.",
    "I tried one last year and it was hard work.",
    "My neighbour says she saved hundreds.",
    "Not sure the numbers add up honestly.",
    "Anyone have real figures on this one.",
    "I would like to see a proper study.",
]

PAGES = {SURVEY_URL: SURVEY, NEWS_URL: NEWS, SOIL_URL: SOIL, COOK_URL: COOK,
         FORUM_URL: FORUM}
TITLES = {SURVEY_URL: "Garden plots cut food bills", NEWS_URL: "Garden plots save money",
          SOIL_URL: "Compost and yields", COOK_URL: "Cooking from the plot",
          FORUM_URL: "Do gardens save money?"}


def text_of(sentences: list[str]) -> str:
    return " ".join(sentences)


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


# The settings every test starts from: small sources count, so the invented pages do.
TEST_SETTINGS = {"min_source_chars": 200, "substantial_sentences": 8, "min_sources": 3,
                 "primary_hosts": ["survey.example.com"], "weak_hosts": ["forum.example.com"]}


class Rig:
    """One Articles app on a fake core, with a clock the test turns."""

    def __init__(self, tmp_path: Path, *, settings: dict | None = None,
                 publishers: dict | None = None, permissions: list[str] | None = None,
                 secrets: dict | None = None, timezone: str = "UTC",
                 roles: dict | None = None):
        values = config.defaults()
        values.update(TEST_SETTINGS)
        values.update(settings or {})
        granted = permissions if permissions is not None else [
            "jobs", "bench.file", "model.ask", "web"] + (["agents.tools"] if roles else [])
        self.core = FakeCore("articles", data_dir=tmp_path, settings=values, timezone=timezone,
                             permissions=granted, cards=["article", "coverage", "notice"],
                             secrets=secrets, roles=roles, owner_hosts=OWNER_HOSTS)
        self.core.embedder = word_vector
        self.clock = Clock()
        self.app = build(transport=self.core, clock=self.clock, publishers=publishers)
        self.app.run()  # with a fake core this only registers
        self.ctx = self.app.articles
        self.data_dir = Path(tmp_path)

    def close(self) -> None:
        self.ctx.close()

    @property
    def store(self):
        return self.ctx.store

    def site(self, **given) -> dict:
        return self.core.call("sites.save", {"name": "Garden notes", "audience": "People new "
                                             "to growing food", "register": "Warm and plain.",
                                             "length": "about 900 words", **given})

    def web(self, urls: list[str] | None = None, pages: dict | None = None) -> None:
        """Every search finds ``urls`` (all the invented pages by default)."""
        found = urls if urls is not None else list(PAGES)
        self.core.search_results["*"] = [{"title": TITLES.get(u, ""), "url": u,
                                          "snippet": ""} for u in found]
        for url, sentences in (pages or PAGES).items():
            self.core.pages[url] = text_of(sentences)

    def topic(self, site: dict, topic: str = "Do community garden plots save families "
              "money?", **more) -> dict:
        return self.core.call("new.topic", {"site_id": site["id"], "topic": topic, **more})

    def advance(self, times: int = 1) -> list:
        """Run the job, a second apart, as the worker would."""
        said = []
        for _ in range(times):
            self.clock.advance(1000)
            said.append(self.core.run_job("advance"))
        return said

    def to_card(self, site: dict | None = None, *, body: str | None = None,
                review: dict | None = None, **more) -> dict:
        """A topic from start to its Bench card: coverage, search, read, summary, outline,
        article, review (nothing found unless ``review`` says)."""
        site = site or self.site()
        self.web()
        piece = self.topic(site, **more)
        self.core.replies += [phrasings(), summary(), outline(),
                              article(body if body is not None else GOOD_BODY),
                              review if review is not None else no_findings()]
        self.advance(7)
        return self.store.piece(piece["id"])

    def cards_of(self, kind: str) -> list[dict]:
        return [c for c in self.core.cards.values() if c["kind"] == f"app.articles.{kind}"
                and not c.get("withdrawn") and not c.get("decided")]


# --- model answers, in the shapes Articles asks for -----------------------------------------


def phrasings(*found: str) -> dict:
    return {"phrasings": list(found) or ["community garden plot savings survey",
                                         "garden plot food costs study"]}


def summary(statements: list | None = None) -> dict:
    return {"statements": statements if statements is not None else [
        {"text": "Plot holders spent 18% less on vegetables.",
         "cites": [{"source": 1, "sentence": 1}]},
        {"text": "Compost raised yields by 9% in a trial.",
         "cites": [{"source": 2, "sentence": 2}]}], "gloss": "Summarised."}


def outline(title: str = "What a garden plot really saves") -> dict:
    return {"title": title, "sections": [{"heading": "The savings", "points": ["18% less"]},
                                         {"heading": "The catch", "points": ["unused plots"]}],
            "gloss": "Outlined."}


def article(body: str, title: str = "What a garden plot really saves") -> dict:
    return {"title": title, "body": body, "gloss": "Wrote it."}


def no_findings() -> dict:
    return {"findings": []}


def finding(quote: str, kind: str = "unsupported", cites=((1, 1),),
            problem: str = "The cited sentence does not say this.") -> dict:
    return {"kind": kind, "quote": quote,
            "cites": [{"source": s, "sentence": n} for s, n in cites], "problem": problem}


# Sources rank as S1 survey (primary), S2 soil, S3 cooking, S4 forum (weak); the news page
# is the same story as the survey and is left out.
GOOD_BODY = (
    "<p>Families with a garden plot spent 18% less on vegetables, a survey of 1,200 "
    "households found [S1:1].</p>"
    "<h2>What the plots give</h2>"
    "<p>Each plot grew 41 kilograms of produce on average [S1:4]. Compost helped too: a "
    "trial on 60 plots saw yields rise by 9% [S2:2].</p>"
    "<p>Not every plot is used, though: 12% were left empty by August [S1:8].</p>")
