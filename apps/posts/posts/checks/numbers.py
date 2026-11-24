"""Every number in a draft must be found in a sentence one of its claims cites, or in the
owner's notes (rule ``number_in_a_cited_sentence``). A number that is in the source but in
no cited sentence is a question too: the claim it belongs to must say where it comes from.

A number matches when the source's number, rounded half up to the places the draft
writes (at the draft's scale: "1.2 million" is rounded in millions), equals it, or when
the two are within ``number_tolerance_pct`` percent. The evidence names each sentence it
was found in. A number found nowhere becomes a question that names no sentence. Every
number is checked: there is no cap that skips later ones.

Numbers are written in digits to be found; spelled-out numbers are not checked. Finding
and matching are the SDK's (``jimmyhq_app.claims``), the same as Articles'.
"""

from __future__ import annotations

from typing import Any

from jimmyhq_app.claims import Number, find_numbers, where_number
from jimmyhq_app.claims import number_matches as matches

from posts.checks import Draft, Material, Result

__all__ = ["Number", "find_numbers", "matches", "numbers"]


def _cited(draft: Draft) -> set[int]:
    return {int(n) for c in draft.claims for n in (c.get("sentences") or [])
            if isinstance(n, int) or str(n).isdigit()}


def numbers(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    tolerance = float(cfg.number_tolerance_pct)
    by_number = dict(enumerate(material.sentences, 1))
    in_notes = find_numbers(material.notes)
    cited = _cited(draft)
    out: list[Result] = []
    written = find_numbers(f"{draft.title}\n{draft.body}")
    for number in written:
        where = where_number(number, by_number, tolerance)
        noted = any(matches(number, s, tolerance) for s in in_notes)
        key = number.text.lower()
        backed = sorted(set(where) & cited)
        if not where and not noted:
            out.append(Result("numbers", "ask", f"Is “{number.text}” right? It is "
                              "not in the source's sentences or in your notes.", [], key))
        elif where and not backed and not noted:
            out.append(Result("numbers", "ask", f"Is “{number.text}” right here? It is in "
                              f"sentence {', '.join(map(str, where))}, and no claim in the "
                              "draft cites that sentence, so nothing ties it to what the "
                              "draft says.", where, key))
        else:
            places = f"sentence {', '.join(map(str, backed))}" if backed else ""
            said = " and ".join(p for p in (places, "your notes" if noted else "") if p)
            out.append(Result("numbers", "ok", f"“{number.text}” is in {said}.",
                              [*backed, *(["notes"] if noted else [])], key))
    if len(written) > int(cfg.many_numbers):
        out.append(Result("numbers", "warn", f"This draft has {len(written)} numbers. Each "
                          "was checked, but that many may be hard to read.", [], "many"))
    if not written:
        out.append(Result("numbers", "ok", "There are no numbers to check.", [], "none"))
    return out
