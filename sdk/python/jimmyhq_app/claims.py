"""Finding a draft's numbers and quotations in its sources, the same way in every app.

    from jimmyhq_app.claims import find_numbers, number_matches, where_number, quoted, \
        where_quote

    for number in find_numbers(draft):
        found_in = where_number(number, {(1, 4): "Some 1,240 homes ...", ...}, tolerance_pct=1)
    for span, attributed in quoted(draft):
        found_in = where_quote(span, sentences)

Two rules live here, so two apps that check drafts against sources cannot drift apart:

- ``a number matches its source`` (``number_matches``): the source's number, rounded half up
  to the places the draft writes and at the draft's scale ("1.2 million" is rounded in
  millions), equals it, or the two are within ``tolerance_pct`` percent. Numbers are found
  when written in digits; spelled-out numbers are not. Every number is found: there is no
  cap.
- ``a quotation is word for word`` (``normalise`` and ``where_quote``): case, the shape of
  quotation marks and apostrophes, runs of spaces and the punctuation at either end of
  the quoted span are ignored, and nothing else.

``sentences`` is any mapping from a key the app chooses (a sentence number, or a
``(source, sentence)`` pair) to the sentence's text; the answer is the keys it was found
under, in the mapping's order. Each app words its own results.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["MIN_QUOTE_WORDS", "QUOTE", "SAID", "Number", "find_numbers", "in_text", "normalise",
           "number_matches", "quote_key", "quoted", "where_number", "where_quote"]

_NUMBER = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\.\d)"
    r"(\s*(?:%|percent\b|per cent\b))?"
    r"(?:\s*(thousand|million|billion|trillion|bn|k)\b)?", re.I)
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "billion": 1e9, "bn": 1e9,
          "trillion": 1e12}


@dataclass(frozen=True)
class Number:
    text: str  # as written
    mantissa: Decimal  # the digits, before the scale word
    places: int
    scale: float
    percent: bool

    @property
    def value(self) -> float:
        return float(self.mantissa) * self.scale


def find_numbers(text: str) -> list[Number]:
    """Every number written in digits, in order, with its scale word and percent sign."""
    out = []
    for m in _NUMBER.finditer(text):
        whole, frac, pct, word = m.group(1), m.group(2), m.group(3), m.group(4)
        digits = whole.replace(",", "") + (f".{frac}" if frac else "")
        out.append(Number(text=m.group(0).strip(), mantissa=Decimal(digits),
                          places=len(frac or ""), scale=_SCALE.get((word or "").lower(), 1.0),
                          percent=bool(pct)))
    return out


def number_matches(draft: Number, source: Number, tolerance_pct: float) -> bool:
    """Rule ``a number matches its source``: whether a number in a draft is a fair
    reading of one in the source."""
    at_scale = Decimal(repr(source.value / draft.scale))
    step = Decimal(1).scaleb(-draft.places)
    if at_scale.quantize(step, rounding=ROUND_HALF_UP) == draft.mantissa.quantize(step):
        return True
    if source.value == 0:
        return draft.value == 0
    return abs(draft.value - source.value) <= abs(source.value) * tolerance_pct / 100


def where_number[K: Hashable](number: Number, sentences: Mapping[K, str],
                              tolerance_pct: float) -> list[K]:
    """The keys of the sentences that hold a fair reading of ``number``."""
    return [key for key, text in sentences.items()
            if any(number_matches(number, said, tolerance_pct) for said in find_numbers(text))]


# --- quotations -----------------------------------------------------------------------------

# A quotation holds no opening mark of its own: the search never reads on past one, so a text of
# nothing but opening marks costs no more than its length.
QUOTE = re.compile(r"“([^“”]+)”|\"([^\"]+)\"")
SAID = re.compile(r"\b(said|says|say|wrote|writes|write|told|tells|according to|explained|"
                  r"explains|added|adds|asked|asks|stated|states|argued|argues|put it|"
                  r"noted|notes|warned|warns)\b", re.I)
_ENDS = re.compile(r"(?<![\s.,;:!?\-–—])[\s.,;:!?\-–—]+$|^[\s.,;:!?\-–—]+")
MIN_QUOTE_WORDS = 3


def normalise(text: str) -> str:
    """Text as two quotations are compared: lower case, one shape of quotation mark and
    apostrophe, single spaces."""
    text = (text.replace("‘", "'").replace("’", "'").replace("“", '"')
            .replace("”", '"'))
    return re.sub(r"\s+", " ", text).strip().lower()


def quoted(body: str) -> list[tuple[str, bool]]:
    """Each quoted span of at least ``MIN_QUOTE_WORDS`` words, and whether it is
    attributed (a word such as "said" or "according to" near it, or a colon before it).
    A word or two in quotation marks is usually not a quotation."""
    out = []
    for m in QUOTE.finditer(body):
        span = (m.group(1) or m.group(2) or "").strip()
        if len(span.split()) < MIN_QUOTE_WORDS:
            continue
        around = body[max(0, m.start() - 60):m.start()] + body[m.end():m.end() + 60]
        attributed = bool(SAID.search(around)) or body[:m.start()].rstrip().endswith(":")
        out.append((span, attributed))
    return out


def quote_key(span: str) -> str:
    """The span as quotations are compared, without the punctuation at either end: what
    makes it the same quotation in the next revision."""
    return _ENDS.sub("", normalise(span))


def where_quote[K: Hashable](span: str, sentences: Mapping[K, str]) -> list[K]:
    """Rule ``a quotation is word for word``: the keys of the sentences that hold
    ``span`` word for word. Empty when it is in none of them on its own; a quotation that
    runs across two sentences is found with ``in_text``."""
    wanted = quote_key(span)
    if not wanted:
        return []
    return [key for key, text in sentences.items() if wanted in normalise(text)]


def in_text(span: str, text: str) -> bool:
    """Whether ``span`` is word for word anywhere in ``text``, across sentences too."""
    wanted = quote_key(span)
    return bool(wanted) and wanted in normalise(text)
