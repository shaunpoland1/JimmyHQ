"""Comparing the vectors ``app.model.embed`` returns, so each app need not write its own.

    vectors = app.model.embed(["first text", "second text", "third text"]).vectors
    for index, score in similar(vectors[0], vectors[1:], top=2, least=0.8):
        ...

Vectors are only comparable when one model made them: keep ``Embedding.model`` beside
any you store, and make them again when it changes.

Without an embeddings model, texts can still be compared by the words they share, the
same way in every app and with no model at all:

    runs = word_runs(title_and_opening)      # store these: they are numbers, not text
    word_share(runs, other_runs)             # 0 to 1
    word_overlap(first_text, second_text)    # the same, from two texts
"""

from __future__ import annotations

import math
import re
import zlib
from collections.abc import Sequence

RUN_WORDS = 3  # words in one run
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """How alike two vectors point, from -1 to 1. 0 when either is all zeros."""
    if len(a) != len(b):
        raise ValueError(f"These vectors have {len(a)} and {len(b)} numbers, so they were "
                         "made by different models and cannot be compared.")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


def similar(query: Sequence[float], vectors: Sequence[Sequence[float]], *,
            top: int | None = None, least: float | None = None) -> list[tuple[int, float]]:
    """``(index, score)`` for each of ``vectors``, most alike to ``query`` first. ``top``
    keeps only that many; ``least`` drops any scoring below it. Equal scores keep their
    order."""
    scored = [(index, cosine(query, vector)) for index, vector in enumerate(vectors)]
    if least is not None:
        scored = [pair for pair in scored if pair[1] >= least]
    scored.sort(key=lambda pair: -pair[1])
    return scored if top is None else scored[:max(0, top)]


def word_runs(text: str, size: int = RUN_WORDS) -> list[int]:
    """The text's runs of ``size`` words, lower case, each hashed to a number, sorted and
    without repeats. The hashes are not text, so they may be kept after the text is
    deleted, and an old story is still recognised."""
    words = _WORD.findall(text.lower())
    if len(words) < size:
        found = {" ".join(words)} if words else set()
    else:
        found = {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}
    return sorted({zlib.crc32(run.encode("utf-8")) for run in found})


def word_share(first: Sequence[int], second: Sequence[int]) -> float:
    """The share of the shorter text's runs that the other also has, 0 to 1. The same
    two texts always give the same number; an empty one shares nothing."""
    a, b = set(first), set(second)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def word_overlap(first: str, second: str, size: int = RUN_WORDS) -> float:
    """``word_share`` of two texts' word runs."""
    return word_share(word_runs(first, size), word_runs(second, size))
