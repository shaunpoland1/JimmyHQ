"""Finding with no agent: the ``search`` job, every ``search_minutes``.

For each topic it covers, longest unsearched first: one search with the topic's next
phrasing, through the platform's ``web.search``; each new result read through
``web.read`` (``articles.take``), the unreadable and too short dropped with a sentence;
have-I-seen-this on what is left; one ranking call that picks by number (``rank``); the
picks suggested and the rest passed over. Then each suggestion's topic outcomes are
carried out.

Limits, all settings: ``searches_per_day`` (shared with the researcher),
``results_per_search``, ``picks_per_run``, and ``cards_per_day`` on the Bench.
Topics the researcher covers are left to it while an agent holds the role.
"""

from __future__ import annotations

from typing import Any

from jimmyhq_app import CallError

from reader import articles, rank, similar, sources, topics, work
from reader.context import Ctx

MAX_QUERY = 300


class SearchError(ValueError):
    """Nothing was searched. The message says why and what to do."""


def searches_today(ctx: Ctx) -> int:
    today = ctx.today()
    return sum(1 for r in ctx.store.all("SELECT created FROM search ORDER BY id DESC LIMIT "
                                        "2000") if ctx.day(r["created"]) == today)


def searches_left(ctx: Ctx) -> int:
    """Rule ``searches_a_day``: how many more searches today, by anyone."""
    return max(0, int(ctx.cfg().searches_per_day) - searches_today(ctx))


def next_phrasing(ctx: Ctx, topic: dict[str, Any]) -> str:
    """The topic's phrasings in turn, one a search."""
    phrasings = topic["phrasings"] or [topic["name"]]
    at = topic["next_phrasing"] % len(phrasings)
    ctx.store.update("topic", topic["id"], {"next_phrasing": at + 1})
    return phrasings[at]


def search(ctx: Ctx, topic: dict[str, Any] | None, query: str, *, by: str,
           agent: str | None = None) -> list[dict[str, Any]]:
    """One search, counted against the day's limit and kept with its results (so an
    address read later is known to have come from this topic)."""
    query = " ".join(str(query).split())[:MAX_QUERY]
    if not query:
        raise SearchError("There is nothing to search for. Nothing was searched. Give a "
                          "query, or a topic with phrasings.")
    if searches_left(ctx) <= 0:
        raise SearchError(f"Today's {int(ctx.cfg().searches_per_day)} searches are used, so "
                          "nothing was searched. Searching starts again tomorrow.")
    store, now = ctx.store, ctx.now()
    topic_id = topic["id"] if topic else None
    try:
        found = ctx.app.web.search(query, limit=int(ctx.cfg().results_per_search))
    except CallError as error:
        raise SearchError(f"The search did not run: {error.message}") from None
    search_id = store.insert("search", {"topic_id": topic_id, "query": query, "by": by,
                                        "agent": agent, "created": now})
    out = []
    for result in found[:int(ctx.cfg().results_per_search)]:
        try:
            url = sources.check_url(result.url)
        except sources.SourceProblem:
            continue
        store.insert("found", {"search_id": search_id, "topic_id": topic_id, "url": url,
                               "title": (result.title or "")[:300], "created": now})
        out.append({"url": url, "title": result.title, "snippet": result.snippet})
    store.update("search", search_id, {"results": len(out)})
    return out


def order(ctx: Ctx, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topics searched longest ago first, so every topic gets its turn."""
    last = {r["topic_id"]: r["at"] for r in ctx.store.all(
        "SELECT topic_id, MAX(created) AS at FROM search GROUP BY topic_id")}
    return sorted(found, key=lambda t: (last.get(t["id"]) or 0, t["id"]))


def run(ctx: Ctx) -> str:
    """The ``search`` job. Returns a line for the log."""
    found = topics.active(ctx)
    if not found:
        return "No topics, so nothing was searched."
    cfg = ctx.cfg()
    most, kept, said = int(cfg.picks_per_run), 0, []
    for topic in order(ctx, found):
        if not work.plain_covers(ctx, topic):
            continue
        if kept >= most:
            break
        if searches_left(ctx) <= 0:
            said.append("Today's searches are used.")
            break
        phrasing = next_phrasing(ctx, topic)
        try:
            results = search(ctx, topic, phrasing, by="plain")
        except SearchError as error:
            said.append(str(error))
            break
        kept += look_at(ctx, topic, phrasing, results, most - kept, said)
    articles.carry_out(ctx)
    return " ".join([f"Suggested {kept}.", *said])


def look_at(ctx: Ctx, topic: dict[str, Any], phrasing: str, results: list[dict[str, Any]],
            room: int, said: list[str]) -> int:
    """Read each new result, check it, rank what is left and suggest the picks. Returns
    how many were suggested."""
    readable: list[dict[str, Any]] = []
    for result in results:
        article, fresh = articles.take(ctx, result["url"], topic_id=topic["id"],
                                       phrasing=phrasing, by="plain",
                                       title=result.get("title") or "")
        if not fresh or article["state"] != "read":
            continue  # seen at this address before, or dropped with its sentence
        found = similar.seen(ctx, print_=articles.print_of(article), skip=article["id"])
        if found.stops:
            ctx.store.set_article(article["id"], state="dropped", seen=found.to_dict(),
                                  said=f"{similar.describe(found, ctx.store)} It was not "
                                  "suggested again.")
            continue
        readable.append(article)
    if not readable:
        return 0
    picks, note = rank.rank(ctx, topic, phrasing, readable, room)
    if note:
        said.append(note)
    count = 0
    picked = {p.index for p in picks}
    for pick in picks:
        done = articles.suggest(ctx, readable[pick.index]["id"], topic_id=topic["id"],
                                reason=pick.why, by="plain")
        count += done["state"] == "new"
    for index, article in enumerate(readable):
        if index not in picked:
            articles.pass_over(ctx, article["id"], f"Read for {topic['name']}; the ranker "
                               "did not pick it.")
    return count
