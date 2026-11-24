"""Whether words a model suggested rest on the words it quotes, the same way in every app.

A model is asked for a suggestion and the words it rests on. The quote is looked up in the
source, so it is real; but nothing yet ties the suggestion to the quote, and a model (or text
planted in what it read) can pair a harmless real quote with a different suggestion.
``shares_words`` is the second check: the suggestion's own content words must mostly be in
the quote.

    if not shares_words(item["text"], item["quote"]):
        continue  # a real quote, but not what this suggestion rests on

The rule is simple and the same each time:

- A *content word* is a run of letters or digits of at least ``MIN_WORD`` characters that is
  not on a short list of small words (``the``, ``will``, ``with``...), lower-cased, with a
  plural ``s``, ``es``, ``ing`` or ``ed`` taken off. Numbers count, so "5000" must be in the
  quote. A language written without spaces (Chinese, Japanese, Korean) is read as pairs of
  characters.
- The suggestion passes when at least ``least`` (half, to begin with) of its content words
  are in the quote, and at least one. A suggestion with no content word at all fails: it
  says nothing a quote could back.
"""

from __future__ import annotations

import re

__all__ = ["LEAST", "MIN_WORD", "content_words", "share_in", "shares_words"]

MIN_WORD = 3
LEAST = 0.5
_WORDS = re.compile(r"\w+", re.UNICODE)
_UNSPACED = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]")
_SMALL = frozenset("""
the and for with that this these those will would could should shall can may might must
have has had having are was were been being you your yours our ours his her hers its they
them their theirs but not nor yet also just very too than then there here where when what
which who whom whose why how all any some each every more most much many such own same
other another into onto over under out off from about after before again once only
get got let lets say said says
""".split())


def _stem(word: str) -> str:
    for ending in ("ing", "es", "ed", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= MIN_WORD:
            return word[:-len(ending)]
    return word


def content_words(text: str) -> set[str]:
    """The words of ``text`` that say something, as the rule above defines them."""
    found: set[str] = set()
    for word in _WORDS.findall(str(text or "").casefold()):
        if _UNSPACED.search(word):
            found.update(word[i:i + 2] for i in range(max(1, len(word) - 1)))
        elif len(word) >= MIN_WORD and word not in _SMALL:
            found.add(_stem(word))
    return found


def share_in(text: str, quote: str) -> float:
    """The share (0 to 1) of ``text``'s content words that are in ``quote``; 0 when ``text``
    has none."""
    wanted = content_words(text)
    if not wanted:
        return 0.0
    return len(wanted & content_words(quote)) / len(wanted)


def shares_words(text: str, quote: str, least: float = LEAST) -> bool:
    """Whether at least ``least`` of ``text``'s content words, and at least one, are in
    ``quote``."""
    share = share_in(text, quote)
    return share > 0 and share >= least
