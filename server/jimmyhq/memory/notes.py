"""Reading a note: which lines are facts, and how the rest is cut into passages.

Pure functions over text. No disk, no database, no clock.

**What counts as a fact.** A line that starts in the first column with ``-``,
``*`` or ``+``, then a space or tab, then text with at least one letter or
digit in it, outside a fenced code block, YAML front matter and any HTML comment
that runs over several lines (see ``_classify``). That is all. Indented lines directly
under it (nested bullets, a wrapped sentence) belong to it and travel with it.
Numbered lists, indented bullets, bullets inside a code fence and everything
else are free text: searchable, but with no identity of their own.

**How a fact's id is written.** As a trailing HTML comment on the fact's own
line, ``- Tea, not coffee. <!-- m:01JABC... -->``, which Markdown viewers do
not show. The id is a 26-character ULID.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

_EOL = re.compile(r"\r\n|\n|\r")
_BULLET = re.compile(r"^([-*+][ \t]+)(.*)$")
_ID = re.compile(r"[ \t]*<!--[ \t]*m:([0-9A-HJKMNP-TV-Z]{26})[ \t]*-->([ \t]*)$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\w+")
_INITIALS = re.compile(r"\b(?:\w\.){2,}")  # a.m., U.S.A.
_NUMBER = re.compile(r"\d+")
_COMMENT = re.compile(r"<!--.*?-->")
_YAML_KEY = re.compile(r"[^\s:#\[\]{}>|*&!%@`,-][^:]*:(\s|$)")
FACT_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
BOM = "﻿"


@dataclass
class FactLine:
    index: int  # which line of the note
    end: int  # one past its last continuation line
    prefix: str  # the bullet and the space after it, exactly as typed
    text: str  # the statement, without the id comment
    id: str | None
    trailing: str  # spaces after the text or the comment (two make a Markdown line break)


@dataclass
class Parsed:
    lines: list[list[str]]  # [content, line ending] pairs; joined they are the note exactly
    facts: list[FactLine] = field(default_factory=list)

    def render(self) -> str:
        return "".join(content + eol for content, eol in self.lines)

    @property
    def eol(self) -> str:
        """The line ending this note uses, for lines that are added to it."""
        return next((eol for _, eol in self.lines if eol), "\n")

    def write_fact(self, fact: FactLine) -> None:
        """Put a fact's line back after its id or text changed. Nothing else moves."""
        lead = BOM if fact.index == 0 and self.lines[0][0].startswith(BOM) else ""
        mark = f" <!-- m:{fact.id} -->" if fact.id else ""
        self.lines[fact.index][0] = f"{lead}{fact.prefix}{fact.text}{mark}{fact.trailing}"

    def body(self, fact: FactLine) -> str:
        """A fact as a passage: its statement and whatever is nested under it."""
        rest = [self.lines[i][0].rstrip() for i in range(fact.index + 1, fact.end)]
        return "\n".join([fact.text, *rest])


def _fence(line: str) -> tuple[str, int, bool] | None:
    """A code fence line: its character, how many, and whether anything follows them."""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or stripped[:3] not in ("```", "~~~"):
        return None
    char = stripped[0]
    count = len(stripped) - len(stripped.lstrip(char))
    rest = stripped[count:]
    if char == "`" and "`" in rest:
        return None  # inline code that happens to start the line
    return char, count, bool(rest.strip())


def _front_matter_end(lines: list[list[str]]) -> int:
    """The line that closes YAML front matter, or -1 when the note has none.

    A note may also simply begin with a rule (``---``) and have another further
    down, with facts between them. So the block counts as front matter only if
    it looks like YAML all the way: it starts with a ``key:`` line, and holds
    only such lines, list items, ``#`` comments and indented or blank lines.
    """
    if len(lines) < 2 or lines[0][0].removeprefix(BOM).rstrip() != "---" \
            or not _YAML_KEY.match(lines[1][0]):
        return -1
    for index in range(1, len(lines)):
        line = lines[index][0].rstrip()
        if line in ("---", "..."):
            return index
        if line and line[0] not in " \t#" and not _BULLET.match(line) \
                and not _YAML_KEY.match(line):
            return -1
    return -1


def _classify(lines: list[list[str]]) -> list[tuple[str, str]]:
    """What each line is, and what of it a reader of the rendered note would see.

    - ``front``: YAML front matter (see ``_front_matter_end``). A list in it
      is data for another tool.
    - ``code``: a fence line or a line inside a fenced block. A block opened
      with four marks is not closed by three, nor by a line with a language
      name after the marks.
    - ``comment``: a line that begins inside an HTML comment running over
      several lines, or opens one it does not close.
    - ``text``: everything else.

    None but ``text`` can hold a fact: an id written into any of the others
    would change what the person's file means (a comment would end early, a
    YAML value or a code sample would gain a stray comment).
    """
    out: list[tuple[str, str]] = []
    front_end = _front_matter_end(lines)
    fence: tuple[str, int] | None = None
    in_comment = False
    for index, (content, _) in enumerate(lines):
        if index == 0:
            content = content.removeprefix(BOM)
        if index <= front_end:
            out.append(("front", content))
            continue
        if not in_comment:
            mark = _fence(content)
            if fence is None and mark is not None:
                fence = mark[:2]
                out.append(("code", content))
                continue
            if fence is not None:
                if mark is not None and mark[0] == fence[0] and mark[1] >= fence[1] \
                        and not mark[2]:
                    fence = None
                out.append(("code", content))
                continue
        started_inside = in_comment
        if in_comment:
            close = content.find("-->")
            content = "" if close < 0 else content[close + 3:]
            in_comment = close < 0
        content = _COMMENT.sub("", content)
        start = content.find("<!--")
        if start >= 0:
            content, in_comment = content[:start], True
        out.append(("comment" if started_inside or in_comment else "text", content))
    return out


def parse(text: str) -> Parsed:
    lines: list[list[str]] = []
    pos = 0
    for match in _EOL.finditer(text):
        lines.append([text[pos:match.start()], match.group(0)])
        pos = match.end()
    if pos < len(text):
        lines.append([text[pos:], ""])
    parsed = Parsed(lines)
    current: FactLine | None = None
    for index, (kind, _) in enumerate(_classify(lines)):
        content = lines[index][0]
        if index == 0:
            content = content.removeprefix(BOM)
        indented = content[:1] in (" ", "\t") and content.strip() != ""
        if current is not None:
            if indented:
                current.end = index + 1
            else:
                current = None
        if kind != "text" or indented:
            continue
        bullet = _BULLET.match(content)
        if bullet is None:
            continue
        rest = bullet.group(2)
        found = _ID.search(rest)
        if found:
            statement, fact_id, trailing = rest[:found.start()], found.group(1), found.group(2)
        else:
            statement = rest.rstrip(" \t")
            fact_id, trailing = None, rest[len(statement):]
        if not any(ch.isalnum() for ch in statement):
            continue  # a rule such as "- - -", or an empty bullet
        current = FactLine(index, index + 1, bullet.group(1), statement, fact_id, trailing)
        parsed.facts.append(current)
    return parsed


# --- passages --------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    fact_id: str | None
    heading: str  # the headings above it, "Top > Sub", or ""
    text: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(f"{self.heading}\n{self.text}".encode()).hexdigest()

    @property
    def embed_text(self) -> str:
        return f"{self.heading}\n{self.text}" if self.heading else self.text


def _split_long(paragraph: str, size: int) -> list[str]:
    """Cut a paragraph that is too long, at sentence ends where there are any."""
    out: list[str] = []
    piece = ""
    for sentence in _SENTENCE.split(paragraph):
        while len(sentence) > size:  # one endless sentence: cut at a space if possible
            cut = sentence.rfind(" ", size // 2, size)
            cut = cut if cut > 0 else size
            if piece:
                out.append(piece)
                piece = ""
            out.append(sentence[:cut].rstrip())
            sentence = sentence[cut:].lstrip()
        if piece and len(piece) + 1 + len(sentence) > size:
            out.append(piece)
            piece = sentence
        else:
            piece = f"{piece} {sentence}" if piece else sentence
    if piece:
        out.append(piece)
    return out


def chunks(parsed: Parsed, size: int) -> list[Chunk]:
    """Cut a note into passages: each fact alone, free text by heading and
    paragraph, packed up to ``size`` characters."""
    out: list[Chunk] = []
    trail: list[tuple[int, str]] = []
    paragraphs: list[str] = []
    lines: list[str] = []

    def heading() -> str:
        return " > ".join(name for _, name in trail if name)

    def end_paragraph() -> None:
        if lines:
            paragraphs.append("\n".join(lines))
            lines.clear()

    def end_section() -> None:
        end_paragraph()
        packed = ""
        for paragraph in paragraphs:
            for part in ([paragraph] if len(paragraph) <= size else _split_long(paragraph, size)):
                if packed and len(packed) + 2 + len(part) > size:
                    out.append(Chunk(None, heading(), packed))
                    packed = part
                else:
                    packed = f"{packed}\n\n{part}" if packed else part
        if packed:
            out.append(Chunk(None, heading(), packed))
        paragraphs.clear()

    facts = {f.index: f for f in parsed.facts}
    kinds = _classify(parsed.lines)
    index = 0
    while index < len(parsed.lines):
        fact = facts.get(index)
        if fact is not None:
            end_section()
            body = parsed.body(fact)
            if fact.id:
                out.append(Chunk(fact.id, heading(), body))
            else:
                paragraphs.append(body)  # not yet given an id: plain text for now
            index = fact.end
            continue
        kind, content = kinds[index]
        raw = parsed.lines[index][0]
        index += 1
        if kind in ("code", "front"):
            # Kept as it is, blank lines and all: searchable, never a heading.
            if content.strip() or kind == "code":
                lines.append(content.rstrip())
            else:
                end_paragraph()
            continue
        # An HTML comment is a note to whoever edits the file. It is not shown
        # when the note is rendered, and it is not searched.
        if raw.strip() and not content.strip():
            continue  # a line that was only a comment is not a paragraph break
        title = _HEADING.match(content)
        if title is not None:
            end_section()
            level = len(title.group(1))
            while trail and trail[-1][0] >= level:
                trail.pop()
            trail.append((level, title.group(2).strip()))
        elif not content.strip():
            end_paragraph()
        else:
            lines.append(content.rstrip())
    end_section()
    return [c for c in out if c.text.strip()]


# --- comparing text --------------------------------------------------------------


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _INITIALS.sub(lambda m: m.group(0).replace(".", ""), folded)
    return _WORD.findall(folded)


def normalise(text: str) -> str:
    """Text reduced to its words: case, punctuation and spacing make no difference."""
    return " ".join(words(text))


NEAR_WORDS = 0.85  # share of distinct words two statements must have in common


def near_duplicate(a: str, b: str) -> bool:
    """Whether two statements say the same thing in nearly the same words.

    They must contain the same numbers in the same order ("the budget is
    5000" and "the budget is 6000" are different facts however alike). Then
    they are near-duplicates when, ignoring case and punctuation, they are
    equal, or equal once spaces are ignored too ("10am" and "10 a.m."), or
    one only adds a few words to the other (at least 85% of the distinct words
    are shared). A word swapped for another is never a near-duplicate, however
    long the sentence around it: "the review is every Monday" and "the review
    is every Tuesday" are two facts, and so are "always" and "never". That is
    deliberately cautious, because wrongly refusing a fact loses something
    and keeping a near-repeat only costs a line.
    """
    one, two = normalise(a), normalise(b)
    if not one or not two:
        return False
    if one == two or one.replace(" ", "") == two.replace(" ", ""):
        return True
    if _NUMBER.findall(one) != _NUMBER.findall(two):
        return False
    first, second = set(one.split()), set(two.split())
    if first - second and second - first:
        return False  # each says something the other does not
    return len(first & second) / len(first | second) >= NEAR_WORDS


def title_for(path: str) -> str:
    """A heading for a new note, from its file name."""
    stem = path.rsplit("/", 1)[-1][:-3]
    name = " ".join(re.sub(r"[-_]+", " ", stem).split())
    return (name[:1].upper() + name[1:]) or "Notes"
