"""Reading an article: its cleaned text, kept, with its sentences numbered.

The numbers are what a pick or a fact points to: a mined fact is a list of sentence
numbers, and the text Memory gets is those sentences, word for word. Cleaning and
splitting are the SDK's (``jimmyhq_app.text``), the same as Posts', so the two apps
number an article's sentences the same way while each installs on its own.

An article that cannot be read, or is too short, is dropped before anything is spent on
it, with a sentence (``SourceProblem``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from jimmyhq_app import CallError
from jimmyhq_app.text import (
    clean,
    excerpt,
    numbered,
    sentences_page,
    split_sentences,
)

__all__ = ["Source", "SourceProblem", "check_url", "clean", "excerpt", "host_of", "numbered",
           "read", "sentences_page", "split_sentences"]


class SourceProblem(Exception):
    """The article cannot be used. ``str()`` is the sentence for the owner."""


@dataclass(frozen=True)
class Source:
    url: str
    final_url: str
    title: str
    text: str
    sentences: list[str]


def check_url(value: Any) -> str:
    """An address, trimmed: http or https, with a host. Anything else is refused."""
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or " " in url:
        raise SourceProblem("That is not a web address Reader can read. Give the whole "
                            "address, starting with https://. Nothing was read.")
    return url


def host_of(url: str | None) -> str:
    return (urlsplit(url or "").hostname or "").lower()


# --- reading --------------------------------------------------------------------------------


def read(app: Any, url: str, *, min_chars: int, keep_chars: int) -> Source:
    """Read a public page through the platform's ``web.read``, piece by piece, and keep
    its cleaned text. Raises ``SourceProblem`` with the owner's sentence."""
    url = check_url(url)
    pieces: list[str] = []
    start: int | None = 0
    title, final = "", url
    try:
        while start is not None and sum(len(p) for p in pieces) < keep_chars:
            page = app.web.read(url, start=start)
            title, final = page.title or title, page.final_url or final
            pieces.append(page.text)
            start = page.next
    except CallError as error:
        raise SourceProblem(f"Reader could not read {host_of(url) or 'that page'}: "
                            f"{error.message} It was dropped, and nothing was spent on "
                            "it.") from None
    text = clean("".join(pieces))[:keep_chars]
    if len(text) < min_chars:
        raise SourceProblem(f"The page at {host_of(url)} gave only {len(text)} characters of "
                            f"text, and an article needs at least {min_chars}. It may need a "
                            "sign-in, or be mostly pictures. It was dropped, and nothing was "
                            "spent on it.")
    return Source(url=url, final_url=final, title=title.strip()[:300], text=text,
                  sentences=split_sentences(text))
