"""Mining: facts from articles, offered to the owner's Memory through the platform's
memory source plug (source ``facts``).

Two switches, both off to begin with: Reader's own setting ``mine_facts``, and the
source's switch in Memory, which the platform keeps off until the owner turns it on.
While ``mine_facts`` is off nothing is mined, by anyone, and the source hands Memory
nothing.

A fact is a list of sentence numbers in one article (``facts_from``). What Memory gets is
those sentences, word for word from Reader's own reading of the article, with the article
as their source: never a model's or an agent's words. A note on why it is worth keeping
stays in Reader.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError, Item

from reader.context import Ctx
from reader.sources import numbered

log = logging.getLogger("reader")

MAX_NOTE = 300
MAX_FACT_SENTENCES = 4  # one fact is a sentence or a few in a row, not a page

FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {"sentences": {"type": "array", "items": {"type": "integer"}},
                       "note": {"type": "string"}},
        "required": ["sentences"]}}},
    "required": ["facts"],
}

OWNER_ASKED, TOPIC_ASKS = 1, 2  # article.mine_wanted: who asked for it

MINING_OFF = ("Mining is off in Reader's settings, so nothing was kept for Memory. The "
              "owner switches it on under Reader's settings, \"Mine articles into Memory\".")


class MiningError(ValueError):
    """Nothing was mined. The message says why and what to do."""


def mining_on(ctx: Ctx) -> bool:
    """Rule ``mining_is_opt_in``: the owner has switched mining on."""
    return bool(ctx.cfg().mine_facts)


def facts_from(sentences: list[str], numbers: list[int]) -> tuple[list[int], list[int]]:
    """Rule ``facts_are_the_articles_own_sentences``: the numbers that name a sentence the
    article has (in order, without repeats), and those set aside."""
    kept: list[int] = []
    aside: list[int] = []
    for n in numbers:
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(sentences):
            aside.append(n)
        elif n not in kept:
            kept.append(n)
    return sorted(kept), aside


def record(ctx: Ctx, article: dict[str, Any], numbers: list[int], note: str = "", *,
           by: str, agent: str | None = None) -> dict[str, Any]:
    """Keep one fact. Writes only Reader's own records and its receipt; Memory collects
    it later."""
    if not mining_on(ctx):
        raise MiningError(MINING_OFF)
    if not article.get("sentences"):
        raise MiningError(f"Article {article['id']} has no text kept any more, so nothing was "
                          "mined. Mine from an article Reader still holds.")
    kept, aside = facts_from(article["sentences"], numbers)
    if not kept:
        raise MiningError(f"None of those numbers is a sentence of article {article['id']}, "
                          f"which has {len(article['sentences'])}. Nothing was mined. Give "
                          "the numbers read shows.")
    if len(kept) > MAX_FACT_SENTENCES:
        raise MiningError(f"One fact is at most {MAX_FACT_SENTENCES} sentences; you gave "
                          f"{len(kept)}. Nothing was mined. Keep each fact on its own.")
    store = ctx.store
    have = store.all("SELECT sentences FROM fact WHERE article_id=?", (article["id"],))
    if any(r["sentences"] == kept for r in have):
        return {"fact": None, "sentences": kept, "aside": aside, "again": True}
    if len(have) >= int(ctx.cfg().facts_per_article):
        raise MiningError(f"Article {article['id']} has given its "
                          f"{int(ctx.cfg().facts_per_article)} facts already. Nothing more "
                          "was mined.")
    text = " ".join(article["sentences"][n - 1] for n in kept)
    fact_id = store.insert("fact", {"article_id": article["id"], "sentences": kept,
                                    "text": text, "note": " ".join(str(note).split())[:MAX_NOTE],
                                    "by": by, "agent": agent, "created": ctx.now()})
    store.set_article(article["id"], mined=article.get("mined", 0) + 1)
    ctx.tape("fact.mined", {"fact": fact_id, "article": article["id"]},
             subject=f"article:{article['id']}")
    return {"fact": fact_id, "sentences": kept, "aside": aside, "again": False}


def mine_queued(ctx: Ctx) -> list[str]:
    """Articles waiting to be mined (the owner pressed m, or a topic mines its articles):
    one model call each picks sentences by number. Run by the ``advance`` job."""
    store, cfg, said = ctx.store, ctx.cfg(), []
    for article in store.all("SELECT * FROM article WHERE mine_wanted > 0 ORDER BY id"):
        by = "owner" if article["mine_wanted"] == OWNER_ASKED else "plain"
        if not mining_on(ctx):
            store.set_article(article["id"], mine_wanted=0, note=MINING_OFF)
            continue
        if not article["sentences"]:
            store.set_article(article["id"], mine_wanted=0,
                              note="Its text was deleted, so nothing was mined.")
            continue
        try:
            reply = ctx.app.model.ask(
                "Choose the facts in this article worth remembering, at most "
                f"{int(cfg.facts_per_article)}. Each fact is one sentence, or a few in a "
                "row, named by the numbers in brackets. Add a short note on why each is "
                "worth keeping. Choose only from the numbered sentences.",
                role=str(cfg.role_mine), json_schema=FACTS_SCHEMA,
                untrusted=[numbered(article["sentences"])])
        except CallError as error:
            store.set_article(article["id"], mine_wanted=0,
                              note=f"Could not choose facts: {error.message} Press m to try "
                              "again.")
            continue
        facts = (reply.data or {}).get("facts") if isinstance(reply.data, dict) else None
        count = 0
        for fact in (facts or [])[:int(cfg.facts_per_article)]:
            if not isinstance(fact, dict):
                continue
            numbers = [n for n in fact.get("sentences") or [] if isinstance(n, int)]
            try:
                if record(ctx, store.article(article["id"]) or article, numbers,
                          str(fact.get("note") or ""), by=by)["fact"]:
                    count += 1
            except MiningError as error:
                log.info("a fact from article %s was set aside: %s", article["id"], error)
        store.set_article(article["id"], mine_wanted=0,
                          note=f"Mined {count} fact{'s' if count != 1 else ''} for Memory.")
        said.append(f"Mined {count} from article {article['id']}.")
    return said


def collect(ctx: Ctx, cursor: str | None, limit: int) -> tuple[list[Item], str | None]:
    """The memory source ``facts``: facts after ``cursor``, oldest first. Nothing while
    mining is off."""
    if not mining_on(ctx):
        return [], cursor
    after = int(cursor) if cursor and cursor.isdigit() else 0
    store, items = ctx.store, []
    rows = store.all("SELECT * FROM fact WHERE id > ? ORDER BY id LIMIT ?",
                     (after, max(1, limit)))
    for fact in rows:
        article = store.article(fact["article_id"]) or {}
        url = article.get("final_url") or article.get("url") or ""
        title = article.get("title") or article.get("host") or "an article"
        items.append(Item(ref=f"fact:{fact['id']}", when_ms=fact["created"],
                          who="owner" if fact["by"] == "owner" else "app",
                          text=f"{fact['text']}\n(From \"{title}\", {url})", where=url))
    return items, str(rows[-1]["id"]) if rows else cursor
