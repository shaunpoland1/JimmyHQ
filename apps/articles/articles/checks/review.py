"""The review's findings, as checks, and a warning when it ran on the writer's own model
role. The review itself is asked for and held to its shape in ``articles.review``."""

from __future__ import annotations

from typing import Any

from articles.checks import Draft, Material, Result, mark

KIND_WORDS = {
    "quote_is_paraphrase": "A paraphrase is presented as a quotation",
    "scale_or_direction": "The scale or direction is wrong",
    "left_out": "An inconvenient finding is left out",
    "unsupported": "The sources do not support this",
    "other": "The review raises this",
}


def review_findings(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    if material.review is None:
        return [Result("review", "warn", "This version has not been reviewed yet.", [],
                       "not_yet")]
    out = []
    for finding in material.review:
        where = f" On “{finding['quote']}”:" if finding.get("quote") else ""
        out.append(Result("review", "ask", f"{KIND_WORDS[finding['kind']]}.{where} "
                          f"{finding['problem']}", [mark(tuple(c)) for c in finding["cites"]],
                          f"review:{finding['kind']}:{finding.get('quote', '')[:60].lower()}"))
    if material.review_aside:
        out.append(Result("review", "ok", f"{material.review_aside} of the review's findings "
                          "were set aside: they did not quote the article word for word, or "
                          "cited no real source sentence.", [], "aside"))
    if not out:
        out.append(Result("review", "ok", "The review found nothing to raise.", [], "none"))
    return out


def same_role(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    writer, reviewer = material.roles
    if writer and writer == reviewer:
        return [Result("review", "warn", f"The article was reviewed on the same model role "
                       f"(“{writer}”) that wrote it, so the review may share its blind spots. "
                       "Choose another under Articles' settings.", [], "same_role")]
    return []
