"""The one error web lookup raises."""

from __future__ import annotations

# Why a lookup did not work. The HTTP status a route should answer with.
KINDS: dict[str, int] = {
    "disabled": 409,  # the owner has not switched web lookup on
    "not_configured": 409,  # no search provider chosen, or its address is missing
    "no_key": 409,
    "bad_request": 422,  # a query or address that cannot be used
    "bad_url": 422,
    "refused": 422,  # an address web lookup will not go to
    "not_text": 422,
    "auth": 502,
    "billing": 502,
    "rate_limited": 429,
    "unavailable": 502,
    "unreachable": 502,
    "timeout": 504,
    "http": 502,  # the page answered with an error status
    "redirects": 502,
    "bad_reply": 502,
}


class WebError(Exception):
    """A search or a fetch that did not happen. ``message`` is one sentence
    for the owner (or the model) that says what to do. ``kind`` is one of
    ``KINDS`` and is all a receipt keeps: the sentence may name an address."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    @property
    def status(self) -> int:
        return KINDS.get(self.kind, 502)
