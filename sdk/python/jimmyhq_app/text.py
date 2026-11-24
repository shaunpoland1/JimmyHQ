"""Article text as apps keep it: cleaned prose, split into numbered sentences.

    from jimmyhq_app.text import clean, split_sentences, numbered

    sentences = split_sentences(clean(app.web.read_all(url)))
    prompt_part = numbered(sentences)          # "[1] First sentence.\\n[2] ..."

The numbers are what evidence points to: a claim cites sentence numbers, a fact is a
list of them. So the splitter is stable: the same text always gives the same sentences,
in the same order, numbered from 1, in every app that uses it. Two apps that read the
same article number its sentences the same way.
"""

from __future__ import annotations

import re

__all__ = ["ABBREVIATIONS", "clean", "excerpt", "numbered", "sentences_page",
           "split_sentences"]

# --- cleaning -------------------------------------------------------------------------------

# Page text is text from outside, so no pattern here may take time in proportion to the square
# of a long run of the same character (a page of "[[[[..." or of blank lines): each one either
# begins only where a run begins, stays within one line, or is bounded. Links and emphasis
# longer than their bound are left as they are.
_IMAGE = re.compile(r"!\[[^\[\]\n]{0,500}\]\([^()\s]{0,2000}\)")
_LINK = re.compile(r"\[([^\[\]\n]{0,500})\]\([^()\s]{0,2000}\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.{1,1000}?)(?<=\S)\1")
_BULLET = re.compile(r"^[^\S\n]*([-*+]|\d+[.)])[^\S\n]+", re.M)
_QUOTE_MARK = re.compile(r"^[^\S\n]*>[^\S\n]?", re.M)
_SPACES = re.compile(r"[ \t ]+")


def clean(text: str) -> str:
    """Page text as plain prose: Markdown marks taken off, spaces tidied, paragraphs
    kept as blank lines. Line endings of any platform become ``\\n``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _QUOTE_MARK.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    paragraphs, current = [], []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


# --- sentences ------------------------------------------------------------------------------

# Words that end in a full stop without ending a sentence.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "inc",
    "ltd", "co", "corp", "no", "fig", "approx", "est", "dept", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "u.s", "u.k", "a.m", "p.m",
})
_END = re.compile(r"(?<![.!?])([.!?]+)([\"'”’)\]]*)\s+(?=[\"'“‘(\[]?[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    """The sentences of cleaned text, in order. A paragraph break always ends one; a
    full stop ends one unless it closes an abbreviation, an initial or a decimal."""
    out: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        start = 0
        for match in _END.finditer(paragraph):
            before = paragraph[start:match.start()].rsplit(" ", 1)[-1]
            word = before.lower().rstrip(".")
            if match.group(1) == "." and (word in ABBREVIATIONS
                                          or (len(word) == 1 and word.isalpha())):
                continue
            piece = paragraph[start:match.end()].strip()
            if piece:
                out.append(piece)
            start = match.end()
        rest = paragraph[start:].strip()
        if rest:
            out.append(rest)
    return out


def numbered(sentences: list[str], start: int = 1) -> str:
    """Sentences as a model or an agent is given them: ``[n] sentence``, one to a line."""
    return "\n".join(f"[{n}] {s}" for n, s in enumerate(sentences, start))


def sentences_page(sentences: list[str], start: int, room: int) -> tuple[str, int | None]:
    """Numbered sentences from ``start`` that fit in ``room`` characters, and the number
    the next page starts at (None at the end)."""
    lines, used, n = [], 0, max(1, start)
    total = len(sentences)
    while n <= total:
        line = f"[{n}] {sentences[n - 1]}"
        if lines and used + len(line) + 1 > room:
            break
        lines.append(line[:room])
        used += len(line) + 1
        n += 1
    return "\n".join(lines), (n if n <= total else None)


def excerpt(sentences: list[str], count: int) -> str:
    """The opening of an article, as plain text."""
    return " ".join(sentences[:max(0, count)])
