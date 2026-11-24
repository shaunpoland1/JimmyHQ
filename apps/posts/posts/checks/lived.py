"""A first-person story the owner did not tell is a question, never a block.

A draft may say "I planted twelve trees last March" only if the owner's notes for that
piece say so. The check is deterministic and conservative: it looks for a first person
("I", or "we") followed by a past-tense verb of doing from a fixed list, a few set
phrases ("when I was", "in my experience", "I used to"), and "I have been ...ing". It
leaves alone:

- opinions and feelings ("I think", "I love"), which are not stories;
- anything inside quotation marks (the quote check looks at those);
- a question, and a clause after "if", "imagine", "suppose" and the like;
- words that are the source's own;
- a story whose words are mostly in the owner's notes for the piece
  (``lived_notes_share``).
"""

from __future__ import annotations

import re
from typing import Any

from posts.checks import Draft, Material, Result
from posts.checks.quotes import _QUOTE, normalise
from posts.sources import split_sentences

# Past-tense verbs of doing something, the kind a story is told with.
DOING = ("visited", "went", "tried", "planted", "built", "grew", "grown", "cooked", "baked",
         "made", "bought", "installed", "met", "spent", "took", "walked", "hiked", "drove",
         "flew", "travelled", "traveled", "stayed", "picked", "harvested", "fixed",
         "repaired", "painted", "learned", "learnt", "started", "finished", "lost", "found",
         "joined", "attended", "swam", "moved", "watched", "tested", "used", "saw", "seen",
         "tasted", "ate", "talked", "spoke", "interviewed", "ran", "climbed", "camped", "sold",
         "raised", "adopted", "volunteered", "lived", "done", "been to", "opened", "planned",
         "switched", "signed up", "paid", "fitted", "replaced", "noticed")
# "We" says these as often about everyone as about the owner ("we have seen prices rise").
GENERIC_WE = frozenset({"seen", "saw", "found", "learned", "learnt", "used", "done", "made",
                        "noticed", "lived"})
_VERB = "|".join(sorted((re.escape(v).replace(r"\ ", r"\s+") for v in DOING),
                        key=len, reverse=True))
_STORY = re.compile(
    r"\b(?P<who>I|[Ww]e)(?:\s+have|\s+had|'ve|'d)?\s+"
    r"(?:(?:just|finally|once|recently|actually|also|first|never|even|already)\s+)?"
    rf"(?P<verb>{_VERB})\b")
_BEEN = re.compile(r"\b(?P<who>I|[Ww]e)(?:\s+have|'ve)\s+been\s+[a-z]+ing\b")
_PHRASE = re.compile(r"(?:\b[Ww]hen I was\b|\b[Ii]n my (?:own )?experience\b|\bhappened to "
                     r"me\b|\bI remember\b|\bI used to\b)")
_IF = re.compile(r"\b(if|unless|imagine|suppose|supposing|whether|wish|pretend)\s+(\w+\s+)?$",
                 re.I)
_WORD = re.compile(r"[a-z]+")
COMMON = frozenset({
    "about", "after", "again", "also", "already", "before", "been", "being", "could", "does",
    "done", "each", "even", "every", "finally", "first", "from", "have", "here", "into",
    "just", "last", "like", "made", "many", "more", "most", "much", "never", "once", "only",
    "other", "ours", "over", "really", "recently", "same", "some", "such", "than", "that",
    "their", "them", "then", "there", "these", "they", "this", "those", "through", "today",
    "very", "were", "what", "when", "where", "which", "while", "will", "with", "would",
    "year", "years", "month", "months", "week", "weeks", "ago", "yesterday", "your"})


def _plain(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def _unquoted(body: str) -> str:
    """The body with every quoted span blanked out, keeping its length."""
    return _QUOTE.sub(lambda m: " " * len(m.group(0)), body)


def stem(word: str) -> str:
    for ending in ("ing", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


def words_of(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(text.lower()) if len(w) >= 4 and w not in COMMON}


def story_in(sentence: str) -> str | None:
    """The words that tell ``sentence`` as something the writer did, or None. Rule
    ``first_person_story``."""
    text = _plain(sentence).strip()
    if not text or text.endswith("?"):
        return None
    for pattern in (_STORY, _BEEN, _PHRASE):
        for match in pattern.finditer(text):
            if _IF.search(text[:match.start()]):
                continue  # a supposition, not a story
            who = (match.groupdict().get("who") or "I").lower()
            verb = re.sub(r"\s+", " ", (match.groupdict().get("verb") or "")).lower()
            if who == "we" and verb in GENERIC_WE:
                continue
            return match.group(0)
    return None


def in_notes(sentence: str, notes: str, share: float) -> bool:
    """Rule ``story_from_the_notes``: enough of the story's own words are in the notes."""
    mine = words_of(sentence)
    if not notes.strip():
        return False
    if not mine:
        return True  # nothing in it but the telling: the notes speak of something
    return len(mine & words_of(notes)) / len(mine) >= share


def lived_experience(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    share = float(cfg.lived_notes_share)
    source = normalise(" ".join(material.sentences))
    out: list[Result] = []
    for sentence in split_sentences(_unquoted(draft.body)):
        told = story_in(sentence)
        if told is None:
            continue
        said = normalise(sentence)
        if said and said in source:
            continue  # the source's own words
        if in_notes(sentence, material.notes, share):
            out.append(Result("lived", "ok", f"“{sentence.strip()}” is in your notes for "
                              "this piece.", ["notes"], said[:80]))
            continue
        out.append(Result("lived", "ask", f"Did this happen? “{sentence.strip()}” tells it "
                          "as something you did, and your notes for this piece do not say so.",
                          [], said[:80]))
    if not out:
        out.append(Result("lived", "ok", "No story is told as yours that needs your word.",
                          [], "none"))
    return out
