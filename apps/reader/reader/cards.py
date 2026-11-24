"""The cards Reader puts on the owner's Bench, and what it does with each decision.

- ``article``: an article to read, with its why-line, its source and an excerpt, as text.
  Keep, Read later, Skip, Send on. Each closes the card, and each is the owner's vote.
- ``notice``: something the owner should know (a setting not taken, a late researcher).

At most ``cards_per_day`` article cards a day; past it the article waits in Triage.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError, Decision

from reader.context import Ctx
from reader.sources import excerpt

log = logging.getLogger("reader")

MAX_BODY = 6000

KEEP = {"id": "keep", "label": "Keep", "style": "primary", "closes": True}
LATER = {"id": "later", "label": "Read later", "closes": True}
SKIP = {"id": "skip", "label": "Skip", "closes": True}
SEND = {"id": "send", "label": "Send on", "closes": True}
GOT_IT = {"id": "ok", "label": "Got it", "style": "primary", "closes": True}
ARTICLE_ACTIONS = [KEEP, LATER, SKIP, SEND]


def withdraw(ctx: Ctx, card_id: str | None, note: str = "Decided in Reader.") -> None:
    if not card_id:
        return
    try:
        ctx.app.bench.withdraw(card_id, note=note)
    except CallError as error:
        log.info("could not withdraw card %s: %s", card_id, error.message)


def cards_today(ctx: Ctx) -> int:
    today = ctx.today()
    return sum(1 for r in ctx.store.all("SELECT card_filed FROM article WHERE card_filed "
                                        "IS NOT NULL") if ctx.day(r["card_filed"]) == today)


def cards_left(ctx: Ctx) -> int:
    """Rule ``cards_a_day``: how many more article cards may go on the Bench today."""
    return max(0, int(ctx.cfg().cards_per_day) - cards_today(ctx))


def card_body(ctx: Ctx, article: dict[str, Any]) -> str:
    lines = [article["why"], f"Source: {article['host'] or article['url']}"]
    shown = excerpt(article["sentences"], int(ctx.cfg().excerpt_sentences))
    if shown:
        lines += ["", shown]
    lines += ["", article["final_url"] or article["url"]]
    return "\n".join(lines)[:MAX_BODY]


def file_article(ctx: Ctx, article: dict[str, Any]) -> str | None:
    """Put one article on the Bench, if today has room. Returns the card's id, or None
    with the reason kept on the article."""
    if article.get("card_id"):
        return article["card_id"]
    if cards_left(ctx) <= 0:
        ctx.store.set_article(article["id"], note="Today's Bench cards are used; it waits "
                              "in Triage.")
        return None
    title = article["title"] or article["host"] or "An article"
    try:
        card_id = ctx.app.bench.file("article", f"To read: {title}"[:200],
                                     body=card_body(ctx, article),
                                     subject=f"article:{article['id']}",
                                     actions=ARTICLE_ACTIONS, summary=article["why"][:300])
    except CallError as error:
        ctx.store.set_article(article["id"], note=f"It could not go on your Bench: "
                              f"{error.message} It waits in Triage.")
        return None
    ctx.store.set_article(article["id"], card_id=card_id, card_filed=ctx.now())
    return card_id


def notice(ctx: Ctx, title: str, body: str, *, subject: str | None = None,
           actions: list[dict[str, Any]] | None = None) -> str | None:
    try:
        return ctx.app.bench.file("notice", title[:200], body=body, subject=subject,
                                  actions=actions or [GOT_IT])
    except CallError as error:
        log.warning("could not tell the owner %r: %s", title, error.message)
        return None


def article_of(subject: str | None) -> int | None:
    if subject and subject.startswith("article:") and subject[8:].isdigit():
        return int(subject[8:])
    return None


def on_article(ctx: Ctx, decision: Decision) -> str:
    """The owner decided an article card."""
    from reader import articles  # noqa: PLC0415

    article = ctx.store.article(article_of(decision.subject))
    if article is None or article.get("card_id") != decision.card_id:
        return "That card is no longer the article's current one; nothing was done."
    if decision.action not in ("keep", "later", "skip", "send"):
        return f"Reader does not know the action {decision.action!r}; nothing was done."
    return articles.choose(ctx, article["id"], decision.action, from_card=True)["state"]
