"""The checks on what an article claims, each against the sentences its claims cite.

- ``cites_are_real``: a citation must name a sentence one of the sources has.
- ``numbers_in_cited_sentences``: every number, however many there are, must be in a
  sentence its own sentence cites (a heading's or the title's: any sentence the article
  cites), as the SDK's rule ``a number matches its source`` reads it, within
  ``number_tolerance_pct``. In a source but not a cited sentence, or in none, it is a
  question.
- ``exact_quotes``: a quotation with an attribution must be word for word in a source
  (the SDK's rule ``a quotation is word for word``); without one it is a warning.
- ``named_in_cited_sentences``: a person or organisation the article says said or did
  something must be named in a sentence that sentence cites.
- ``uncited_claims``: a sentence that states a figure, a quotation or what someone said
  or found, and cites nothing, is a question.
- ``advice_as_fact``: a sentence that tells readers what to do about health, the law or
  money (the owner's word lists) is a question for the owner.
- ``weak_sources_cited``: citing a source on a weak host is a warning.
"""

from __future__ import annotations

import re
from typing import Any

from jimmyhq_app.claims import find_numbers, in_text, quote_key, quoted, where_number, where_quote

from articles.checks import Draft, Material, Result, mark, marks
from articles.markup import Claim

ACTION = re.compile(r"\b(said|says|say|wrote|writes|told|tells|according to|explained|"
                    r"added|stated|states|argued|argues|noted|notes|warned|warns|found|"
                    r"finds|reported|reports|showed|shows|announced|announces|estimated|"
                    r"estimates|published|publishes|concluded|concludes|recommended|"
                    r"recommends|discovered|surveyed|measured|launched|claimed|claims)\b", re.I)
DIRECTIVE = re.compile(r"\b(you should|you must|you need to|you have to|be sure to|make sure|"
                       r"always|never|it is safe|it's safe|is safe to|we recommend|is "
                       r"recommended|do not|don't|avoid|stop taking|start taking)\b", re.I)
HEADINGS = ("h2", "h3")
_TOKEN = re.compile(r"[A-Za-z][\w'’.\-]*")
_JOINERS = {"of", "for", "and", "the", "de", "du", "van", "von", "la", "le", "on"}
_TITLES = {"dr", "mr", "mrs", "ms", "prof", "sir", "st"}
_COMMON = {
    "the", "a", "an", "in", "on", "at", "this", "that", "these", "those", "it", "its", "our",
    "we", "you", "your", "they", "their", "he", "she", "i", "but", "and", "or", "so", "for",
    "if", "when", "while", "according", "after", "before", "since", "most", "many", "some",
    "one", "two", "three", "last", "next", "every", "each", "there", "here", "what", "why",
    "how", "who", "as", "by", "with", "from", "to", "of", "researchers", "experts", "studies",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january",
    "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december",
}


def _valid(claim: Claim, material: Material) -> list[tuple[int, int]]:
    every = material.sentences()
    return [c for c in claim.cites if c in every]


def _key(prefix: str, claim: Claim) -> str:
    return f"{prefix}@{' '.join(claim.text.lower().split())[:60]}"


def _short(text: str, most: int = 140) -> str:
    return text if len(text) <= most else text[:most - 1].rstrip() + "…"


def is_claim(text: str) -> bool:
    """A sentence states a fact the reader may want to check: a figure, a quotation, or
    what someone said, found or did."""
    return bool(find_numbers(text) or quoted(text) or ACTION.search(text))


# --- the checks -----------------------------------------------------------------------------


def cites_are_real(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    every = material.sentences()
    out = []
    for claim in draft.claims():
        wrong = [c for c in claim.cites if c not in every]
        if wrong:
            out.append(Result("cites", "warn", f"“{_short(claim.text)}” cites {marks(wrong)}, "
                              "which no source has. That citation counts for nothing.",
                              [], _key("cites", claim)))
    return out


def numbers_in_cited_sentences(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    tolerance = float(cfg.number_tolerance_pct)
    every = material.sentences()
    found = draft.claims()
    all_cited = [c for claim in found for c in _valid(claim, material)]
    title = Claim(0, "h1", draft.title, ())
    out: list[Result] = []
    for claim in [title, *found]:
        cited = all_cited if claim.block in (*HEADINGS, "h1") else _valid(claim, material)
        cited_text = material.text_of(cited)
        for number in find_numbers(claim.text):
            key = _key(number.text.lower(), claim)
            backed = where_number(number, cited_text, tolerance)
            if backed:
                out.append(Result("numbers", "ok", f"“{number.text}” is in "
                                  f"{marks(backed)}.", [mark(c) for c in backed], key))
                continue
            elsewhere = where_number(number, every, tolerance)
            if elsewhere:
                out.append(Result("numbers", "ask", f"Is “{number.text}” right in "
                                  f"“{_short(claim.text)}”? It is in {marks(elsewhere[:5])}, "
                                  "which this sentence does not cite, so nothing ties it to "
                                  "what the article says.", [mark(c) for c in elsewhere[:5]],
                                  key))
            else:
                out.append(Result("numbers", "ask", f"Is “{number.text}” right in "
                                  f"“{_short(claim.text)}”? It is in no sentence of any "
                                  "source.", [], key))
    return out


def exact_quotes(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    every = material.sentences()
    out = []
    for claim in draft.claims():
        for span, attributed in quoted(claim.text):
            key = quote_key(span)[:80]
            where = where_quote(span, every) or [
                (n, 0) for n, s in material.sources.items() if in_text(span, s["text"])]
            if where:
                out.append(Result("quotes", "ok", f"The quotation “{span}” is word for word "
                                  f"in {marks(where[:3]).replace(':0', '')}.",
                                  [mark(c).replace(":0", "") for c in where[:3]], key))
            elif attributed:
                out.append(Result("quotes", "ask", f"Did someone say “{span}”? It is not word "
                                  "for word in any source.", [], key))
            else:
                out.append(Result("quotes", "warn", f"“{span}” is in quotation marks but is "
                                  "not word for word in any source.", [], key))
    return out


def names(text: str) -> list[str]:
    """The names in a sentence: runs of capitalised words, joined by small words such as
    "of", without a sentence-opening common word."""
    tokens = [(m.group(0), m.start()) for m in _TOKEN.finditer(text)]
    runs: list[list[str]] = []
    current: list[str] = []
    for word, _ in tokens:
        bare = word.strip(".'’-")
        if bare[:1].isupper():
            current.append(bare)
        elif current and bare.lower() in _JOINERS:
            current.append(bare)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    out = []
    for run in runs:
        while run and (run[-1].lower() in _JOINERS):
            run = run[:-1]
        words = [w for w in run if w.lower() not in _COMMON and w.lower() not in _TITLES
                 and w.lower() not in _JOINERS and len(w) >= 2]
        if words:
            out.append(" ".join(words))
    return out


def named_in_cited_sentences(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    out = []
    for claim in draft.claims():
        cited = _valid(claim, material)
        if not cited or not ACTION.search(claim.text):
            continue
        cited_text = " ".join(material.text_of(cited).values()).lower()
        for name in names(claim.text):
            key = _key(f"name:{name.lower()}", claim)
            missing = [w for w in name.split()
                       if not re.search(rf"\b{re.escape(w.lower())}\b", cited_text)]
            if missing:
                out.append(Result("names", "ask", f"Is it right that {name} said or did what "
                                  f"“{_short(claim.text)}” says? {name} is not named in "
                                  f"{marks(cited)}, which it cites.",
                                  [mark(c) for c in cited], key))
            else:
                out.append(Result("names", "ok", f"{name} is named in {marks(cited)}.",
                                  [mark(c) for c in cited], key))
    return out


def uncited_claims(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    out = []
    for claim in draft.claims():
        if claim.block in HEADINGS or _valid(claim, material) or not is_claim(claim.text):
            continue
        out.append(Result("uncited", "ask", f"“{_short(claim.text)}” states a fact and cites "
                          "nothing. Which source says it?", [], _key("uncited", claim)))
    return out


def advice_topic(text: str, cfg: Any) -> str | None:
    """Which of health, the law or money a sentence gives advice about, or None."""
    if not DIRECTIVE.search(text):
        return None
    low = text.lower()
    for topic, words in (("health", cfg.advice_health), ("the law", cfg.advice_law),
                         ("money", cfg.advice_money)):
        for word in words or []:
            word = str(word).strip().lower()
            if word and re.search(rf"\b{re.escape(word)}", low):
                return topic
    return None


def advice_as_fact(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    out = []
    for claim in draft.claims():
        topic = advice_topic(claim.text, cfg)
        if topic:
            out.append(Result("advice", "ask", f"“{_short(claim.text)}” tells readers what to "
                              f"do about {topic}, as fact. Do you want it said that way? It "
                              "may be safer as what a source says, or with a pointer to a "
                              "professional.", [mark(c) for c in _valid(claim, material)],
                              _key("advice", claim)))
    return out


def weak_sources_cited(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    used = {c[0] for claim in draft.claims() for c in _valid(claim, material)}
    return [Result("sources", "warn", f"S{n} is on {material.sources[n]['host']}, which you "
                   "count as weak. Look for where it got its facts.", [f"S{n}"], f"weak:S{n}")
            for n in sorted(used) if material.sources[n].get("weak")]
