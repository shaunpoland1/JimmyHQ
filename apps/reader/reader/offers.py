"""Articles offered to other apps.

An offered article is kept as a record of a fixed shape (the ``offer`` table) and
printed once on the owner's record as ``article.offered`` (``kinds``). Those events are
Reader's feed ``articles``: an app the owner lets read it (``feed:reader.articles``)
takes them from there. Nothing is sent anywhere by Reader itself, and no article's text
goes with an offer.

An offer can be taken back (``withdraw``). The row stays, marked with when, and
``offer.withdrawn`` is printed: Reader's second feed, ``withdrawn``, is that kind read
back, so an app the owner lets read it (``feed:reader.withdrawn``) can drop what it took.
Nothing printed earlier is rewritten. Offering the article again clears the mark and
prints ``article.offered`` again, with the same offer number.
"""

from __future__ import annotations

from typing import Any

from reader.context import Ctx


def text_ref(article_id: int) -> str:
    return f"reader:article:{article_id}"


def shape(row: dict[str, Any]) -> dict[str, Any]:
    """An offer as other apps will read it."""
    return {"offer": row["id"], "article": row["article_id"], "url": row["url"],
            "title": row["title"], "topic": row["topic"], "why": row["why"],
            "text_ref": row["text_ref"], "when_ms": row["created"]}


def of_article(ctx: Ctx, article_id: int) -> dict[str, Any] | None:
    """The offer standing for this article now, or None (never offered, or taken back)."""
    return ctx.store.one("SELECT * FROM offer WHERE article_id=? AND withdrawn IS NULL",
                         (article_id,))


def make(ctx: Ctx, article: dict[str, Any]) -> dict[str, Any]:
    """Offer one article. Offering it again changes nothing and returns the first offer,
    unless that one was taken back: then it is offered afresh under the same number."""
    store = ctx.store
    earlier = store.one("SELECT * FROM offer WHERE article_id=?", (article["id"],))
    if earlier is not None and earlier["withdrawn"] is None:
        return shape(earlier)
    topic = store.topic(article.get("topic_id")) or {}
    row = {"article_id": article["id"], "url": article["final_url"] or article["url"],
           "title": (article["title"] or article["host"] or article["url"])[:300],
           "topic": str(topic.get("name") or ""), "why": article["why"][:500],
           "text_ref": text_ref(article["id"]), "created": ctx.now()}
    if earlier is None:
        offer_id = store.insert("offer", row)
    else:
        offer_id = earlier["id"]
        store.update("offer", offer_id, {**row, "withdrawn": None})
    store.set_article(article["id"], offered=1)
    made = shape({**row, "id": offer_id})
    ctx.tape("article.offered", made, subject=f"article:{article['id']}")
    return made


def withdraw(ctx: Ctx, article_id: int) -> dict[str, Any] | None:
    """Take back the offer of one article. Returns what was printed, or None when no
    offer of it stands (nothing is changed and nothing is printed)."""
    store = ctx.store
    standing = of_article(ctx, article_id)
    if standing is None:
        return None
    now = ctx.now()
    store.update("offer", standing["id"], {"withdrawn": now})
    store.set_article(article_id, offered=0)
    said = {"offer": standing["id"], "article": article_id, "url": standing["url"],
            "when_ms": now}
    ctx.tape("offer.withdrawn", said, subject=f"article:{article_id}")
    return said


def listed(ctx: Ctx, most: int) -> list[dict[str, Any]]:
    """The offers that stand, newest first. One taken back is not listed."""
    return [shape(r) for r in ctx.store.all(
        "SELECT * FROM offer WHERE withdrawn IS NULL ORDER BY created DESC, id DESC LIMIT ?",
        (most,))]
