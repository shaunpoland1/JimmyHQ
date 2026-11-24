"""Reading sources: each page's cleaned text, kept, with its sentences numbered.

Sources are numbered S1, S2 and so on in the order the ranking keeps them, and each one's
sentences from 1, so evidence is a pair: (source, sentence), written ``S2:5``. Cleaning
and splitting are the SDK's (``jimmyhq_app.text``), the same as Posts' and Reader's, so
the apps number one page's sentences the same way.

A page that cannot be read, or is too short, is left out with a sentence
(``SourceProblem``); nothing is spent on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from jimmyhq_app import CallError
from jimmyhq_app.text import clean, numbered, split_sentences

__all__ = ["Read", "SourceProblem", "block", "blocks", "cite", "host_of", "normal_url",
           "read", "sentence_map"]


class SourceProblem(Exception):
    """The page cannot be used. ``str()`` is the sentence for the owner."""


@dataclass(frozen=True)
class Read:
    url: str
    final_url: str
    title: str
    text: str
    sentences: list[str]


def host_of(url: str | None) -> str:
    return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")


def normal_url(url: str) -> str:
    """One form for one page, so two searches that find it read it once: no fragment, no
    trailing slash, no ``www.``, lower-case host."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def check_url(value: Any) -> str | None:
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or " " in url:
        return None
    return url


def read(app: Any, url: str, *, min_chars: int, keep_chars: int) -> Read:
    """A public page through the platform's ``web.read``, piece by piece, cleaned and
    split. Raises ``SourceProblem`` with the owner's sentence."""
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
        raise SourceProblem(f"Left out {host_of(url) or 'a page'}: it could not be read "
                            f"({error.message.rstrip('.')}).") from None
    text = clean("".join(pieces))[:keep_chars]
    if len(text) < min_chars:
        raise SourceProblem(f"Left out {host_of(url)}: it gave only {len(text)} characters "
                            f"of text, and a source needs at least {min_chars}.")
    return Read(url=url, final_url=final, title=" ".join(title.split())[:300], text=text,
                sentences=split_sentences(text))


# --- what models are given ------------------------------------------------------------------


def cite(source: int, sentence: int) -> str:
    return f"S{source}:{sentence}"


def sentence_map(sources: list[dict[str, Any]]) -> dict[tuple[int, int], str]:
    """Every kept sentence by (source, sentence)."""
    return {(s["n"], i): text for s in sources for i, text in enumerate(s["sentences"], 1)}


def block(source: dict[str, Any], room: int) -> str:
    """One source as a model is given it: its number, title and host, then its numbered
    sentences, as many as fit in ``room`` characters. Numbers stay the source's own."""
    head = f"Source S{source['n']}: {source['title'] or '(no title)'} ({source['host']})"
    lines: list[str] = []
    used = len(head)
    for line in numbered(source["sentences"]).split("\n"):
        if used + len(line) + 1 > room and lines:
            lines.append("[the rest of this source is left out for room]")
            break
        lines.append(line)
        used += len(line) + 1
    return head + "\n" + "\n".join(lines)


def blocks(sources: list[dict[str, Any]], budget: int) -> list[str]:
    """Each kept source as untrusted text, sharing ``budget`` characters fairly."""
    if not sources:
        return []
    room = max(500, budget // len(sources))
    return [block(s, room) for s in sources]
