"""Text in quotation marks must appear word for word in the source or the owner's notes.

A quote with an attribution ("she said", "according to", a colon before it) that is not
found is a question. A longer quoted run with no attribution that is not found is a
warning. Comparison ignores case, the shape of quotation marks and apostrophes, and runs
of spaces; nothing else. The rule is the SDK's (``jimmyhq_app.claims``), the same as
Articles'.
"""

from __future__ import annotations

from typing import Any

from jimmyhq_app.claims import QUOTE as _QUOTE
from jimmyhq_app.claims import in_text, normalise, quote_key, quoted, where_quote

from posts.checks import Draft, Material, Result

__all__ = ["_QUOTE", "exact_quotes", "normalise", "quoted"]


def exact_quotes(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    whole = " ".join(material.sentences)
    out = []
    for span, attributed in quoted(draft.body):
        where = where_quote(span, dict(enumerate(material.sentences, 1)))
        key = quote_key(span)[:80]
        if where or in_text(span, whole):
            out.append(Result("quotes", "ok", f"The quote “{span}” is word for word "
                              "in the source.", where, key))
        elif in_text(span, material.notes):
            out.append(Result("quotes", "ok", f"The quote “{span}” is word for word "
                              "in your notes.", ["notes"], key))
        elif attributed:
            out.append(Result("quotes", "ask", f"Did someone say “{span}”? It is not "
                              "word for word in the source or your notes.", [], key))
        else:
            out.append(Result("quotes", "warn", f"“{span}” is in quotation marks "
                              "but is not word for word in the source or your notes.", [], key))
    return out
