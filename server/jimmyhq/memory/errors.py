"""The one error this module raises for something that cannot be done."""

from __future__ import annotations


class LibraryError(Exception):
    """``detail`` is one sentence that says what to do; ``status`` is the HTTP
    status a route should answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
