"""What the owner taught, as the writer's brief carries it, and the shape of a draft.

Three kinds of lesson reach every later brief for the page: a decline and its reason, a
send-back note (its quoted spans are also flagged by the checks), and the difference
between a draft and the owner's own edit of it. ``shape`` describes a draft so a writer
can see its own habits and vary them.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from posts.sources import split_sentences
from posts.store import Store

CHANGE_WORDS = 12  # words of each side shown for one change
CHANGES_PER_EDIT = 3


def record_edit(store: Store, post: dict[str, Any], after: str) -> int | None:
    """The owner changed a draft's text: kept, to show the writer what they changed."""
    before = post["body"]
    if before.strip() == after.strip():
        return None
    return store.insert("edit", {"page_id": post["page_id"], "post_id": post["id"],
                                 "before": before[:5000], "after": after[:5000],
                                 "created": store.now()})


def _cut(words: list[str]) -> str:
    text = " ".join(words[:CHANGE_WORDS])
    return text + (" ..." if len(words) > CHANGE_WORDS else "")


def changes(before: str, after: str, most: int = CHANGES_PER_EDIT) -> list[str]:
    """The owner's changes in plain words: took out, added, changed this to that."""
    old, new = before.split(), after.split()
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(a=old, b=new,
                                                      autojunk=False).get_opcodes():
        if op == "replace":
            out.append(f"changed “{_cut(old[a1:a2])}” to “{_cut(new[b1:b2])}”")
        elif op == "delete":
            out.append(f"took out “{_cut(old[a1:a2])}”")
        elif op == "insert":
            out.append(f"added “{_cut(new[b1:b2])}”")
    return out[:most]


def for_brief(store: Store, page_id: int, most: int) -> list[str]:
    """The owner's latest lessons for the page, newest first, one line each."""
    if most <= 0:
        return []
    found: list[tuple[int, str]] = []
    for lesson in store.lessons(page_id, limit=most):
        if lesson["kind"] == "decline":
            line = f"Declined ({lesson['reason'] or 'no reason listed'}): {lesson['note']}"
        else:
            line = f"Sent back: {lesson['note']}"
        found.append((lesson["created"], line.strip()))
    for edit in store.all("SELECT * FROM edit WHERE page_id=? ORDER BY id DESC LIMIT ?",
                          (page_id, most)):
        said = "; ".join(changes(edit["before"], edit["after"]))
        if said:
            found.append((edit["created"], f"Edited a draft before approving it: {said}."))
    found.sort(key=lambda pair: -pair[0])
    return [line[:400] for _, line in found[:most]]


def quoted_phrases(store: Store, page_id: int) -> list[str]:
    """The spans the owner quoted in send-back notes: flagged if a draft says them again."""
    return list(dict.fromkeys(q for lesson in store.lessons(page_id, limit=200)
                              if lesson["kind"] == "send_back" for q in lesson["quotes"]))


# --- the shape of a draft -------------------------------------------------------------------

_NUMBER = re.compile(r"\d")


def first_sentence(body: str) -> str:
    found = split_sentences(body.strip())
    return (found[0] if found else body.strip())[:140]


def opens_with(body: str) -> str:
    first = first_sentence(body)
    if first.endswith("?"):
        return "a question"
    if first[:1] in "\"“'‘":
        return "a quotation"
    if _NUMBER.search(first[:25]):
        return "a number"
    if re.match(r"(I|We|My|Our)\b", first):
        return "the first person"
    return "a statement"


def shape(body: str) -> str:
    """\"3 paragraphs, opens with a number, ends with a question, 84 words\"."""
    text = body.strip()
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    listed = sum(1 for line in text.splitlines() if re.match(r"\s*([-*•]|\d+[.)])\s", line))
    last = text.rstrip().splitlines()[-1].strip() if text else ""
    ends = ("a question" if last.endswith("?") else "tags" if last.startswith("#")
            else "a statement")
    parts = [f"{len(paragraphs)} paragraph{'s' if len(paragraphs) != 1 else ''}",
             f"opens with {opens_with(text)}", f"ends with {ends}"]
    if listed:
        parts.append(f"a list of {listed}")
    parts.append(f"{len(text.split())} words")
    return ", ".join(parts)
