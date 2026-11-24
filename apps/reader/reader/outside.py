"""The tools Reader offers anyone who holds no role: an agent with no work from Reader, or
an outside AI client the owner let through the MCP door.

    topic_list(limit)                       the owner's topics: name, phrasings, paused
    article_list(limit, state, topic)       articles the owner kept or has waiting in triage
    article_text(article, start)            the stored text of one of those articles

(Reader's researcher already has ``topics``, so the plain list of topics is ``topic_list``.)

None is in the researcher's role, so none counts a step against a round of looking, credits
an agent or reads who is asking: the caller is not looked at at all. Each only reads Reader's
own records: no search, no fetch, no model, no Bench, no task. Left out on purpose: the
agent or model that found an article, its address history, vectors, the offers made to other
apps, facts mined for Memory, the owner's notes, and the articles Reader merely read or the
owner skipped. Titles, addresses, summaries and article text came from the web or from a
model, so everything is given in the ``shown`` form. Every list has a ``limit`` (at most
``MOST``), newest first, and says when it was cut and how many were left out.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from jimmyhq_app import App

from reader import sources
from reader.context import Ctx

DEFAULT = 10
MOST = 25
LINE = 300  # characters kept of one article's summary
TITLE = 200
NAMES = ("topic_list", "article_list", "article_text")

# The articles the tools show, by the word the caller uses for where each stands. Nothing
# Reader merely read, passed over or dropped, and nothing the owner skipped, is here.
LISTED = {"waiting": "new", "kept": "kept", "later": "later", "sent": "sent"}
STATE_WORDS = {"new": "suggested, waiting for the owner", "kept": "kept by the owner",
               "later": "saved by the owner to read later", "sent": "sent on by the owner"}
CUT = " (cut)"


class Refused(Exception):
    """The tool did nothing. ``str()`` is the sentence for the caller."""


def refusal(sentence: str) -> dict[str, Any]:
    return {"result": sentence[:300], "ok": False, "shown": sentence}


def answer(result: str, shown: str) -> dict[str, Any]:
    return {"result": result[:300], "ok": True, "shown": shown}


def whole(args: dict[str, Any], key: str, *, low: int = 1, high: int | None = None,
          default: int | None = None) -> int | None:
    """A whole number from ``low`` (to ``high``); ``default`` when none was given."""
    value = args.get(key)
    where = f"from {low} to {high}" if high else f"{low} or more"
    if value is None or value == "":
        return default
    try:
        if isinstance(value, bool) or isinstance(value, float) and value != int(value):
            raise ValueError
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise Refused(f"\"{key}\" must be a whole number {where}, and you gave "
                      f"{str(value)[:40]!r}. Nothing was read.") from None
    if number < low or (high is not None and number > high):
        raise Refused(f"\"{key}\" must be a whole number {where}, and you gave {number}. "
                      "Nothing was read.")
    return number


def limit_of(args: dict[str, Any]) -> int:
    return whole(args, "limit", high=MOST, default=DEFAULT) or DEFAULT


def stamp(ms: int | None) -> str:
    if ms is None:
        return "no time"
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


def clip(text: Any, most: int) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= most else line[:max(0, most - len(CUT))].rstrip() + CUT


def say(head: str, rows: list[str], total: int, room: int, *, nothing: str) -> str:
    """The head, then as many whole rows as fit in ``room`` characters, then a line saying
    how many were left out when not all of the ``total`` are shown."""
    lines, used = [head], len(head)
    for row in rows:
        if used + len(row) + 2 > room - 200 and lines[1:]:
            break
        lines.append(row)
        used += len(row) + 1
    shown = len(lines) - 1
    if not shown:
        lines.append(nothing)
    elif shown < total:
        lines.append(f"({total - shown} more not shown here: {shown} of {total} fit in one "
                     "answer. Ask for fewer with limit, or narrow it with state or topic.)")
    return "\n".join(lines)


# --- the tools -------------------------------------------------------------------------------


def topic_list(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    most = limit_of(args)
    found = ctx.store.topics()
    rows = [f"- Topic {t['id']}: {clip(t['name'], 80)}"
            f"{' (paused: not searched for)' if t['paused'] else ''}\n  Phrasings: "
            + "; ".join(clip(p, 200) for p in t["phrasings"]) for t in found[:most]]
    head = (f"The topics the owner has Reader look for, by name. {len(found)} in all. Names "
            "and phrasings are the owner's own words.")
    return answer(f"{len(found)} topics.", say(
        head, rows, len(found), int(ctx.cfg().tool_page_chars),
        nothing="There are no topics yet: the owner has added none."))


def topic_filter(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any] | None:
    number = whole(args, "topic")
    if number is None:
        return None
    found = ctx.store.topic(number)
    if found is None:
        raise Refused(f"There is no topic {number}. Nothing was read. Use the topic_list "
                      "tool to see the numbers.")
    return found


def article_list(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    most = limit_of(args)
    chosen = args.get("state")
    if chosen not in (None, "") and chosen not in LISTED:
        raise Refused(f"\"state\" must be one of {', '.join(LISTED)}, and you gave "
                      f"{str(chosen)[:40]!r}. Nothing was read. Leave it out for all four.")
    wanted = [LISTED[chosen]] if chosen else list(LISTED.values())
    topic = topic_filter(ctx, args)
    marks = ",".join("?" * len(wanted))
    where, params = f"state IN ({marks})", list(wanted)
    if topic:
        where, params = where + " AND topic_id=?", [*params, topic["id"]]
    total = ctx.store.one(f"SELECT COUNT(*) AS n FROM article WHERE {where}", params)["n"]  # noqa: S608
    found = ctx.store.all(f"SELECT id, url, title, topic_id, why, state, suggested, created "  # noqa: S608
                          f"FROM article WHERE {where} ORDER BY COALESCE(suggested, created) "
                          "DESC, id DESC LIMIT ?", (*params, most))
    names = {t["id"]: t["name"] for t in ctx.store.topics()}
    rows = [f"- Article {a['id']}: {clip(a['title'], TITLE) or '(no title)'}\n"
            f"  Address: {clip(a['url'], 300)}\n"
            f"  Topic: {clip(names.get(a['topic_id']), 80) or 'none'}. "
            f"{STATE_WORDS[a['state']]}, {stamp(a['suggested'] or a['created'])}\n"
            f"  Summary Reader holds: {clip(a['why'], LINE) or 'none'}" for a in found]
    head = ("Articles the owner kept, or that wait in the owner's triage, newest first. "
            "Titles and addresses come from the web, and a summary was written by a model "
            "or by Reader. Not articles Reader only read, passed over, dropped or the owner "
            "skipped.")
    return answer(f"{total} articles.", say(
        head, rows, total, int(ctx.cfg().tool_page_chars),
        nothing="There are no articles like that."))


def article_text(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    number = whole(args, "article")
    if number is None:
        raise Refused("You did not say which article. Nothing was read. Give \"article\" as "
                      "a number from article_list.")
    start = whole(args, "start", default=1) or 1
    found = ctx.store.article(number)
    if found is None or found["state"] not in LISTED.values():
        raise Refused(f"Reader has no kept or waiting article {number}. Nothing was read. Use "
                      "article_list to see the numbers.")
    head = (f"Article {found['id']}: {clip(found['title'], TITLE) or '(no title)'}. "
            f"{STATE_WORDS[found['state']]}. Address: {clip(found['url'], 300)}. The text "
            "below came from the web.")
    sentences = found["sentences"]
    if not sentences:
        return answer(f"Article {found['id']} has no text kept.",
                      f"{head}\nIts text was deleted, or never kept, so there is none to give.")
    if start > len(sentences):
        raise Refused(f"Article {found['id']} has {len(sentences)} sentences, and you asked "
                      "to start at a later one. Nothing was read. Leave start out, or use a "
                      "smaller number.")
    room = max(300, int(ctx.cfg().tool_page_chars) - len(head) - 250)
    page, more = sources.sentences_page(sentences, start, room)
    last = (more or len(sentences) + 1) - 1
    tail = (f"\n(Sentences {start} to {last} of {len(sentences)}. Cut here to fit one "
            f"answer: ask again with start={more} to read on.)" if more
            else f"\n(Sentences {start} to {last} of {len(sentences)}: the end.)")
    return answer(f"Article {found['id']}, from sentence {start}.", f"{head}\n\n{page}{tail}")


# --- wiring ----------------------------------------------------------------------------------

READING: dict[str, Callable[[Ctx, dict[str, Any]], dict[str, Any]]] = {
    "topic_list": topic_list, "article_list": article_list, "article_text": article_text}


def guarded(ctx: Ctx, fn: Callable[[Ctx, dict[str, Any]], dict[str, Any]]
            ) -> Callable[..., dict[str, Any]]:
    """The tool as the platform calls it. ``agent`` and ``role`` are accepted and never
    looked at: who asks changes nothing, and nothing here is counted against anyone."""

    def run(args: dict[str, Any], agent: str = "", role: str | None = None) -> dict[str, Any]:
        try:
            return fn(ctx, dict(args or {}))
        except Refused as no:
            return refusal(str(no))
    run.__name__ = fn.__name__
    return run


def register(app: App, ctx: Ctx) -> None:
    for name, fn in READING.items():
        app.tool(name)(guarded(ctx, fn))
