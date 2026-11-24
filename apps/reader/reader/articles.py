"""One path for every article, whoever found it: read, have-I-seen-this, suggest, carry
out the topic's outcomes, and the owner's choice.

- ``take(ctx, url, ...)`` reads an address once. The same address is never read twice;
  an unreadable or too short page is kept only as a dropped row with its sentence, and
  nothing more (no model, no embedding) is spent on it.
- ``suggest(ctx, article, ...)`` runs have-I-seen-this again against everything
  suggested so far, then marks it suggested with its why-line. It only writes, so the
  researcher's ``record`` tool and the plain job use the same function.
- ``carry_out(ctx)`` (the ``advance`` job) does what each new suggestion's topic says:
  a Bench card, mining, an offer to other apps.
- ``choose(ctx, article, action)`` is the owner's Keep, Read later, Skip, Send on or
  Mine, from the Bench or Reader's screen: the votes that topic health counts.
- ``take_back(ctx, article)`` takes back the offer of an article to other apps. One the
  owner sent on waits in Triage again, as if Send on had not been pressed.
"""

from __future__ import annotations

import re
from typing import Any

from reader import cards, mining, offers, similar, sources, topics
from reader.context import Ctx
from reader.store import SUGGESTED

DAY_MS = 86_400_000
MAX_WHY = 300

STATE_OF = {"keep": "kept", "later": "later", "skip": "skipped", "send": "sent"}


class ArticleError(ValueError):
    """Nothing was done. The message says why and what to do."""


def take(ctx: Ctx, url: str, *, topic_id: int | None, phrasing: str = "", by: str,
         agent: str | None = None, title: str = "") -> tuple[dict[str, Any], bool]:
    """The article at ``url``, and whether it is new: the row already there for that
    address (False), or a new one (True), read now (``state`` read) or dropped with its
    sentence (``state`` dropped)."""
    url = sources.check_url(url)
    earlier = similar.same_address(ctx, url)
    if earlier is not None:
        return earlier, False
    cfg, store, now = ctx.cfg(), ctx.store, ctx.now()
    row: dict[str, Any] = {"url": url, "host": sources.host_of(url), "topic_id": topic_id,
                           "phrasing": phrasing[:200], "by": by, "agent": agent,
                           "created": now, "updated": now}
    try:
        source = sources.read(ctx.app, url, min_chars=int(cfg.min_chars),
                              keep_chars=int(cfg.keep_chars))
    except sources.SourceProblem as problem:
        return store.article(store.insert("article", {**row, "state": "dropped",
                                                      "said": str(problem)})) or {}, True
    if source.final_url != url:
        moved = similar.same_address(ctx, source.final_url)
        if moved is not None:
            return moved, False
    title = title_of(source, title)
    made = similar.fingerprint(ctx, title, source.sentences)
    return store.article(store.insert("article", {
        **row, "state": "read", "final_url": source.final_url,
        "host": sources.host_of(source.final_url) or row["host"], "title": title,
        "text": source.text, "sentences": source.sentences, "vector": made.vector,
        "vector_model": made.model, "runs": made.runs,
        "seen": {"note": made.note} if made.note else {}})) or {}, True


def title_of(source: sources.Source, given: str = "") -> str:
    """The page's own title, else the search result's, else a short first line."""
    for said in (source.title, given):
        said = " ".join(str(said or "").split())
        if said:
            return said[:300]
    first = source.sentences[0] if source.sentences else ""
    return first[:120] if len(first) <= 160 else first[:117].rstrip() + "..."


def print_of(article: dict[str, Any]) -> similar.Print:
    return similar.Print(article.get("vector"), article.get("vector_model"),
                         article.get("runs") or [],
                         str((article.get("seen") or {}).get("note") or ""))


_ADDRESS = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


def no_addresses(text: str) -> str:
    """Rule ``no_link_from_a_model``: an address written in a ranker's or researcher's
    words is taken out. The only address an article carries is the one Reader read."""
    return _ADDRESS.sub("(address taken out)", text)


def why_line(topic: dict[str, Any] | None, article: dict[str, Any], reason: str, by: str,
             audience: str) -> str:
    """The one line an article carries: which topic, which phrasing, whose reason, and
    for whom. Written by the code; only ``reason`` is the ranker's or researcher's."""
    name = (topic or {}).get("name") or "No topic"
    where = f" (search “{article['phrasing']}”)" if article.get("phrasing") else ""
    who = "Researcher" if by == "researcher" else "Ranker"
    reason = no_addresses(" ".join(str(reason).split()))[:MAX_WHY]
    line = f"{name}{where}. {who}: {reason}" if reason else f"{name}{where}."
    if audience and audience != "you":
        line += f" For {audience[:120]}."
    return line


def suggest(ctx: Ctx, article_id: int, *, topic_id: int | None, reason: str, by: str,
            audience: str = "", agent: str | None = None) -> dict[str, Any]:
    """Suggest an article Reader has read. Returns the row: ``state`` new when it was
    suggested, or dropped with ``said`` when have-I-seen-this stopped it."""
    store = ctx.store
    article = store.article(article_id)
    if article is None or article["state"] == "dropped" and not article["sentences"]:
        raise ArticleError(f"Reader has no readable article {article_id}. Nothing was "
                           "suggested. Read it first, and use the number read gives.")
    if article["state"] in SUGGESTED:
        raise ArticleError(f"Article {article_id} was suggested already, "
                           f"{similar.describe(similar.Seen('same_address', article_id), store)}"
                           " Nothing was suggested again.")
    if not article["sentences"]:
        raise ArticleError(f"Article {article_id}'s text was deleted, so it cannot be "
                           "suggested. Nothing was done. Read the address again.")
    found = similar.seen(ctx, print_=print_of(article), skip=article_id)
    if found.stops:
        said = similar.describe(found, store)
        return store.set_article(article_id, state="dropped", seen=found.to_dict(),
                                 said=f"{said} It was not suggested again.")
    topic = store.topic(topic_id)
    audience = topics.audience_line(topic, audience)
    line = why_line(topic, article, reason, by, audience)
    if found.status == "close":
        line += f" {similar.describe(found, store)}"
    return store.set_article(article_id, state="new", topic_id=topic_id, why=line[:600],
                             audience=audience, by=by, agent=agent if by == "researcher"
                             else article["agent"], seen=found.to_dict(), pending=1,
                             suggested=ctx.now())


def pass_over(ctx: Ctx, article_id: int, said: str) -> None:
    """Read and looked at, and not picked. Its text goes at once: it was never
    suggested, so nobody will read it."""
    ctx.store.set_article(article_id, state="passed", said=said, text="", sentences=[],
                          text_deleted=ctx.now())


# --- carrying out a topic's outcomes --------------------------------------------------------


def carry_out(ctx: Ctx) -> list[str]:
    """For each new suggestion: a Bench card, mining, an offer, as its topic says. The
    list needs nothing: every suggested article is in Triage until the owner chooses."""
    store, said = ctx.store, []
    for article in store.all("SELECT * FROM article WHERE pending=1 ORDER BY id"):
        topic = store.topic(article["topic_id"]) or {}
        wanted = topic.get("outcomes") or ["list"]
        done = ["list"]
        notes = []
        if "bench" in wanted:
            if cards.file_article(ctx, store.article(article["id"]) or article):
                done.append("bench")
            else:
                notes.append((store.article(article["id"]) or {}).get("note") or "")
        if "memory" in wanted:
            if not mining.mining_on(ctx):
                notes.append("Its topic mines articles, but mining is off in Reader's "
                             "settings.")
            elif article["mined"] == 0:
                store.set_article(article["id"], mine_wanted=mining.TOPIC_ASKS)
                done.append("memory")
        if "offer" in wanted:
            offers.make(ctx, store.article(article["id"]) or article)
            done.append("offer")
        store.set_article(article["id"], pending=0, outcomes=done,
                          note=" ".join(n for n in notes if n))
        ctx.tape("article.suggested", {"article": article["id"], "topic": article["topic_id"],
                                       "by": "researcher" if article["by"] == "researcher"
                                       else "plain"}, subject=f"article:{article['id']}")
        said.append(f"Article {article['id']}: {', '.join(done)}.")
    if said:
        ctx.emit()
    return said


# --- the owner's choice ---------------------------------------------------------------------


def choice_takes_back(state_before: str, action: str, topic: dict[str, Any] | None) -> bool:
    """Rule ``a_changed_mind_takes_back_the_offer``: Keep, Read later or Skip on an
    article the owner sent on themself takes the offer back, because their choice now
    says otherwise. An offer the article's topic makes is not the owner's choice on this
    article, so it stays (and the screen says so)."""
    if state_before != "sent" or action == "send":
        return False
    return "offer" not in ((topic or {}).get("outcomes") or [])


def choose(ctx: Ctx, article_id: int, action: str, *, from_card: bool = False
           ) -> dict[str, Any]:
    """Keep, Read later, Skip, Send on (offers it to other apps) or Mine. The first four
    are the owner's vote on the article and close its Bench card. The row comes back with
    ``offer_change``: "withdrawn" when the choice took an offer back, "stays" when the
    article is still offered after a choice other than Send on, else ""."""
    store = ctx.store
    article = store.article(article_id)
    if article is None or article["state"] not in SUGGESTED:
        raise ArticleError("There is no such article in your list. Nothing was changed.")
    if action == "mine":
        if not mining.mining_on(ctx):
            raise ArticleError("Mining is off, so nothing was mined. Switch on \"Mine "
                               "articles into Memory\" in Reader's settings first.")
        if not article["sentences"]:
            raise ArticleError("This article's text was deleted, so there is nothing to "
                               "mine. Nothing was done.")
        store.set_article(article_id, mine_wanted=mining.OWNER_ASKED,
                          note="Choosing facts for Memory.")
        ctx.tape("article.decided", {"article": article_id, "decision": "mine"},
                 subject=f"article:{article_id}")
        return store.article(article_id) or {}
    if action not in STATE_OF:
        raise ArticleError(f"Reader does not know {action!r}. Nothing was changed. Choose "
                           "keep, later, skip, send or mine.")
    if not from_card:
        cards.withdraw(ctx, article["card_id"])
    done = store.set_article(article_id, state=STATE_OF[action], decided=ctx.now(),
                             card_id=None if not from_card else article["card_id"])
    change = ""
    if action == "send":
        offers.make(ctx, done)
    elif choice_takes_back(article["state"], action, store.topic(article["topic_id"])):
        change = "withdrawn" if offers.withdraw(ctx, article_id) else ""
    elif offers.of_article(ctx, article_id) is not None:
        change = "stays"
    ctx.tape("article.decided", {"article": article_id, "decision": action},
             subject=f"article:{article_id}")
    ctx.emit()
    return {**(store.article(article_id) or {}), "offer_change": change}


def take_back(ctx: Ctx, article_id: int) -> dict[str, Any]:
    """Take back the offer of one article to other apps (``offers.withdraw``). An article
    the owner sent on goes back to waiting in Triage; one its topic offered keeps the
    owner's choice. The row comes back with ``back_in_triage``."""
    store = ctx.store
    article = store.article(article_id)
    if article is None or article["state"] not in SUGGESTED:
        raise ArticleError("There is no such article in your list. Nothing was changed.")
    if offers.withdraw(ctx, article_id) is None:
        raise ArticleError("This article is not offered to other apps, so there was "
                           "nothing to take back. Nothing was changed.")
    back = article["state"] == "sent"
    if back:
        store.set_article(article_id, state="new", decided=None)
    ctx.emit()
    return {**(store.article(article_id) or {}), "back_in_triage": back}


# --- keeping text no longer than asked ------------------------------------------------------


def text_expired(article: dict[str, Any], now_ms: int, days: int) -> bool:
    """Rule ``text_kept_for_days``: an article the owner did not keep loses its text
    ``keep_text_days`` after it was read. Kept and sent-on articles keep theirs."""
    if article["state"] in ("kept", "sent") or not article["text"]:
        return False
    return now_ms - article["created"] >= days * DAY_MS


def expire_text(ctx: Ctx) -> int:
    """Delete the text of every article past its days. The address, title, why-line and
    fingerprint stay, so it is still recognised and never suggested again."""
    cfg, now, count = ctx.cfg(), ctx.now(), 0
    for article in ctx.store.all("SELECT id, state, text, created FROM article WHERE "
                                 "text != ''"):
        if text_expired(article, now, int(cfg.keep_text_days)):
            ctx.store.set_article(article["id"], text="", sentences=[], text_deleted=now)
            count += 1
    if count:
        ctx.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return count
