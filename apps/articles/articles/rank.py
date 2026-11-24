"""Ranking the pages read for an article, with no model: which to keep, in what order, and
a sentence for each saying why.

The older way kept the first ten pages in the order they arrived. Here every page that
could be read is scored by named rules, and the best are kept:

- ``is_primary``: a host the owner prefers, or one ending as the owner's list says
  (official and research bodies by default), comes first;
- ``is_weak``: a host on the owner's weak list comes last, and is flagged;
- ``substance``: a fuller page (``substantial_sentences``) before a short one;
- ``gives_figures``: a page that states figures before one that does not;
- ``same_story``: two copies of one story count once. Pages are compared by shared
  three-word runs (the SDK's ``word_share``); the better-ranked copy is kept and the other
  is left out with a sentence naming the one it repeats.

Ties keep the order the searches found them in, so the same pages always rank the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jimmyhq_app import word_runs, word_share
from jimmyhq_app.claims import find_numbers

PRIMARY, WEAK, SUBSTANTIAL, FIGURES = 4.0, -6.0, 2.0, 1.0
FIGURE_SENTENCES = 3  # sentences with figures that make a page "give figures"


@dataclass
class Ranked:
    index: int  # the order it was found in, from 0
    page: dict[str, Any]  # {url, final_url, host, title, text, sentences}
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    weak: bool = False
    kept: bool = False
    why: str = ""


def _on(host: str, hosts: list[str]) -> str | None:
    for listed in hosts:
        listed = str(listed).strip().lower().removeprefix("www.")
        if listed and (host == listed or host.endswith("." + listed)):
            return listed
    return None


def is_primary(host: str, cfg: Any) -> bool:
    """Rule ``primary sources first``: on the owner's list of hosts, or ending as the
    owner's list of endings says."""
    if _on(host, list(cfg.primary_hosts or [])):
        return True
    endings = [str(e).strip().lower() for e in cfg.primary_endings or [] if str(e).strip()]
    return any(host.endswith(e if e.startswith(".") else "." + e) for e in endings)


def is_weak(host: str, cfg: Any) -> bool:
    """Rule ``weak hosts last``: on the owner's weak list (sub-hosts too)."""
    return _on(host, list(cfg.weak_hosts or [])) is not None


def substance(sentences: list[str], cfg: Any) -> bool:
    return len(sentences) >= int(cfg.substantial_sentences)


def gives_figures(sentences: list[str]) -> bool:
    return sum(1 for s in sentences if find_numbers(s)) >= FIGURE_SENTENCES


def same_story(first: list[int], second: list[int], cfg: Any) -> bool:
    """Rule ``one story counts once``: two pages share at least ``same_story_share`` of
    the shorter one's three-word runs."""
    return word_share(first, second) >= float(cfg.same_story_share)


def score(page: dict[str, Any], cfg: Any) -> tuple[float, list[str], bool]:
    host, sentences = page["host"], page["sentences"]
    points, reasons, weak = 0.0, [], False
    if is_primary(host, cfg):
        points += PRIMARY
        reasons.append(f"a primary source ({host})")
    if is_weak(host, cfg):
        points += WEAK
        weak = True
        reasons.append(f"on {host}, which you count as weak")
    if substance(sentences, cfg):
        points += SUBSTANTIAL
        reasons.append(f"substantial ({len(sentences)} sentences)")
    else:
        reasons.append(f"short ({len(sentences)} sentences)")
    if gives_figures(sentences):
        points += FIGURES
        reasons.append("gives figures")
    return points, reasons, weak


def rank(pages: list[dict[str, Any]], cfg: Any) -> list[Ranked]:
    """Every page, best first, each kept or left out with a sentence saying why. At most
    ``max_sources`` are kept."""
    scored = []
    for index, page in enumerate(pages):
        points, reasons, weak = score(page, cfg)
        scored.append(Ranked(index, page, points, reasons, weak))
    scored.sort(key=lambda r: (-r.score, r.index))
    most = int(cfg.max_sources)
    kept: list[tuple[Ranked, list[int]]] = []
    for item in scored:
        runs = word_runs(item.page["text"])
        twin = next((k for k, k_runs in kept if same_story(runs, k_runs, cfg)), None)
        said = ", ".join(item.reasons)
        if twin is not None:
            item.why = (f"Left out: the same story as {twin.page['host']} "
                        f"(\"{twin.page['title'] or twin.page['url']}\"), which ranks higher.")
        elif len(kept) >= most:
            item.why = f"Left out: {most} better sources were kept first. It was {said}."
        else:
            item.kept = True
            item.why = f"Kept{', though weak' if item.weak else ''}: {said}."
            kept.append((item, runs))
    return scored
