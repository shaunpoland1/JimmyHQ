"""\"Have we written about this lately?\": asked first, before anything is spent.

A new article's topic, angle and notes are compared with the site's articles from the
last ``coverage_days`` (any not declined or dropped, finished or still being worked on):
each one's title, topic and angle. Similarity comes from the owner's ``embeddings`` role
through the platform (``model.embed``); when there is none, or it does not answer, the
SDK's comparison by shared three-word runs (``word_overlap``) is used instead, and the
result says which was used and why. It is never silently skipped.
"""

from __future__ import annotations

from typing import Any

from jimmyhq_app import CallError, similar, word_overlap

from articles import states
from articles.context import Ctx

DAY_MS = 86_400_000
EMBED_BATCH = 60  # texts per model.embed call, under the platform's 64


def subject_text(piece: dict[str, Any], title: str = "") -> str:
    """What stands for an article in a comparison."""
    return " ".join(p.strip() for p in (title, piece["topic"], piece["angle"], piece["notes"])
                    if p and p.strip())[:1500]


def corpus(ctx: Ctx, piece: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    since = ctx.now() - int(ctx.cfg().coverage_days) * DAY_MS
    marks = ",".join("?" * len(states.COUNTED))
    out = []
    for other in ctx.store.all(f"SELECT * FROM piece WHERE site_id=? AND id != ? AND state IN "  # noqa: S608
                               f"({marks}) AND created >= ? ORDER BY id",
                               (piece["site_id"], piece["id"], *sorted(states.COUNTED), since)):
        revision = ctx.store.revision(other["id"])
        title = revision["title"] if revision else ""
        out.append(({"id": other["id"], "title": title or other["topic"],
                     "when": other["created"]}, subject_text(other, title)))
    return out


def _embedded(ctx: Ctx, text: str, others: list[str]) -> list[float]:
    scores: list[float] = []
    for start in range(0, len(others), EMBED_BATCH):
        batch = others[start:start + EMBED_BATCH]
        found = ctx.app.model.embed([text, *batch]).vectors
        ranked = dict(similar(found[0], found[1:]))
        scores.extend(ranked.get(i, 0.0) for i in range(len(batch)))
    return scores


def compare(ctx: Ctx, piece: dict[str, Any]) -> dict[str, Any]:
    """``{method, score, level, close, best, note}``: how close this topic is to the site's
    recent articles, and how that was measured."""
    cfg = ctx.cfg()
    found = corpus(ctx, piece)
    text = subject_text(piece)
    method, note, scores = "embeddings", "", []
    others = [t for _, t in found]
    if others:
        try:
            scores = _embedded(ctx, text, others)
        except CallError as error:
            method = "words"
            note = ("Compared by shared words, because the embeddings model did not answer: "
                    f"{error.message}")
    if method == "words":
        scores = [word_overlap(text, other) for other in others]
    level = float(cfg.cover_close if method == "embeddings" else cfg.cover_words_close)
    pairs = sorted(zip([m for m, _ in found], scores, strict=True), key=lambda p: -p[1])
    best = {**pairs[0][0], "score": round(pairs[0][1], 4)} if pairs else None
    top = best["score"] if best else 0.0
    return {"method": method, "score": top, "level": level, "close": top >= level,
            "best": best, "note": note, "compared": len(found)}


def describe(found: dict[str, Any], now_ms: int) -> str:
    """\"86% like \"Soil that holds water\", started 3 weeks ago, by meaning.\""""
    how = "by meaning" if found["method"] == "embeddings" else "by shared words"
    best = found.get("best")
    if not best:
        return f"Nothing this site wrote lately is like it ({how})."
    days = max(0, (now_ms - best["when"]) // DAY_MS)
    when = "today" if days < 1 else f"{days} day{'s' if days != 1 else ''} ago"
    return f"{round(best['score'] * 100)}% like \"{best['title']}\", started {when}, {how}."
