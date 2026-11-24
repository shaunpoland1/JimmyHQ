"""Checks on the text itself: Markdown, addresses, and runs recycled from recent drafts."""

from __future__ import annotations

import re
from typing import Any

from posts.checks import Draft, Material, Result
from posts.sources import host_of

# --- Markdown: the booked text is plain -----------------------------------------------------

_MARKS = (
    ("a link written as [text](address)",
     re.compile(r"\[([^\[\]\n]{1,500})\]\([^()\s]{0,2000}\)"), r"\1"),
    ("bold or italic marks", re.compile(r"(\*\*|__)(?=\S)(.{1,1000}?)(?<=\S)\1"), r"\2"),
    ("bold or italic marks", re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"),
     r"\1"),
    ("a heading mark", re.compile(r"^\s{0,3}#{1,6}\s+", re.M), ""),
    ("a list mark", re.compile(r"^[^\S\n]*[-*+][^\S\n]+", re.M), ""),
    ("a quote mark", re.compile(r"^[^\S\n]*>[^\S\n]?", re.M), ""),
    ("code marks", re.compile(r"`([^`\n]+)`"), r"\1"),
)


def strip_markdown(text: str) -> tuple[str, list[str]]:
    """The text as it is booked, and what was taken off it."""
    removed: list[str] = []
    for what, pattern, keep in _MARKS:
        text, count = pattern.subn(keep, text)
        if count and what not in removed:
            removed.append(what)
    return text, removed


def markdown(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    plain, removed = strip_markdown(draft.body)
    if not removed:
        return [Result("markdown", "ok", "The text is plain.", [], "plain")]
    return [Result("markdown", "warn", f"Booked as plain text, without {', '.join(removed)}. "
                   f"It will read: {plain[:600]}", [], ",".join(removed))]


# --- addresses ------------------------------------------------------------------------------

URL = re.compile(r"\bhttps?://[^\s<>()\"']+|\bwww\.[^\s<>()\"']+", re.I)


def remove_addresses(text: str) -> tuple[str, list[str]]:
    """Text with every web address taken out, and the addresses. Used on what a model
    writes: the code writes addresses, so a model's own never reaches a draft."""
    found = [m.group(0).rstrip(".,;:!?") for m in URL.finditer(text)]
    cleaned = URL.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"(?<! ) +([.,;:!?])", r"\1", cleaned)
    return cleaned.strip(), found


def links(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    """An address in the text that Posts did not write is pointed out."""
    known = {host_of(u) for u in draft.links}
    out = []
    for address in [m.group(0).rstrip(".,;:!?") for m in URL.finditer(draft.body)]:
        target = address if "://" in address else f"https://{address}"
        if host_of(target) not in known:
            out.append(Result("links", "warn", f"The address {address} was not read by Posts. "
                              "Make sure it goes where you mean.", [], address[:80]))
    if not out:
        out.append(Result("links", "ok", "Every address was written by Posts from an article "
                          "it read." if draft.links else "There are no addresses in the "
                          "text.", [], "ok"))
    return out


# --- recycled runs --------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9']+")


def runs(text: str, size: int) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def recycled(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    size, enough = int(cfg.recycled_words), int(cfg.recycled_runs)
    mine = runs(draft.body, size)
    out = []
    for post_id, body in material.recent[:int(cfg.recycled_drafts)]:
        shared = mine & runs(body, size)
        if len(shared) >= enough:
            example = " ".join(sorted(shared)[0])
            out.append(Result("recycled", "warn", f"{len(shared)} runs of {size} words are the "
                              f"same as in post {post_id}, such as “{example}”.",
                              [f"post:{post_id}"], f"post:{post_id}"))
    if not out:
        out.append(Result("recycled", "ok", "No runs of words are recycled from recent "
                          "drafts.", [], "ok"))
    return out
