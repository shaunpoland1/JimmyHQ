"""Checks the owner set: the sponsor tag, their own rules and tags, and the passages they
quoted when they sent a draft back."""

from __future__ import annotations

import re
from typing import Any

from posts.checks import Draft, Material, Result

_SPONSOR_WORDS = re.compile(r"\b(sponsored|paid partnership|in partnership with|"
                            r"affiliate link|promo code|#sponsored|#ad)\b", re.I)


def sponsor(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    tag = str(cfg.sponsor_tag or "").strip()
    if draft.sponsored:
        if tag and tag.lower() not in draft.body.lower():
            return [Result("sponsor", "warn", f"This piece is marked sponsored, and “{tag}"
                           "” is not in the text: it will be added at the end when it is "
                           "booked.", [], "missing")]
        return [Result("sponsor", "ok", "Marked sponsored, and the tag is there.", [], "ok")]
    said = _SPONSOR_WORDS.search(draft.body)
    if said:
        return [Result("sponsor", "ask", f"Is this sponsored? It says “{said.group(0)}"
                       "”, and the piece is not marked sponsored.", [], "unmarked")]
    return [Result("sponsor", "ok", "Not sponsored.", [], "ok")]


def _has(body: str, text: str) -> bool:
    return text.strip().lower() in body.lower()


def owner_rules(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    body = f"{draft.title}\n{draft.body}"
    out = []
    tags = list(dict.fromkeys([*material.page_tags,
                               *(r["text"] for r in material.rules if r["kind"] == "tag")]))
    for tag in tags:
        if not _has(body, tag):
            out.append(Result("rules", "warn", f"The brand's posts carry “{tag}”, and "
                              "this one does not.", [], f"tag:{tag}"))
    for rule in material.rules:
        says = f" {rule['says']}" if rule.get("says") else ""
        if rule["kind"] == "phrase" and _has(body, rule["text"]):
            out.append(Result("rules", "warn", f"It says “{rule['text']}”, a phrase "
                              f"you asked to be flagged.{says}", [], f"rule:{rule['id']}"))
        elif rule["kind"] == "question":
            out.append(Result("rules", "ask", f"{rule['text']}{says}", [],
                              f"rule:{rule['id']}"))
    if not out:
        out.append(Result("rules", "ok", "Your rules and tags are kept.", [], "ok"))
    return out


def send_back_quotes(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    out = []
    for quote in dict.fromkeys(material.send_back_quotes):
        if quote and _has(draft.body, quote):
            out.append(Result("send_back", "ask", f"You quoted “{quote}” when you "
                              "sent a draft back, and it is here again. Keep it?", [],
                              quote.lower()[:80]))
    return out
