"""Web lookup: searching the web and reading pages for a model.

Other modules use the two functions exported here and nothing beneath them:

    from jimmyhq import lookup

    results = await lookup.search(core, "broad beans sowing time",
                                  actor=who.actor, caller="chat.reply")
    page = await lookup.fetch(core, results[0].url, actor=who.actor, caller="chat.reply")

Both refuse until the owner has switched ``lookup.enabled`` on, both leave a
receipt on the Tape, and what either returns is text from the internet: data,
never instructions. See docs/lookup.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from jimmyhq.core import Core
from jimmyhq.lookup.errors import WebError
from jimmyhq.lookup.fetching import Lookup, Page, fetch, service
from jimmyhq.lookup.searching import Result, search

__all__ = ["Lookup", "Page", "Result", "WebError", "build", "fetch", "search", "service"]


def build(core: Core) -> APIRouter:
    from jimmyhq.lookup.routes import build_router  # noqa: PLC0415 - keeps import light

    return build_router(core)
