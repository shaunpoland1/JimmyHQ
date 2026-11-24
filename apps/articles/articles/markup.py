"""An article's markup, made safe by the code: the allowed set, links only to sources, and
the citation marks claims carry.

An article is HTML limited to ``ALLOWED``. ``sanitise`` rebuilds it from what a model or
the owner wrote: any other tag is taken off (its words kept; a script's or a style's
content is dropped), every attribute but a link's ``href`` is dropped, and a link stays
only when it points at one of the article's sources by number (``href="S2"``). Every web
address written into the text is taken out too. The code writes the real addresses when
the article is published (``render``), so a made-up link cannot reach it. Each removal is
said, for the checks to report.

Claims carry citations as marks in the text: ``[S2:5]`` is source 2, sentence 5;
``[S2:5,7]``, ``[S2:5-7]`` and ``[S1:3; S4:2]`` cite more than one. ``claims`` reads the
article back as its sentences, each with what it cites.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from jimmyhq_app.text import split_sentences

ALLOWED = frozenset({"p", "h2", "h3", "ul", "ol", "li", "strong", "em", "blockquote", "a",
                     "br"})
BLOCKS = frozenset({"p", "h2", "h3", "li", "blockquote"})
VOID = frozenset({"br"})
DROP_CONTENT = frozenset({"script", "style", "iframe", "object", "embed", "noscript",
                          "template", "svg", "math", "head", "title", "textarea", "select"})
SOURCE_HREF = re.compile(r"^S(\d{1,3})$")
URL = re.compile(r"\bhttps?://[^\s<>()\"']+|\bwww\.[^\s<>()\"']+", re.I)
MARK = re.compile(r"\[(S\d{1,3}:\s*\d{1,5}(?:\s*[-–,;]\s*(?:S\d{1,3}:\s*)?\d{1,5})*)\]")
_PART = re.compile(r"S(\d{1,3}):\s*([\d\s,\-–]+)")
_AFTER_END = re.compile(r"([.!?][\"'”’)]*)\s*((?:\[S\d{1,3}:[^\]]{1,80}\]\s*)+)")
RANGE_MOST = 20  # sentences one range may cover; a wider one cites only its ends


@dataclass
class Clean:
    html: str
    removed: list[str] = field(default_factory=list)  # what was taken out, in words


class _Sanitiser(HTMLParser):
    def __init__(self, sources: set[int]):
        super().__init__(convert_charrefs=True)
        self.sources = sources
        self.out: list[str] = []
        self.open: list[str] = []  # allowed tags open, innermost last
        self.dropping = 0
        self.removed: list[str] = []
        self.unwrapped_links = 0

    def _note(self, what: str) -> None:
        if what not in self.removed:
            self.removed.append(what)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in DROP_CONTENT:
            self.dropping += 1
            self._note(f"a <{tag}> and what was in it")
            return
        if self.dropping:
            return
        if tag not in ALLOWED:
            self._note(f"<{tag}> tags (their words were kept)")
            return
        kept_attrs = ""
        for name, _value in attrs:
            if tag == "a" and name.lower() == "href":
                continue
            self._note(f"the \"{name.lower()}\" attribute")
        if tag == "a":
            href = next((v or "" for n, v in attrs if n.lower() == "href"), "")
            found = SOURCE_HREF.match(href.strip())
            if not found or int(found.group(1)) not in self.sources:
                self._note(f"a link to {href.strip()[:80] or 'nowhere'}, which is not one of "
                           "the sources (its words were kept)")
                self.open.append("a-unwrapped")
                return
            kept_attrs = f' href="S{int(found.group(1))}"'
        if tag in VOID:
            self.out.append(f"<{tag}>")
            return
        self.out.append(f"<{tag}{kept_attrs}>")
        self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in DROP_CONTENT:
            self._note(f"a <{tag.lower()}>")
            return
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID and tag.lower() in ALLOWED and self.open \
                and self.open[-1] == tag.lower():
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_CONTENT:
            self.dropping = max(0, self.dropping - 1)
            return
        if self.dropping or tag not in ALLOWED or tag in VOID:
            return
        wanted = "a-unwrapped" if tag == "a" and "a" not in self.open else tag
        if wanted not in self.open:
            return  # a stray closing tag
        while self.open:
            last = self.open.pop()
            if last != "a-unwrapped":
                self.out.append(f"</{last}>")
            if last == wanted:
                break

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        self._note("comments")

    def finish(self) -> str:
        self.close()
        while self.open:
            last = self.open.pop()
            if last != "a-unwrapped":
                self.out.append(f"</{last}>")
        return "".join(self.out)


def remove_addresses(text: str) -> tuple[str, list[str]]:
    """Text with every web address taken out, and the addresses."""
    found = [m.group(0).rstrip(".,;:!?") for m in URL.finditer(text)]
    cleaned = URL.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"(?<! ) +([.,;:!?])", r"\1", cleaned)
    return cleaned, found


def sanitise(body: str, sources: set[int]) -> Clean:
    """Rule ``markup is the allowed set``: the article rebuilt from the allowed tags, its
    links only to sources by number, with no web address in its words."""
    parser = _Sanitiser(sources)
    parser.feed(body or "")
    rebuilt = parser.finish()
    # Addresses in the words, never in a tag: the only href left is "S<n>".
    pieces = re.split(r"(<[^>]*>)", rebuilt)
    taken: list[str] = []
    for i, piece in enumerate(pieces):
        if piece.startswith("<"):
            continue
        pieces[i], found = remove_addresses(piece)
        taken += found
    out = "".join(pieces).strip()
    removed = list(parser.removed)
    if taken:
        removed.append(f"{len(taken)} web address{'es' if len(taken) != 1 else ''} written "
                       "into the text")
    if out and not re.match(r"\s*<(p|h2|h3|ul|ol|blockquote)\b", out):
        out = f"<p>{out}</p>"  # bare text becomes a paragraph
    return Clean(out, removed)


def to_html(text: str) -> str:
    """Plain text the owner wrote, as paragraphs (a blank line between them)."""
    return "".join(f"<p>{html.escape(p.strip(), quote=False)}</p>"
                   for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip())


# --- reading it back ------------------------------------------------------------------------


class _Blocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []  # (tag, text)
        self.current: list[str] = []
        self.tag = "p"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCKS:
            self._flush()
            self.tag = tag
        elif tag == "br":
            self.current.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        self.current.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self.current).split())
        if text:
            self.blocks.append((self.tag, text))
        self.current = []
        self.tag = "p"


def blocks(body: str) -> list[tuple[str, str]]:
    """Each block of the article as (tag, its words), in order."""
    reader = _Blocks()
    reader.feed(body or "")
    reader.close()
    reader._flush()  # noqa: SLF001
    return reader.blocks


def parse_mark(inner: str) -> list[tuple[int, int]]:
    """The (source, sentence) pairs one mark cites."""
    out: list[tuple[int, int]] = []
    for source, numbers in _PART.findall(inner):
        for part in re.split(r"(?<!\s)\s*,\s*", numbers.strip(" ,")):
            if not part:
                continue
            ends = [int(x) for x in re.split(r"(?<!\s)\s*[-–]\s*", part) if x.strip().isdigit()]
            if len(ends) == 2 and ends[0] <= ends[1] and ends[1] - ends[0] < RANGE_MOST:
                out += [(int(source), n) for n in range(ends[0], ends[1] + 1)]
            else:
                out += [(int(source), n) for n in ends]
    return list(dict.fromkeys(out))


def strip_marks(text: str) -> str:
    text = MARK.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"(?<! ) +([.,;:!?])", r"\1", text).strip()


@dataclass(frozen=True)
class Claim:
    index: int  # its place in the article, from 1
    block: str  # the block's tag: p, h2, li ...
    text: str  # the sentence without its marks
    cites: tuple[tuple[int, int], ...]  # what it cites, valid or not


def claims(body: str) -> list[Claim]:
    """The article's sentences, each with what it cites. A mark written after a sentence's
    full stop belongs to that sentence, not the next."""
    out: list[Claim] = []
    for tag, text in blocks(body):
        text = _AFTER_END.sub(lambda m: f" {m.group(2).strip()}{m.group(1)} ", text)
        for sentence in split_sentences(" ".join(text.split())):
            cited: list[tuple[int, int]] = []
            for mark in MARK.finditer(sentence):
                cited += parse_mark(mark.group(1))
            plain = strip_marks(sentence)
            if plain:
                out.append(Claim(len(out) + 1, tag, plain, tuple(dict.fromkeys(cited))))
    return out


def word_count(body: str) -> int:
    return sum(len(strip_marks(text).split()) for _, text in blocks(body))
