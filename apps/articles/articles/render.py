"""The article as it is published: clean HTML, or Markdown, written by the code.

The citation marks come out of the text (or become numbers, as the owner chose in
``cite_style``), each ``href="S2"`` becomes source 2's own address, and a Sources list is
added at the end from the sources the article actually cited, in the order it first cites
them. A source that was read but never cited is not listed.
"""

from __future__ import annotations

import html
import re
from typing import Any

from articles import markup


def cited_order(body: str, sources: dict[int, dict[str, Any]]) -> list[int]:
    """The sources the article cites or links to, in the order it first does."""
    order: list[int] = []
    for found in re.finditer(r'href="S(\d+)"|' + markup.MARK.pattern, body):
        if found.group(1):
            numbers = [int(found.group(1))]
        else:
            numbers = [s for s, _ in markup.parse_mark(found.group(2))]
        for n in numbers:
            if n in sources and n not in order:
                order.append(n)
    return order


def final_html(title: str, body: str, sources: dict[int, dict[str, Any]], *,
               cite_style: str = "list", with_title: bool = True) -> str:
    """The article as clean HTML, ready to paste or send. ``with_title`` puts the title
    first as a heading; a site that takes the title on its own (WordPress) leaves it out."""
    order = cited_order(body, sources)
    place = {n: i for i, n in enumerate(order, 1)}

    def mark(found: re.Match[str]) -> str:
        if cite_style != "numbers":
            return ""
        numbers = sorted({place[s] for s, _ in markup.parse_mark(found.group(1)) if s in place})
        return f" [{', '.join(map(str, numbers))}]" if numbers else ""

    text = re.sub(r"(?<!\s)\s*" + markup.MARK.pattern, mark, body)
    text = re.sub(r"(?<!\s)\s+([.,;:!?])", r"\1", text)
    text = re.sub(r'href="S(\d+)"', lambda m: f'href="{html.escape(_url(sources, m), quote=True)}"',
                  text)
    out = [f"<h1>{html.escape(title, quote=False)}</h1>"] if with_title else []
    out.append(text)
    if order:
        items = []
        for n in order:
            source = sources[n]
            name = html.escape(source["title"] or source["host"], quote=False)
            items.append(f'<li><a href="{html.escape(source["final_url"] or source["url"])}">'
                         f"{name}</a> ({html.escape(source['host'], quote=False)})</li>")
        out.append("<h2>Sources</h2><ol>" + "".join(items) + "</ol>")
    return "\n".join(out)


def _url(sources: dict[int, dict[str, Any]], found: re.Match[str]) -> str:
    source = sources.get(int(found.group(1)))
    return (source["final_url"] or source["url"]) if source else ""


_MD = (
    (re.compile(r"<h1>(.*?)</h1>", re.S), r"# \1\n\n"),
    (re.compile(r"<h2>(.*?)</h2>", re.S), r"## \1\n\n"),
    (re.compile(r"<h3>(.*?)</h3>", re.S), r"### \1\n\n"),
    (re.compile(r"<strong>(.*?)</strong>", re.S), r"**\1**"),
    (re.compile(r"<em>(.*?)</em>", re.S), r"*\1*"),
    (re.compile(r'<a href="([^"]*)">(.*?)</a>', re.S), r"[\2](\1)"),
    (re.compile(r"<br>"), "  \n"),
    (re.compile(r"<blockquote>(.*?)</blockquote>", re.S), lambda m: "\n".join(
        f"> {line}" for line in m.group(1).strip().split("\n")) + "\n\n"),
    (re.compile(r"<p>(.*?)</p>", re.S), r"\1\n\n"),
)


def to_markdown(clean_html: str) -> str:
    """The published HTML as Markdown. Only the allowed set ever reaches here."""
    text = clean_html
    text = re.sub(r"<ol>(.*?)</ol>", lambda m: _items(m.group(1), True) + "\n", text, flags=re.S)
    text = re.sub(r"<ul>(.*?)</ul>", lambda m: _items(m.group(1), False) + "\n", text,
                  flags=re.S)
    for pattern, repl in _MD:
        text = pattern.sub(repl, text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _items(inner: str, ordered: bool) -> str:
    found = re.findall(r"<li>(.*?)</li>", inner, flags=re.S)
    return "\n".join(f"{n}. {item}" if ordered else f"- {item}"
                     for n, item in enumerate(found, 1)) + "\n"


def plain_text(body: str) -> str:
    """The article's words, without marks or tags, for a card or a comparison."""
    return "\n\n".join(markup.strip_marks(text) for _, text in markup.blocks(body))
