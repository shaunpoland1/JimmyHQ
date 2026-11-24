"""What the owner taught, as the writer is given it, and the shape of an article.

Three kinds of lesson reach every later ``brief`` for the site (``writing.lessons`` says
each in a line): a decline and its reason, a send-back note, and what the owner changed
in an article before approving it. A span the owner put in quotation marks in a
send-back note is also a phrase the checks flag if it comes back (``quoted_spans``,
``flagged_phrases``, and the check ``owner_flagged``). ``shape`` describes an article so
a writer can see its own habits and vary them.
"""

from __future__ import annotations

import re
from typing import Any

from articles import markup, render
from articles.store import Store

_QUOTED = re.compile(r"“([^“”]+)”|\"([^\"]+)\"|‘([^‘’]+)’")
SPAN_LEAST, SPAN_MOST = 3, 200  # characters a quoted span must have to be a phrase


def quoted_spans(note: str) -> list[str]:
    """Rule ``a quoted span is a phrase to flag``: the spans the owner put in quotation
    marks in a note, as written."""
    out = []
    for found in _QUOTED.finditer(note or ""):
        span = " ".join(next(g for g in found.groups() if g is not None).split())
        if SPAN_LEAST <= len(span) <= SPAN_MOST and span not in out:
            out.append(span)
    return out


def flagged_phrases(store: Store, site_id: int, most: int) -> list[str]:
    """Every span the owner quoted when sending this site's articles back, newest first."""
    out: list[str] = []
    for lesson in store.lessons(site_id, limit=max(most, 1) * 10):
        if lesson["kind"] == "send_back":
            out += [s for s in quoted_spans(lesson["note"]) if s not in out]
    return out[:most]


# --- the shape of an article ----------------------------------------------------------------


def opening(body: str) -> str:
    """The article's first sentence, without its marks."""
    found = markup.claims(body)
    return found[0].text[:160] if found else ""


def opens_with(body: str) -> str:
    first = opening(body)
    if first.endswith("?"):
        return "a question"
    if first[:1] in "\"“'‘":
        return "a quotation"
    if re.search(r"\d", first[:30]):
        return "a figure"
    if re.match(r"(I|We|My|Our|You)\b", first):
        return "a person speaking"
    return "a statement"


def shape(body: str) -> str:
    """\"5 paragraphs under 2 headings, a list, opens with a figure, ends with a
    question, 640 words\"."""
    found = markup.blocks(body)
    paragraphs = sum(1 for tag, _ in found if tag == "p")
    headings = sum(1 for tag, _ in found if tag in ("h2", "h3"))
    lists = len(re.findall(r"<(?:ul|ol)>", body or ""))
    quotes = sum(1 for tag, _ in found if tag == "blockquote")
    last = render.plain_text(body).rstrip()
    ends = "a question" if last.endswith("?") else "a statement"
    parts = [f"{paragraphs} paragraph{'s' if paragraphs != 1 else ''}"
             + (f" under {headings} heading{'s' if headings != 1 else ''}" if headings
                else ", no headings")]
    if lists:
        parts.append(f"{lists} list{'s' if lists != 1 else ''}")
    if quotes:
        parts.append(f"{quotes} block quotation{'s' if quotes != 1 else ''}")
    parts += [f"opens with {opens_with(body)}", f"ends with {ends}",
              f"{markup.word_count(body)} words"]
    return ", ".join(parts)


def past_line(revision: dict[str, Any]) -> str:
    return f"opens “{opening(revision['body'])}”; {shape(revision['body'])}"
