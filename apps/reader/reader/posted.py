"""\"Close to something already posted\": what Reader learns from Posts' ``posted`` feed.

Only when the owner let Reader read it (``feed:posts.posted``, asked at install). Each
time ``advance`` runs, Reader reads what Posts printed since it last looked, keeps each
post's title, source address and the word runs of its opening, and marks any article of
the last ``similar_days`` that has the same address or shares enough of its words
(``words_close``) as close to that post. The mark is a line beside the article; nothing
is dropped or hidden because of it.

What Posts sends is data from another app: it is compared, never read as an instruction,
and never shown to a model. A post the owner had erased loses its row here too.

Posts is optional. Without it, or without the grant, nothing is read and nothing is
marked; the sentence JimmyHQ gave is kept for Reader's Offered view, said quietly.
"""

from __future__ import annotations

import json
from typing import Any

from jimmyhq_app import CallError, word_runs, word_share

from reader.context import Ctx

PRODUCER, FEED = "posts", "posted"
GRANT = f"feed:{PRODUCER}.{FEED}"
CURSOR = "posted.cursor"
STATUS = "posted.status"
DAY_MS = 86_400_000
MAX_TITLE = 300
MAX_OPENING = 1200


def granted(ctx: Ctx) -> bool:
    try:
        return GRANT in (ctx.app.hello().get("permissions") or [])
    except CallError:
        return False


def item_row(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rule ``posted_item_is_data``: what Reader keeps of one item of Posts' feed, or None
    when it carries nothing to compare (an older item with ids only). Only plain fields
    of a bounded size are kept."""
    if not isinstance(item, dict) or not isinstance(item.get("post"), int):
        return None
    title = str(item.get("title") or "")[:MAX_TITLE]
    url = str(item.get("source_url") or "")[:2000]
    opening = str(item.get("opening") or "")[:MAX_OPENING]
    if not (title or url or opening):
        return None
    return {"post": item["post"], "title": title, "url": url,
            "runs": word_runs(f"{title} {opening}")}


def read_feed(ctx: Ctx) -> list[str]:
    """Take in what Posts printed since Reader last looked. Returns what it did."""
    if not granted(ctx):
        return []
    store = ctx.store
    after = int(store.get_meta(CURSOR) or 0)
    try:
        page = ctx.app.feeds.read(PRODUCER, FEED, after=after)
    except CallError as error:
        store.set_meta(STATUS, {"ok": False, "said": error.message, "at": ctx.now()})
        return []
    taken = 0
    for entry in page.items:
        store.conn.execute("DELETE FROM posted WHERE seq=?", (entry.seq,))
        store.conn.execute("DELETE FROM posted_close WHERE seq=?", (entry.seq,))
        row = None if entry.erased else item_row(entry.item)
        if row is not None:
            store.conn.execute(
                "INSERT INTO posted (seq, post, title, url, runs, when_ms) VALUES (?,?,?,?,?,?)",
                (entry.seq, row["post"], row["title"], row["url"], json.dumps(row["runs"]),
                 entry.ts or ctx.now()))
            taken += 1
    store.conn.commit()
    store.set_meta(CURSOR, page.next)
    store.set_meta(STATUS, {"ok": True, "said": "", "at": ctx.now()})
    return [f"Took in {taken} posts from Posts."] if taken else []


def closeness(article: dict[str, Any], post: dict[str, Any]) -> tuple[float, str]:
    """Rule ``close_to_a_post``: 1.0 for the same source address, otherwise the share of
    shared three-word runs, and which of the two it was."""
    if post["url"] and post["url"] in (article.get("url"), article.get("final_url")):
        return 1.0, "address"
    return word_share(article.get("runs") or [], post["runs"]), "words"


def note_for(post: dict[str, Any], score: float, how: str) -> str:
    title = f" (\"{post['title'][:80]}\")" if post["title"] else ""
    if how == "address":
        return f"Close to something already posted: post {post['post']}{title} used this " \
               "article."
    return (f"Close to something already posted: post {post['post']}{title}, "
            f"{round(score * 100)}% alike by shared words.")


def mark(ctx: Ctx) -> int:
    """Mark recent articles close to a post Posts sent. Returns how many were marked."""
    store, cfg = ctx.store, ctx.cfg()
    posts = [{**r, "runs": r["runs"] if isinstance(r["runs"], list) else json.loads(r["runs"])}
             for r in store.all("SELECT * FROM posted ORDER BY seq")]
    if not posts:
        return 0
    least = float(cfg.words_close)
    since = ctx.now() - int(cfg.similar_days) * DAY_MS
    marked = 0
    for article in store.suggested_since(since):
        if store.one("SELECT 1 AS x FROM posted_close WHERE article_id=?", (article["id"],)):
            continue
        best: tuple[float, str, dict[str, Any]] | None = None
        for post in posts:
            score, how = closeness(article, post)
            if score >= least and (best is None or score > best[0]):
                best = (score, how, post)
        if best is None:
            continue
        score, how, post = best
        store.conn.execute(
            "INSERT INTO posted_close (article_id, seq, score, how, note, created) VALUES "
            "(?,?,?,?,?,?)", (article["id"], post["seq"], round(score, 4), how,
                              note_for(post, score, how), ctx.now()))
        marked += 1
    store.conn.commit()
    return marked


def follow(ctx: Ctx) -> list[str]:
    """The ``advance`` job's part: read Posts' feed, then mark what is close."""
    said = read_feed(ctx)
    if not granted(ctx):
        return said
    marked = mark(ctx)
    if marked:
        said.append(f"Marked {marked} articles close to something already posted.")
    return said


def note_of(ctx: Ctx, article_id: int) -> str:
    found = ctx.store.one("SELECT note FROM posted_close WHERE article_id=?", (article_id,))
    return found["note"] if found else ""


def status(ctx: Ctx) -> dict[str, Any]:
    """For Reader's page: whether it reads Posts' feed, and what JimmyHQ last said."""
    if not granted(ctx):
        return {"reads": False, "ok": True, "said": "", "at": None}
    found = ctx.store.get_meta(STATUS) or {}
    return {"reads": True, "ok": bool(found.get("ok", True)), "said": found.get("said", ""),
            "at": found.get("at")}
