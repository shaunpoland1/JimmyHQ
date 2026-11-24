"""Reading a source: the article's cleaned text, kept, with its sentences numbered.

The numbers are what evidence points to. A claim in a draft cites sentence numbers; a
check names the sentence a number was found in. Cleaning and splitting are the SDK's
(``jimmyhq_app.text``), the same as Reader's, so the two apps number an article's
sentences the same way while each installs on its own.

A source that cannot be read, or is too short, stops the piece before any model is
asked, with a sentence and a way to try again (``SourceProblem``).
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
    """The source cannot be used. ``str()`` is the sentence for the owner."""


@dataclass(frozen=True)
class Source:
    url: str
    final_url: str
    title: str
    text: str
    sentences: list[str]


def check_url(value: Any) -> str:
    """A pasted link, trimmed: http or https, with a host. Anything else is refused."""
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or " " in url:
        raise SourceProblem("That is not a web address Posts can read. Paste the whole "
                            "link, starting with https://. Nothing was started.")
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
        raise SourceProblem(f"Posts could not read {host_of(url) or 'that page'}: "
                            f"{error.message} Nothing was spent on this piece. Press Retry "
                            "once it can be read, or drop it.") from None
    text = clean("".join(pieces))[:keep_chars]
    if len(text) < min_chars:
        raise SourceProblem(f"The page at {host_of(url)} gave only {len(text)} characters of "
                            f"text, and a source needs at least {min_chars}. It may need a "
                            "sign-in, or be mostly pictures. Nothing was spent on this "
                            "piece. Paste another link, or press Retry.")
    return Source(url=url, final_url=final, title=title, text=text,
                  sentences=split_sentences(text))
