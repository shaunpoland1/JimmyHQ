"""The review: one call on a different model role, under plain rules, held to its shape.

The older way lost findings when a review came back in the wrong shape: it read as "no
findings". Here ``read_review`` checks the whole answer against ``REVIEW_SCHEMA`` first.
An answer that is not that shape (not an object, no ``findings`` list, a finding missing
a field or of an unknown kind) raises ``ReviewShapeError``, and the step fails with a
sentence. It is never read as finding nothing.

A finding in the right shape is still set aside, and counted, when it is not usable
(rule ``a finding quotes and cites``): it must quote the article word for word (a
``left_out`` finding may quote a source instead) and cite at least one real source
sentence (an ``unsupported`` one may cite none, since nothing supports it). At most
``review_findings_max`` are kept.
"""

from __future__ import annotations

import json
from typing import Any

from jimmyhq_app.claims import normalise

from articles import markup, prompts, render

KINDS = ("quote_is_paraphrase", "scale_or_direction", "left_out", "unsupported", "other")

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"findings": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": list(KINDS)},
                       "quote": {"type": "string"},
                       "cites": {"type": "array", "items": prompts.CITE},
                       "problem": {"type": "string"}},
        "required": ["kind", "quote", "cites", "problem"]}}},
    "required": ["findings"],
}


class ReviewShapeError(ValueError):
    """The review's answer is not in the agreed shape. ``str()`` says how."""


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def answer_data(reply: Any) -> Any:
    """What the model answered, as data: the platform's parsed JSON, or the text read as
    JSON. None when it is neither."""
    if reply.data is not None:
        return reply.data
    try:
        return json.loads(reply.text)
    except (TypeError, ValueError):
        return None


def check_shape(data: Any) -> list[dict[str, Any]]:
    """Rule ``a review is in the agreed shape``: the findings, or ``ReviewShapeError``."""
    if not isinstance(data, dict):
        raise ReviewShapeError("the answer was not a JSON object")
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ReviewShapeError("the answer had no list of findings")
    for n, item in enumerate(findings, 1):
        if not isinstance(item, dict):
            raise ReviewShapeError(f"finding {n} was not an object")
        missing = [k for k in ("kind", "quote", "cites", "problem") if k not in item]
        if missing:
            raise ReviewShapeError(f"finding {n} had no {', '.join(missing)}")
        if item["kind"] not in KINDS:
            raise ReviewShapeError(f"finding {n} was of a kind the review may not use")
        if not isinstance(item["quote"], str) or not isinstance(item["problem"], str) \
                or not item["problem"].strip():
            raise ReviewShapeError(f"finding {n} had a quote or problem that was not text")
        if not isinstance(item["cites"], list) or not all(
                isinstance(c, dict) and _int(c.get("source")) and _int(c.get("sentence"))
                for c in item["cites"]):
            raise ReviewShapeError(f"finding {n} had citations that were not source and "
                                   "sentence numbers")
    return findings


def usable(item: dict[str, Any], article_text: str, sentences: dict[tuple[int, int], str],
           source_text: str) -> dict[str, Any] | None:
    """Rule ``a finding quotes and cites``: the finding made safe to keep, or None."""
    quote = markup.strip_marks(item["quote"]).strip()
    cites = [(c["source"], c["sentence"]) for c in item["cites"]
             if (c["source"], c["sentence"]) in sentences]
    in_article = bool(quote) and normalise(quote) in normalise(article_text)
    if item["kind"] == "left_out":
        quoted_ok = not quote or in_article or normalise(quote) in normalise(source_text)
    else:
        quoted_ok = in_article
    if not quoted_ok or (not cites and item["kind"] != "unsupported"):
        return None
    return {"kind": item["kind"], "quote": quote[:300], "cites": [list(c) for c in cites],
            "problem": " ".join(item["problem"].split())[:500]}


def read_review(data: Any, revision: dict[str, Any], sources: list[dict[str, Any]],
                most: int) -> tuple[list[dict[str, Any]], int]:
    """The usable findings, at most ``most``, and how many were set aside. Raises
    ``ReviewShapeError`` when the answer is not in the agreed shape."""
    findings = check_shape(data)
    sentences = {(s["n"], i): t for s in sources for i, t in enumerate(s["sentences"], 1)}
    article = f"{revision['title']}\n{render.plain_text(revision['body'])}"
    whole = " ".join(s["text"] for s in sources)
    kept, aside = [], 0
    for item in findings:
        found = usable(item, article, sentences, whole)
        if found is None or len(kept) >= most:
            aside += 1
        else:
            kept.append(found)
    return kept, aside
