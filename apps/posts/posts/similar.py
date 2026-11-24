"""\"Have we covered this lately?\": asked before any drafting, on every way in, and again
on every draft.

A piece's text is compared with the page's posts going out or sent within
``dup_days``, and with the page's other open pieces. Embeddings come from the owner's
``embeddings`` role through the platform (``model.embed``); when there is none, or it
does not answer, a deterministic comparison by shared three-word runs is used instead and
the result says so. It is never silently skipped. The comparison by words is the SDK's
(``jimmyhq_app.word_overlap``), the same as Reader's.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from jimmyhq_app import CallError, similar, word_overlap

from posts import config, states
from posts.store import Store

__all__ = ["Match", "Overlap", "compare", "corpus", "describe", "word_overlap"]

DAY_MS = 86_400_000
OPENING_CHARS = 800  # how much of a text stands for it in a comparison
EMBED_BATCH = 60  # texts per model.embed call, under the platform's 64


@dataclass(frozen=True)
class Match:
    what: str  # "post" or "piece"
    id: int
    score: float
    when_ms: int
    title: str


@dataclass(frozen=True)
class Overlap:
    method: str  # "embeddings" or "words"
    tier: str  # "none", "close" or "strong"
    score: float
    warn: float
    strong: float
    best: Match | None = None
    matches: list[Match] = field(default_factory=list)
    note: str = ""  # why words were used, or why the owner's thresholds were not

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def opening(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())[:OPENING_CHARS]


# --- what is compared with ------------------------------------------------------------------


def corpus(store: Store, page_id: int, *, days: int, now_ms: int,
           skip_piece: int | None = None) -> list[tuple[Match, str]]:
    """The page's posts going out or sent within the window, and its open pieces."""
    since = now_ms - days * DAY_MS
    out: list[tuple[Match, str]] = []
    marks = ",".join("?" * len(states.POST_OUT))
    for post in store.all(f"SELECT * FROM post WHERE page_id=? AND state IN ({marks}) "  # noqa: S608
                          "AND COALESCE(slot_ms, updated) >= ? ORDER BY id",
                          (page_id, *sorted(states.POST_OUT), since)):
        if post["piece_id"] == skip_piece:
            continue
        out.append((Match("post", post["id"], 0.0, post["slot_ms"] or post["updated"],
                          post["title"]), opening(post["title"], post["body"])))
    marks = ",".join("?" * len(states.PIECE_OPEN))
    for piece in store.all(f"SELECT * FROM piece WHERE page_id=? AND state IN ({marks}) "  # noqa: S608
                           "ORDER BY id", (page_id, *sorted(states.PIECE_OPEN))):
        post = store.post_of(piece["id"])
        if piece["id"] == skip_piece or (post is not None and post["state"] in states.POST_OUT):
            continue  # its post is already counted
        out.append((Match("piece", piece["id"], 0.0, piece["created"],
                          piece["source_title"] or piece["topic"]),
                    piece_text(piece)))
    return out


def piece_text(piece: dict[str, Any]) -> str:
    return opening(piece["source_title"], piece["topic"], piece["notes"], piece["source_text"])


def _embed_scores(app: Any, text: str, others: list[str]) -> list[float]:
    scores: list[float] = []
    for start in range(0, len(others), EMBED_BATCH):
        batch = others[start:start + EMBED_BATCH]
        found = app.model.embed([text, *batch]).vectors
        ranked = dict(similar(found[0], found[1:]))
        scores.extend(ranked.get(i, 0.0) for i in range(len(batch)))
    return scores


LOCAL_NOTE = ("Compared by shared words for now; Posts measures it by meaning again before "
              "the draft goes on.")


def compare(app: Any, store: Store, cfg: config.Config, *, page_id: int, text: str,
            now_ms: int, skip_piece: int | None = None, use_embeddings: bool = True) -> Overlap:
    """How close ``text`` is to what the page has covered lately, and which way that was
    measured. ``use_embeddings=False`` compares by shared words without asking a model,
    as an agent's ``record`` tool must (it may not reach outside the app)."""
    found = corpus(store, page_id, days=int(cfg.dup_days), now_ms=now_ms,
                   skip_piece=skip_piece)
    others = [t for _, t in found]
    method, note = "embeddings", ""
    scores: list[float] = []
    if others and not use_embeddings:
        method, note = "words", LOCAL_NOTE
    elif others:
        try:
            scores = _embed_scores(app, text, others)
        except CallError as error:
            method = "words"
            note = ("Compared by shared words, because the embeddings model did not answer: "
                    f"{error.message}")
    if method == "words":
        scores = [word_overlap(text, other) for other in others]
    limits = config.thresholds(cfg, store, method)
    if limits.refused:
        note = f"{note} {limits.refused}".strip()
    matches = sorted((Match(m.what, m.id, round(s, 4), m.when_ms, m.title)
                      for (m, _), s in zip(found, scores, strict=True)),
                     key=lambda m: -m.score)
    best = matches[0] if matches else None
    score = best.score if best else 0.0
    tier = "strong" if score >= limits.high else "close" if score >= limits.low else "none"
    return Overlap(method=method, tier=tier, score=score, warn=limits.low,
                   strong=limits.high, best=best, matches=matches[:5], note=note)


def ago(then_ms: int, now_ms: int) -> str:
    """A plain \"3 weeks ago\"."""
    days = max(0, (now_ms - then_ms) // DAY_MS)
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def describe(overlap: Overlap, now_ms: int) -> str:
    """\"82% like the post of 3 weeks ago\", with how it was measured."""
    how = "by meaning" if overlap.method == "embeddings" else "by shared words"
    if overlap.best is None:
        return f"Nothing like it in the last while ({how})."
    best = overlap.best
    when = ago(best.when_ms, now_ms)
    which = f"the post of {when}" if best.what == "post" else f"the piece started {when}"
    name = f" (\"{best.title}\")" if best.title else ""
    return f"{round(best.score * 100)}% like {which}{name}, {how}."
