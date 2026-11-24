"""\"Have I seen this?\": asked before anything is kept, whoever found it.

Three answers, in order:

- **the same address**: an article Reader has already read there, kept or not;
- **the same story** from another address: by meaning, through the owner's
  ``embeddings`` role (``model.embed``), or, when there is none, by shared three-word runs;
- **close to** an article already suggested: still suggested, and marked so.

Each article's fingerprint is made once, when it is read (``fingerprint``): its vector
and the model that made it, and a set of hashed word runs. Comparing is then local, so
an agent's ``record`` tool, which may not reach outside the app, runs exactly the same
check as the job. The hashes are not text, so they stay when an article's text is
deleted, and an old story is still recognised. The result always says how it was
measured, and why words were used when they were.

The word-run comparison is the SDK's (``jimmyhq_app.word_runs`` and ``word_share``), the
same as Posts' fallback.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from jimmyhq_app import CallError, cosine, word_runs, word_share

from reader import config
from reader.context import Ctx

DAY_MS = 86_400_000
OPENING_SENTENCES = 12  # how much of an article stands for it in a comparison
NO_EMBEDDINGS = "no embeddings model answered"


def opening(title: str, sentences: list[str]) -> str:
    return " ".join([title.strip(), *sentences[:OPENING_SENTENCES]]).strip()


def runs_of(text: str) -> list[int]:
    """The text's three-word runs, hashed: what Reader keeps to compare by words."""
    return word_runs(text)


@dataclass(frozen=True)
class Print:
    vector: list[float] | None
    model: str | None
    runs: list[int]
    note: str = ""  # why there is no vector


def fingerprint(ctx: Ctx, title: str, sentences: list[str]) -> Print:
    """Made once, when an article is read. Asks the owner's embeddings role; without
    one, the word runs alone are kept and the note says why."""
    text = opening(title, sentences)
    runs = runs_of(text)
    try:
        found = ctx.app.model.embed([text])
    except CallError as error:
        return Print(None, None, runs, f"{NO_EMBEDDINGS}: {error.message}")
    vector = found.vectors[0] if found.vectors else None
    return Print(vector, found.model or "", runs, "" if vector else NO_EMBEDDINGS)


def text_print(text: str) -> Print:
    """A fingerprint of a passage, by its words only (as the ``seen`` tool compares)."""
    return Print(None, None, runs_of(text), "a passage is compared by its words")


@dataclass(frozen=True)
class Seen:
    status: str  # "new", "same_address", "same_story" or "close"
    article: int | None = None
    score: float = 0.0
    method: str = ""  # "embeddings" or "words" ("" for an address)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def stops(self) -> bool:
        """Whether the article must not be suggested again."""
        return self.status in ("same_address", "same_story")


def score_of(print_: Print, other: dict[str, Any]) -> tuple[float, str]:
    """How alike two articles are, and how that was measured: by meaning when both have
    a vector from the same model, otherwise by shared word runs."""
    if (print_.vector and other.get("vector") and print_.model is not None
            and other.get("vector_model") == print_.model
            and len(print_.vector) == len(other["vector"])):
        return cosine(print_.vector, other["vector"]), "embeddings"
    return word_share(print_.runs, other.get("runs") or []), "words"


def same_address(ctx: Ctx, url: str, *, skip: int | None = None) -> dict[str, Any] | None:
    """Rule ``same_address``: an article already read at this address, in any state."""
    found = ctx.store.by_url(url)
    if found is None or found["id"] == skip:
        return None
    return found


def seen(ctx: Ctx, *, url: str | None = None, print_: Print | None = None,
         skip: int | None = None) -> Seen:
    """Rule ``have_i_seen_this``: the same address, the same story, or close to one
    already suggested within ``similar_days``. ``skip`` is the article being asked
    about, so it is never matched with itself."""
    if url:
        earlier = same_address(ctx, url, skip=skip)
        if earlier is not None:
            return Seen("same_address", earlier["id"])
    if print_ is None:
        return Seen("new")
    cfg, store = ctx.cfg(), ctx.store
    since = ctx.now() - int(cfg.similar_days) * DAY_MS
    pairs = {kind: config.pair(cfg, store, kind) for kind in config.PAIRS}
    best: Seen | None = None
    rank = {"same_story": 2, "close": 1}
    for other in store.suggested_since(since):
        if other["id"] == skip:
            continue
        score, method = score_of(print_, other)
        limits = pairs[method]
        status = ("same_story" if score >= limits.high else
                  "close" if score >= limits.low else None)
        if status is None:
            continue
        found = Seen(status, other["id"], round(score, 4), method)
        if best is None or (rank[status], score) > (rank[best.status], best.score):
            best = found
    notes = [n for n in (print_.note, *(p.refused or "" for p in pairs.values())) if n]
    note = " ".join(notes)
    if best is None:
        method = "embeddings" if print_.vector else "words"
        return Seen("new", method=method, note=note)
    return Seen(best.status, best.article, best.score, best.method, note)


def describe(found: Seen, store: Any) -> str:
    """One plain sentence: what it was matched with, how closely, and how."""
    how = {"embeddings": "by meaning", "words": "by shared words"}.get(found.method, "")
    other = store.article(found.article) if found.article else None
    name = f"article {found.article}"
    if other is not None and other.get("title"):
        name += f" (\"{other['title'][:80]}\")"
    if found.status == "same_address":
        what = "already suggested" if other and other["state"] in (
            "new", "kept", "later", "skipped", "sent") else "already read"
        return f"The same address as {name}, {what}."
    if found.status == "same_story":
        return f"The same story as {name}: {round(found.score * 100)}% alike {how}."
    if found.status == "close":
        return f"Close to {name}: {round(found.score * 100)}% alike {how}."
    return f"Nothing like it in Reader {how}.".replace("  ", " ").replace(" .", ".")
