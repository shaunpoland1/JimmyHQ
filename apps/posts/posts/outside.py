"""The tools Posts offers anyone who holds no role: an agent with no work from Posts, or an
outside AI client the owner let through the MCP door.

    brands(limit)                          each brand: how it sounds, when it posts, which tool
                                           books it, paused or not
    queue(limit, brand)                    posts the owner approved that have not gone out yet
    went_out(limit, brand)                 posts that went out lately, with figures if any

None is in a role of Posts, so none counts a step against an agent's work, credits an
agent, or reads who is asking: the caller is not looked at at all. Each one only reads
Posts' own records (no network, no model, no Bench, no task), and leaves out what is not
the owner's to hand round: no account, key, token or scheduler id, no note the owner wrote
to himself, no draft still waiting for his approval, and nothing of Reader's or Articles'.
Everything comes back in the ``shown`` form, since a post's words came from a model and
from the web. Every list has a ``limit`` (at most ``MOST``), newest first, and says when it
was cut and how many were left out.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from jimmyhq_app import App

from posts import screen, states
from posts.context import Ctx

DEFAULT = 10  # rows when no limit is given
MOST = 25  # the most rows one call may ask for
LINE = 200  # characters kept of one brand's voice
TEXT = 700  # characters kept of one post's words
NAMES = ("brands", "queue", "went_out")

# What the queue lists: approved posts that are on their way out. A draft waiting for the
# owner's approval is not one of them.
ON_THE_WAY = ("queued", "booking", "unsure", "booked", "missing", "failed")

CUT = " (cut)"


class Refused(Exception):
    """The tool did nothing. ``str()`` is the sentence for the caller."""


def refusal(sentence: str) -> dict[str, Any]:
    return {"result": sentence[:300], "ok": False, "shown": sentence}


def answer(result: str, shown: str) -> dict[str, Any]:
    return {"result": result[:300], "ok": True, "shown": shown}


# --- reading what was asked ------------------------------------------------------------------


def limit_of(args: dict[str, Any]) -> int:
    """Rule ``outside_limit_is_small``: a whole number from 1 to ``MOST``; none given is
    ``DEFAULT``."""
    value = args.get("limit")
    if value is None or value == "":
        return DEFAULT
    try:
        if isinstance(value, bool) or isinstance(value, float) and value != int(value):
            raise ValueError
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise Refused(f"\"limit\" must be a whole number from 1 to {MOST}, and you gave "
                      f"{str(value)[:40]!r}. Nothing was read. Leave it out for {DEFAULT}.") \
            from None
    if not 1 <= number <= MOST:
        raise Refused(f"\"limit\" must be from 1 to {MOST}, and you gave {number}. Nothing "
                      f"was read. Leave it out for {DEFAULT}.")
    return number


def brand_of(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any] | None:
    """The brand named by ``brand``, or None when none was named."""
    value = args.get("brand")
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise Refused(f"\"brand\" must be a brand's number, and you gave {str(value)[:40]!r}. "
                      "Nothing was read. Use the brands tool to see the numbers.") from None
    found = ctx.store.page(number)
    if found is None:
        raise Refused(f"There is no brand {number}. Nothing was read. Use the brands tool "
                      "to see the numbers.")
    return found


# --- how things are said ---------------------------------------------------------------------


def stamp(ms: int | None) -> str:
    """A stored time at the display edge: the day and minute, in UTC, saying so."""
    if ms is None:
        return "no time yet"
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


def clip(text: Any, most: int) -> str:
    """One line of someone's words, whitespace made plain, cut with a mark when long."""
    line = " ".join(str(text or "").split())
    return line if len(line) <= most else line[:max(0, most - len(CUT))].rstrip() + CUT


def say(head: str, rows: list[str], total: int, room: int, *, nothing: str) -> str:
    """The head, then as many whole rows as fit in ``room`` characters, then a line that
    says how many were left out (and why) when not all of the ``total`` are shown."""
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
                     "answer. Ask for fewer with limit, or for one brand with brand.)")
    return "\n".join(lines)


def brand_name(ctx: Ctx, cache: dict[int, str], page_id: int) -> str:
    if page_id not in cache:
        cache[page_id] = (ctx.store.page(page_id) or {}).get("name") or f"brand {page_id}"
    return cache[page_id]


# --- the tools -------------------------------------------------------------------------------


def brands(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    most = limit_of(args)
    found = ctx.store.pages()
    rows = []
    for page in found[:most]:
        zone = page.get("timezone") or "the owner's time zone"
        hours = ", ".join(page.get("hours") or []) or "no times set"
        tool = screen.page_tool(ctx, page)
        voice = clip(page.get("register"), LINE) or "no description of its voice"
        rows.append(f"- Brand {page['id']}: {clip(page['name'], 80)}"
                    f"{' (paused: no new work)' if page.get('paused') else ''}\n"
                    f"  Sounds like: {voice}\n  Posts at: {hours} ({zone})\n"
                    f"  Booked through: {clip(tool['label'], 60)}")
    head = (f"The owner's brands in Posts, by name. {len(found)} in all. A brand's name and "
            "description are the owner's own words; the accounts it posts to and every key "
            "are not given.")
    return answer(f"{len(found)} brands.", say(
        head, rows, len(found), int(ctx.cfg().tool_page_chars),
        nothing="There are no brands yet: the owner has not added one."))


def queue(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    most = limit_of(args)
    page = brand_of(ctx, args)
    marks = ",".join("?" * len(ON_THE_WAY))
    where, params = f"state IN ({marks})", list(ON_THE_WAY)
    if page:
        where, params = where + " AND page_id=?", [*params, page["id"]]
    total = ctx.store.one(f"SELECT COUNT(*) AS n FROM post WHERE {where}", params)["n"]  # noqa: S608
    found = ctx.store.all(f"SELECT * FROM post WHERE {where} ORDER BY "  # noqa: S608
                          "COALESCE(slot_ms, updated) DESC, id DESC LIMIT ?", (*params, most))
    names: dict[int, str] = {}
    rows = [f"- Post {p['id']}, {clip(brand_name(ctx, names, p['page_id']), 80)}: "
            f"{states.WORDS.get(p['state'], p['state'])}, for {stamp(p['slot_ms'])}\n"
            f"  {clip(p['body'], TEXT)}" for p in found]
    of = f" for {clip(page['name'], 80)}" if page else ""
    head = (f"Posts the owner approved that have not gone out yet{of}, latest time first. "
            "Drafts still waiting for the owner's approval are not listed. The words are "
            "what will be posted.")
    return answer(f"{total} waiting or booked.", say(
        head, rows, total, int(ctx.cfg().tool_page_chars),
        nothing="Nothing is waiting or booked."))


def figures_of(ctx: Ctx, post_id: int) -> str:
    """What the scheduler reported for one post, summed over its accounts, as words; the
    accounts themselves are never named."""
    found = ctx.store.all("SELECT metric, SUM(value) AS value FROM figure WHERE post_id=? "
                          "GROUP BY metric ORDER BY metric", (post_id,))
    return ", ".join(f"{clip(f['metric'], 40)} {f['value']:g}" for f in found)


def went_out(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    most = limit_of(args)
    page = brand_of(ctx, args)
    where, params = "state='went_out' AND scheduler != 'practice'", []
    if page:
        where, params = where + " AND page_id=?", [page["id"]]
    total = ctx.store.one(f"SELECT COUNT(*) AS n FROM post WHERE {where}", params)["n"]  # noqa: S608
    found = ctx.store.all(f"SELECT * FROM post WHERE {where} ORDER BY "  # noqa: S608
                          "COALESCE(slot_ms, updated) DESC, id DESC LIMIT ?", (*params, most))
    names: dict[int, str] = {}
    rows = []
    for p in found:
        figures = figures_of(ctx, p["id"])
        rows.append(f"- Post {p['id']}, {clip(brand_name(ctx, names, p['page_id']), 80)}, "
                    f"{stamp(p['slot_ms'] or p['updated'])}\n  {clip(p['body'], TEXT)}\n"
                    f"  Figures: {figures or 'none reported'}")
    of = f" for {clip(page['name'], 80)}" if page else ""
    head = (f"Posts that went out{of}, newest first. Figures are what the scheduler "
            "reported, added up over the accounts, and only some schedulers report any.")
    return answer(f"{total} went out.", say(
        head, rows, total, int(ctx.cfg().tool_page_chars),
        nothing="Nothing has gone out through Posts yet."))


# --- wiring ----------------------------------------------------------------------------------

READING: dict[str, Callable[[Ctx, dict[str, Any]], dict[str, Any]]] = {
    "brands": brands, "queue": queue, "went_out": went_out}


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
