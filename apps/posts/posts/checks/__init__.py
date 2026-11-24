"""The checks: small deterministic functions that annotate a draft and never block it.

Each check is a named function ``check(draft, material, cfg) -> list[Result]``, with its
own tests. A result is ``ok``, ``warn`` or ``ask`` with a detail for the owner and its
evidence (source sentence numbers, ``"notes"``, or ``"post:<id>"``). They run on every
revision and on every edit, whoever wrote the text. An ``ask`` is a question the owner
can answer (yes, no or fixed); approving with questions open is allowed.

Nothing here calls a model or the network: anything measured outside (the overlap
score, the checker's points) is measured first and handed in as ``Material``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

LEVELS = ("ok", "warn", "ask")
ANSWERS = ("yes", "no", "fixed")


@dataclass(frozen=True)
class Result:
    name: str
    level: str
    detail: str
    evidence: list[Any] = field(default_factory=list)
    key: str = ""  # what makes it the same question on the next revision

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"A check's level is ok, warn or ask, not {self.level!r}.")


@dataclass(frozen=True)
class Draft:
    title: str
    body: str
    claims: list[dict[str, Any]] = field(default_factory=list)  # {text, sentences: [n]}
    links: list[str] = field(default_factory=list)  # addresses the code wrote
    sponsored: bool = False


@dataclass(frozen=True)
class Material:
    """Everything a check may look at besides the draft."""

    sentences: list[str] = field(default_factory=list)
    notes: str = ""
    source_url: str | None = None
    way: str = "link"
    rules: list[dict[str, Any]] = field(default_factory=list)
    page_tags: list[str] = field(default_factory=list)
    send_back_quotes: list[str] = field(default_factory=list)
    recent: list[tuple[int, str]] = field(default_factory=list)  # (post id, body)
    overlap: dict[str, Any] | None = None  # similar.Overlap.to_dict(), measured first
    overlap_text: str = ""  # "82% like the post of 3 weeks ago, by meaning."
    checker_points: list[dict[str, Any]] | None = None  # None: no check call yet
    roles: tuple[str, str] = ("", "")  # (drafting role, checking role)
    pictures: list[dict[str, Any]] = field(default_factory=list)  # pictures.likeness()
    like_days: int = 0  # how far back the pictures were compared


Check = Callable[[Draft, Material, Any], list[Result]]


def all_checks() -> tuple[Check, ...]:
    from posts.checks import (  # noqa: PLC0415
        lived,
        numbers,
        owner,
        pictures,
        quotes,
        review,
        source,
        text,
    )

    return (source.near_duplicate, numbers.numbers, quotes.exact_quotes,
            lived.lived_experience, source.source_strength, text.markdown, text.links,
            owner.sponsor, owner.owner_rules, owner.send_back_quotes, text.recycled,
            review.checker_points, review.same_role, pictures.likeness)


def run_all(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    out: list[Result] = []
    for check in all_checks():
        out.extend(check(draft, material, cfg))
    return out


def open_questions(results: list[dict[str, Any]]) -> int:
    return sum(1 for r in results if r["level"] == "ask" and not r.get("answer"))
