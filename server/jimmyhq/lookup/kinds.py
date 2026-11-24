"""Event kinds for web lookup: one receipt per search and one per page read.

A receipt says that something left the machine, where it went, how much came
back and how long it took. It never holds what a page or a result said. What
was asked (the query, the URL, the host) is there only when the call was not
private; a private call, such as one made for a conversation kept off the
record, leaves a keyed hash in its place.
"""

from __future__ import annotations

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind


@kind("lookup.searched", example={
    "caller": "chat.reply", "provider": "searxng", "ok": True, "error_kind": None,
    "results": 6, "chars": 1840, "duration_ms": 412, "private": False,
    "query": "when do broad beans go in", "query_hash": None,
})
class Searched(Payload):
    caller: str
    provider: str
    ok: bool
    error_kind: str | None = None  # why it failed, as a word; the sentence is not kept
    results: int = Field(ge=0)
    chars: int = Field(ge=0)  # of titles, snippets and text handed back
    duration_ms: int = Field(ge=0)
    private: bool
    query: str | None = None  # only when not private
    query_hash: str | None = None  # only when private


@kind("lookup.fetched", example={
    "caller": "chat.reply", "ok": True, "error_kind": None, "status": 200,
    "content_type": "text/html", "bytes": 48213, "chars": 9120, "truncated": False,
    "hops": 1, "duration_ms": 655, "private": False, "host": "example.com",
    "url": "https://example.com/growing/broad-beans", "url_hash": None,
})
class Fetched(Payload):
    caller: str
    ok: bool
    error_kind: str | None = None
    status: int | None = None  # the last HTTP status seen, if a server answered
    content_type: str | None = None
    bytes: int = Field(ge=0)  # read from the network, after decompression
    chars: int = Field(ge=0)  # of the text made from them
    truncated: bool = False  # the page was larger than the size limit
    hops: int = Field(ge=0)  # requests made: 1, plus one per redirect followed
    duration_ms: int = Field(ge=0)
    private: bool
    host: str | None = None  # of the address asked for; only when not private
    url: str | None = None  # the address asked for; only when not private
    url_hash: str | None = None  # only when private
