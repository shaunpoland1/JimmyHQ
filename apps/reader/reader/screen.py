"""What Reader's own page may ask (``hq.app.call``). Each call returns plain JSON.

Article text is sent as plain strings, sentence by sentence; the page shows every one of
them as text, never as HTML.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from reader import articles, cards, finding, mining, offers, posted, sources, suggest, topics, work
from reader.context import Ctx
from reader.store import SUGGESTED

LIST_FIELDS = ("id", "url", "final_url", "host", "title", "topic_id", "phrasing", "why", "by",
               "state", "said", "mined", "offered", "note", "created", "suggested",
               "decided", "text_deleted", "audience")


def _int(args: dict[str, Any], key: str = "id") -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Say which one with \"{key}\". Nothing was changed.") from None


def row_of(ctx: Ctx, article: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    out = {k: article[k] for k in LIST_FIELDS}
    out["topic"] = names.get(article["topic_id"] or 0, "")
    out["excerpt"] = sources.excerpt(article["sentences"], int(ctx.cfg().excerpt_sentences))
    out["has_text"] = bool(article["sentences"])
    out["on_bench"] = bool(article["card_id"])
    out["posted_close"] = posted.note_of(ctx, article["id"])
    return out


def waiting(ctx: Ctx) -> int:
    """How many things wait on the owner in Reader: the articles in triage, as many as the
    page lists (``screen_rows``). This is the number beside Reader in the sidebar, said by
    the program after everything it does and also by the page while it is open."""
    return int(ctx.store.one("SELECT COUNT(*) AS n FROM (SELECT 1 FROM article WHERE "
                             "state='new' LIMIT ?)", (int(ctx.cfg().screen_rows),))["n"])


def overview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, cfg = ctx.store, ctx.cfg()
    found = store.topics()
    names = {t["id"]: t["name"] for t in found}
    held = work.holder(ctx) is not None
    triage = store.all("SELECT * FROM article WHERE state='new' ORDER BY suggested, id "
                       "LIMIT ?", (int(cfg.screen_rows),))
    return {
        "topics": [{**t, "health": topics.health(ctx, t),
                    "covered_by": "plain" if work.plain_covers(ctx, t, held) else "researcher"}
                   for t in found],
        "hint": "" if found else topics.NO_TOPICS,
        "triage": [row_of(ctx, a, names) for a in triage],
        "waiting": waiting(ctx),
        "researcher": work.status(ctx),
        "mining_on": mining.mining_on(ctx),
        "limits": {"searches_left": finding.searches_left(ctx),
                   "cards_left": cards.cards_left(ctx),
                   "quiet_days": int(cfg.quiet_days), "keep_text_days": int(cfg.keep_text_days),
                   "default_outcome": str(cfg.default_outcome)},
        "offered": store.one("SELECT COUNT(*) AS n FROM offer WHERE withdrawn IS NULL")["n"],
        "keys_on": bool(cfg.single_keys),
        "posted_feed": posted.status(ctx),
    }


def article_list(ctx: Ctx, args: dict[str, Any]) -> list[dict[str, Any]]:
    names = {t["id"]: t["name"] for t in ctx.store.topics()}
    rows = ctx.store.all("SELECT * FROM article WHERE state != 'read' ORDER BY "
                         "COALESCE(suggested, created) DESC, id DESC LIMIT ?",
                         (int(ctx.cfg().screen_rows),))
    return [row_of(ctx, a, names) for a in rows]


def article_get(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    article = ctx.store.article(_int(args))
    if article is None or article["state"] == "read":
        raise ValueError("There is no such article.")
    names = {t["id"]: t["name"] for t in ctx.store.topics()}
    facts = ctx.store.all("SELECT id, sentences, note, by, created FROM fact WHERE "
                          "article_id=? ORDER BY id", (article["id"],))
    return {**row_of(ctx, article, names), "sentences": article["sentences"], "facts": facts}


def choose(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    done = articles.choose(ctx, _int(args), str(args.get("action") or ""))
    names = {t["id"]: t["name"] for t in ctx.store.topics()}
    return {**row_of(ctx, done, names), "offer_change": done.get("offer_change", "")}


def withdraw(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Take back the offer of the article ``id``."""
    done = articles.take_back(ctx, _int(args))
    names = {t["id"]: t["name"] for t in ctx.store.topics()}
    return {**row_of(ctx, done, names), "back_in_triage": bool(done.get("back_in_triage"))}


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {
        "overview": lambda a: overview(ctx, a),
        "articles.list": lambda a: article_list(ctx, a),
        "article.get": lambda a: article_get(ctx, a),
        "article.choose": lambda a: choose(ctx, a),
        "topics.save": lambda a: {**(t := topics.save(ctx, a)), "health": topics.health(ctx, t)},
        "topics.suggest": lambda a: suggest.suggest(ctx, a),
        "topics.suggest_names": lambda a: suggest.suggest_names(ctx, a),
        "topics.pause": lambda a: topics.pause(ctx, _int(a), bool(a.get("paused", True))),
        "topics.remove": lambda a: topics.remove(ctx, _int(a)),
        "offer.withdraw": lambda a: withdraw(ctx, a),
        "offered.list": lambda a: offers.listed(ctx, int(ctx.cfg().screen_rows)),
        "round.choose": lambda a: work.from_page(ctx, _int(a), str(a.get("action") or "")),
    }


__all__ = ["SUGGESTED", "calls"]
