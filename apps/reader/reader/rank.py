"""Ranking with no agent: one model call that picks articles BY NUMBER from the list it is
given, and says why in a line.

Only the owner's own words go in the prompt: the topic's name, who it is for, and the
phrasing searched. The articles, and the titles of the owner's earlier choices (which
came from the web too), go in the platform's ``untrusted`` argument, as data. A link is
never written by a model: the reply holds numbers, and the code knows the address behind
each. A number that is not on the list, or comes twice, is set aside
(``take_picks``). When the model does not answer, the articles are kept in search order
and their why-line says so: the fallback is visible, never silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jimmyhq_app import CallError

from reader import sources, topics
from reader.context import Ctx

MAX_WHY = 200
OPENING_SENTENCES = 6  # how much of each article the ranker sees

RANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "properties": {"number": {"type": "integer"}, "why": {"type": "string"}},
        "required": ["number", "why"]}}},
    "required": ["picks"],
}


@dataclass(frozen=True)
class Pick:
    index: int  # into the list the ranker was given, from 0
    why: str


def take_picks(data: Any, count: int, most: int) -> tuple[list[Pick], int]:
    """Rule ``picks_are_numbers_from_the_list``: each pick is a whole number from 1 to
    ``count``, once, at most ``most`` of them. Anything else in the reply (an address, a
    title, a number not on the list) is set aside, and the count of those is returned."""
    raw = data.get("picks") if isinstance(data, dict) else None
    picks: list[Pick] = []
    aside = 0
    for item in raw if isinstance(raw, list) else []:
        number = item.get("number") if isinstance(item, dict) else None
        if (isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= count
                or any(p.index == number - 1 for p in picks) or len(picks) >= most):
            aside += 1
            continue
        why = " ".join(str(item.get("why") or "").split())[:MAX_WHY]
        picks.append(Pick(number - 1, why))
    return picks, aside


def prompt(topic: dict[str, Any], phrasing: str, most: int) -> str:
    audience = topics.audience_line(topic)
    who = "the owner" if audience == "you" else f"this audience: {audience}"
    return (f"The owner follows the topic \"{topic['name']}\" and searched for "
            f"\"{phrasing}\". The articles found are listed below as numbered data. Pick "
            f"at most {most} worth the time of {who}, best first, and leave out any that "
            "are thin, off the topic, adverts, or the same story as another. For each "
            "pick give its number and one line saying why it is worth reading. Answer "
            "only with numbers from the list and your lines; never write an address. "
            "Picking none is a good answer when none is worth it.")


def article_block(articles: list[dict[str, Any]]) -> str:
    lines = []
    for n, article in enumerate(articles, 1):
        opening = sources.excerpt(article["sentences"], OPENING_SENTENCES)
        lines.append(f"[{n}] {article['title'] or '(no title)'} ({article['host']})\n"
                     f"{opening}")
    return "Articles found:\n\n" + "\n\n".join(lines)


def votes_block(kept: list[str], skipped: list[str]) -> str | None:
    if not kept and not skipped:
        return None
    lines = ["The owner's earlier choices on this topic, as titles:"]
    lines += [f"kept: {t}" for t in kept] + [f"skipped: {t}" for t in skipped]
    return "\n".join(lines)


def rank(ctx: Ctx, topic: dict[str, Any], phrasing: str, articles: list[dict[str, Any]],
         most: int) -> tuple[list[Pick], str]:
    """The picks, and a note for the log (what was set aside, or why the order is the
    search's own)."""
    if not articles or most <= 0:
        return [], ""
    cfg = ctx.cfg()
    shown = int(cfg.votes_shown)
    untrusted = [article_block(articles)]
    said = votes_block(topics.votes(ctx, topic["id"], ("kept", "sent"), shown),
                       topics.votes(ctx, topic["id"], ("skipped",), shown))
    if said:
        untrusted.append(said)
    try:
        reply = ctx.app.model.ask(prompt(topic, phrasing, most), role=str(cfg.role_rank),
                                  json_schema=RANK_SCHEMA, untrusted=untrusted)
    except CallError as error:
        why = f"Kept in search order, because the ranking model did not answer: {error.message}"
        return [Pick(i, why) for i in range(min(most, len(articles)))], why
    picks, aside = take_picks(reply.data, len(articles), most)
    note = f"{aside} of the ranker's answers were not numbers from the list and were set " \
           "aside." if aside else ""
    return picks, note
