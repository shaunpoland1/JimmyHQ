"""Checks on the article's markup, what the sanitiser took out, said plainly, and the
phrases the owner flagged.

The sanitiser (``markup.sanitise``) has already rebuilt the text, so nothing here can
let a link or a tag through; these say what was removed, so the owner knows.
"""

from __future__ import annotations

import re
from typing import Any

from jimmyhq_app.claims import normalise

from articles.checks import Draft, Material, Result
from articles.render import plain_text


def _is_link(note: str) -> bool:
    return "link to" in note or "web address" in note


def links_are_sources(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    """Rule ``a link is a source``: a link to anything but a source never reaches the
    article; what was taken out is reported."""
    out = [Result("links", "warn", f"Taken out: {note}. Links go only to the sources, by "
                  "number, and the code writes their addresses.", [], f"link:{note[:60]}")
           for note in draft.aside if _is_link(note)]
    linked = sorted({int(n) for n in re.findall(r'href="S(\d+)"', draft.body)})
    if not out:
        said = (f"Every link goes to a source ({', '.join(f'S{n}' for n in linked)})."
                if linked else "There are no links but the sources list.")
        out.append(Result("links", "ok", said, [f"S{n}" for n in linked], "ok"))
    return out


def markup_allowed(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    """Rule ``markup is the allowed set``: what else was taken out of the markup."""
    removed = [note for note in draft.aside if not _is_link(note)]
    if not removed:
        return [Result("markup", "ok", "The markup is the allowed set.", [], "ok")]
    return [Result("markup", "warn", f"Taken out of the markup: {'; '.join(removed)}.", [],
                   "removed")]


def owner_flagged(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    """Rule ``a phrase the owner quoted comes back as a question``: a span the owner put
    in quotation marks when sending an article of this site back, found again."""
    text = normalise(f"{draft.title}\n{plain_text(draft.body)}")
    return [Result("flagged", "ask", f"You quoted “{phrase}” when you sent an earlier article "
                   "back, and it is in this one. Is it all right here?", [],
                   f"flagged:{phrase.lower()[:60]}")
            for phrase in material.flagged if normalise(phrase) and normalise(phrase) in text]
