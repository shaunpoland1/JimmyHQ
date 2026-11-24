"""What Reader writes on the owner's record.

Ids and short words only, except ``article.offered``: that is the kind an offer to other
apps is printed as, and Reader's feed ``articles`` is this kind read back (the manifest's
``feeds``; another app reads it with ``feed:reader.articles``). Its shape is the same as
an ``offer`` row:

    {offer, article, url, title, topic, why, text_ref, when_ms}

``text_ref`` names the article's numbered text inside Reader (``reader:article:<id>``),
for Reader's own use; no other app can read it. A consumer reads the source itself, as
Posts always does.

``offer.withdrawn`` says an offer was taken back, and is Reader's feed ``withdrawn``:

    {offer, article, url, when_ms}

``offer`` and ``url`` are the ones the offer was printed with, so a consumer can find
what it took and drop it.
"""

from __future__ import annotations

from typing import Literal

from jimmyhq_app import App, Payload


class ArticleSuggested(Payload):
    article: int
    topic: int | None
    by: Literal["plain", "researcher"]


class ArticleDecided(Payload):
    article: int
    decision: Literal["keep", "later", "skip", "send", "mine"]


class FactMined(Payload):
    fact: int
    article: int


class ArticleOffered(Payload):
    offer: int
    article: int
    url: str
    title: str
    topic: str
    why: str
    text_ref: str
    when_ms: int


class OfferWithdrawn(Payload):
    offer: int
    article: int
    url: str
    when_ms: int


OFFER_EXAMPLE = {"offer": 1, "article": 12, "url": "https://news.example.com/2027/soil",
                 "title": "Soil that holds water", "topic": "Garden soil",
                 "why": "Garden soil: a new field trial with numbers.",
                 "text_ref": "reader:article:12", "when_ms": 1800000000000}


WITHDRAWN_EXAMPLE = {"offer": 1, "article": 12, "url": "https://news.example.com/2027/soil",
                     "when_ms": 1800000060000}


def declare(app: App) -> None:
    app.kind("article.suggested", example={"article": 1, "topic": 1,
                                           "by": "plain"})(ArticleSuggested)
    app.kind("article.decided", example={"article": 1, "decision": "keep"})(ArticleDecided)
    app.kind("fact.mined", example={"fact": 1, "article": 1})(FactMined)
    app.kind("article.offered", example=OFFER_EXAMPLE)(ArticleOffered)
    app.kind("offer.withdrawn", example=WITHDRAWN_EXAMPLE)(OfferWithdrawn)
