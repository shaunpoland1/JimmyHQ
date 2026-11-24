"""Checks about where a draft came from: overlap with what the page covered lately, and
how strong its source is."""

from __future__ import annotations

from typing import Any

from posts.checks import Draft, Material, Result
from posts.sources import host_of


def near_duplicate(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    """Both tiers. Never withheld: after approval the warning rides along."""
    overlap = material.overlap
    if overlap is None:
        return [Result("near_duplicate", "warn", "The overlap check has not run on this text, "
                       "so Posts cannot say whether the brand covered it lately.", [], "missing")]
    best = overlap.get("best")
    evidence = [f"{best['what']}:{best['id']}"] if best else []
    said = material.overlap_text or ""
    note = f" {overlap['note']}" if overlap.get("note") else ""
    if overlap["tier"] == "strong":
        return [Result("near_duplicate", "ask", f"{said} Post it anyway?{note}", evidence,
                       "strong")]
    if overlap["tier"] == "close":
        return [Result("near_duplicate", "warn", f"{said} Make sure it says something new."
                       f"{note}", evidence, "close")]
    how = "by meaning" if overlap["method"] == "embeddings" else "by shared words"
    return [Result("near_duplicate", "ok", f"Nothing the brand posted lately is close to it, "
                   f"{how}.{note}", evidence, "none")]


def _weak(host: str, weak_hosts: list[str]) -> str | None:
    for weak in weak_hosts:
        weak = str(weak).strip().lower().removeprefix("www.")
        if weak and (host == weak or host.endswith("." + weak)):
            return weak
    return None


def source_strength(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    if not material.source_url:
        return [Result("source", "warn", "This piece has no article: every number and quote "
                       "can rest only on your notes.", [], "none")]
    host = host_of(material.source_url).removeprefix("www.")
    out = []
    weak = _weak(host, list(cfg.weak_hosts or []))
    if weak:
        out.append(Result("source", "warn", f"The source is on {host}, which you count as a "
                          "weak source. Look for the original.", [], "weak"))
    chars = sum(len(s) + 1 for s in material.sentences)
    if chars < int(cfg.short_source_chars):
        out.append(Result("source", "warn", f"The source has only {chars} characters of text, "
                          "so there is little to check the draft against.", [], "short"))
    if not out:
        out.append(Result("source", "ok", f"The source on {host} was read in full: "
                          f"{len(material.sentences)} sentences.", [], "ok"))
    return out
