"""Turning what a server sent into text a model can read.

Pure functions: bytes in, text out, no network. HTML becomes Markdown (or
plain text) with everything a reader never sees taken out first: scripts,
styles, comments, forms, frames, and elements hidden with ``hidden``,
``aria-hidden`` or an inline style. Hidden text is where a page puts words
meant for a model and not for a person, so it is not passed on. That is a
filter, not a guarantee: a page can hide text in ways this does not catch,
which is why what comes out is still treated as untrusted (see docs/lookup.md).
"""

from __future__ import annotations

import codecs
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment
from markdownify import MarkdownConverter

from jimmyhq.lookup.errors import WebError

FORMATS = ("markdown", "text", "html")
MAX_TITLE = 300
MAX_LINKS = 2000

_TEXT_TYPES = ("application/json", "application/xml", "application/xhtml+xml",
               "application/javascript", "application/x-ndjson", "application/yaml",
               "application/x-yaml", "application/toml", "application/rss+xml",
               "application/atom+xml", "application/ld+json")
_HTML_TYPES = ("text/html", "application/xhtml+xml")
# Never part of what a person reads on the page.
_DROP = ("script", "style", "noscript", "template", "svg", "canvas", "iframe", "frame",
         "object", "embed", "form", "button", "input", "select", "textarea", "dialog",
         "nav", "footer", "aside", "head")
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d])|opacity\s*:\s*0"
    r"(?![.\d])", re.IGNORECASE)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""",
                           re.IGNORECASE)
_BLANK_RUN = re.compile(r"\n{3,}")
_LOOKS_HTML = re.compile(rb"^\s*(?:<!doctype\s+html|<html[\s>])", re.IGNORECASE)


def media_type(content_type: str | None) -> tuple[str, str | None]:
    """``("text/html", "utf-8")`` from a Content-Type header's value."""
    if not content_type:
        return "", None
    kind, _, rest = content_type.partition(";")
    charset = None
    for param in rest.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip("\"'")
    return kind.strip().lower(), charset


def is_text(kind: str) -> bool:
    """Whether a media type is one web lookup reads. No type at all is let
    through to be judged by its bytes."""
    return (not kind or kind.startswith("text/") or kind in _TEXT_TYPES
            or kind.endswith(("+json", "+xml")))


def decode(body: bytes, kind: str, charset: str | None) -> str:
    """The body as text, or a refusal when it is not text at all."""
    for mark, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
                       (codecs.BOM_UTF16_BE, "utf-16")):
        if body.startswith(mark):
            charset = name
            break
    else:
        if charset is None and (kind in _HTML_TYPES or not kind):
            found = _META_CHARSET.search(body[:4096])
            charset = found.group(1).decode("ascii", "replace") if found else None
    try:
        codec = codecs.lookup(charset or "utf-8").name
    except LookupError:
        codec = "utf-8"
    text = body.decode(codec, errors="replace")
    if "\x00" in text:
        raise WebError("not_text", "That address holds a file, not a page of text, so it "
                                   "was not read.")
    return text


def _web_link(href: object, base: str) -> str | None:
    if not isinstance(href, str) or not href.strip():
        return None
    try:
        absolute = urljoin(base, href.strip())
        parts = urlsplit(absolute)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return absolute.split("#", 1)[0]


def _clean(soup: BeautifulSoup, base: str) -> list[str]:
    """Remove what a reader never sees, make links absolute, and return them."""
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(_DROP):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        style = tag.get("style")
        if (tag.has_attr("hidden") or str(tag.get("aria-hidden", "")).lower() == "true"
                or (isinstance(style, str) and _HIDDEN_STYLE.search(style))):
            tag.decompose()
    links: list[str] = []
    for anchor in soup.find_all("a"):
        link = _web_link(anchor.get("href"), base)
        if link is None:
            anchor.unwrap()  # keep the words, drop a link that leads nowhere readable
            continue
        anchor["href"] = link
        if "title" in anchor.attrs:
            del anchor["title"]
        if len(links) < MAX_LINKS and link not in links:
            links.append(link)
    return links


def _tidy(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n")
             .split("\n")]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def read(body: bytes, content_type: str | None, fmt: str, base: str
         ) -> tuple[str, str, tuple[str, ...]]:
    """``(title, text, links)`` for a body. ``links`` are the web addresses an
    HTML page links to, absolute and in page order. May take a moment for a
    large page: call it off the event loop."""
    kind, charset = media_type(content_type)
    source = decode(body, kind, charset)
    if not (kind in _HTML_TYPES or (not kind and _LOOKS_HTML.match(body[:512]))):
        return "", _tidy(source), ()
    soup = BeautifulSoup(source, "html.parser")
    title = " ".join(soup.title.get_text().split())[:MAX_TITLE] if soup.title else ""
    if fmt == "html":
        return title, source, ()
    links = _clean(soup, base)
    root = soup.body or soup
    # "text" is the same reading with the link addresses left out.
    strip = ["img", "a"] if fmt == "text" else ["img"]
    converter = MarkdownConverter(heading_style="ATX", bullets="-", strip=strip)
    return title, _tidy(converter.convert_soup(root)), tuple(links)
