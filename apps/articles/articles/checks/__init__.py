"""The claims checks: small deterministic functions that annotate an article and never
block it.

Each check is a named function ``check(draft, material, cfg) -> list[Result]`` with its
own tests. A result is ``ok``, ``warn`` or ``ask`` (a question the owner can answer: yes,
no or fixed), with a detail for the owner and its evidence as ``S<source>:<sentence>``.
They run on every revision and every edit, whoever wrote the text; approving with
questions open is allowed. Nothing here calls a model or the network: the review is
asked first and handed in as ``Material.review``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from articles import markup

LEVELS = ("ok", "warn", "ask")
ANSWERS = ("yes", "no", "fixed")


@dataclass(frozen=True)
class Result:
    name: str
    level: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    key: str = ""  # what makes it the same finding in the next revision

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"A check's level is ok, warn or ask, not {self.level!r}.")


@dataclass(frozen=True)
class Draft:
    title: str
    body: str  # sanitised HTML with citation marks
    aside: list[str] = field(default_factory=list)  # what the sanitiser took out

    def claims(self) -> list[markup.Claim]:
        return markup.claims(self.body)


@dataclass(frozen=True)
class Material:
    """Everything a check may look at besides the article."""

    sources: dict[int, dict[str, Any]] = field(default_factory=dict)  # n -> source row
    review: list[dict[str, Any]] | None = None  # None: not reviewed yet
    review_aside: int = 0
    roles: tuple[str, str] = ("", "")  # (writing role, reviewing role)
    flagged: tuple[str, ...] = ()  # spans the owner quoted when sending articles back

    def sentences(self) -> dict[tuple[int, int], str]:
        return {(n, i): text for n, s in self.sources.items()
                for i, text in enumerate(s["sentences"], 1)}

    def text_of(self, cites: tuple[tuple[int, int], ...] | list[tuple[int, int]]
                ) -> dict[tuple[int, int], str]:
        every = self.sentences()
        return {c: every[c] for c in cites if c in every}


Check = Callable[[Draft, Material, Any], list[Result]]


def mark(cite: tuple[int, int]) -> str:
    return f"S{cite[0]}:{cite[1]}"


def marks(cites: list[tuple[int, int]]) -> str:
    return ", ".join(mark(c) for c in cites)


def all_checks() -> tuple[Check, ...]:
    from articles.checks import claims, review, text  # noqa: PLC0415

    return (claims.cites_are_real, claims.numbers_in_cited_sentences, claims.exact_quotes,
            claims.named_in_cited_sentences, claims.uncited_claims, claims.advice_as_fact,
            claims.weak_sources_cited, text.links_are_sources, text.markup_allowed,
            text.owner_flagged, review.review_findings, review.same_role)


def run_all(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    out: list[Result] = []
    for check in all_checks():
        out.extend(check(draft, material, cfg))
    return out


def open_count(results: list[dict[str, Any]] | list[Result]) -> int:
    """Questions not answered yet: what a repair pass must reduce."""
    count = 0
    for r in results:
        level = r["level"] if isinstance(r, dict) else r.level
        answer = r.get("answer") if isinstance(r, dict) else None
        if level == "ask" and not answer:
            count += 1
    return count
