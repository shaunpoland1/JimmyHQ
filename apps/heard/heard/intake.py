"""Taking transcripts in. ``shapes`` says what a message is; this keeps it.

A finished conversation (Omi's) is kept once under the sender's id. Live batches and single
lines are stitched: they join the newest conversation of their session that was added to
within the quiet gap (a setting), else start a new one. A line sent twice is recognised and
kept once, whichever shape it came in. A message that cannot be read is counted by the
kind of failure, in a tally the owner can see, and nothing it said is kept.

Every time a message states (when a conversation began, when a line was said) is the sender's
word, so it is kept no later than the moment the message arrived plus a small slack (the
setting ``clock_slack_minutes``): a sender cannot make what it sent look newer than it is, and
so cannot outlast the retention (``Store.trim`` also goes by the time Heard last heard the
conversation, which only Heard sets).

The sender's own ``?uid=`` is the id of its recorder. With the setting "Only accept this
recorder id" filled in, a message that carries another id (or none) is refused before it
is read; otherwise the id is kept with the conversation. ``?session_id=`` says which live
session a batch belongs to. Both come from the address, so they are text from outside.
"""

from __future__ import annotations

import logging
from typing import Any

from heard import clean, shapes
from heard.context import Ctx
from heard.shapes import Parsed
from heard.store import Line

log = logging.getLogger("heard")


def _asked(query: dict[str, Any] | None, name: str) -> str:
    value = (query or {}).get(name)
    return clean.line(value, shapes.MAX_ID) if isinstance(value, str) else ""


def wrong_recorder(ctx: Ctx, recorder: str) -> str | None:
    """Rule ``only_the_chosen_recorder_is_kept``: the kind of refusal when the owner chose
    one recorder id and this message does not carry it, else None."""
    wanted = clean.line(ctx.cfg().only_uid, shapes.MAX_ID)
    if not wanted or recorder == wanted:
        return None
    return "other_recorder" if recorder else "no_recorder"


def accept(ctx: Ctx, body: Any, received_ms: int, key_name: str = "",
           query: dict[str, Any] | None = None) -> str:
    """Keep one message. Returns ``"stored"``, ``"duplicate"``, ``"skipped"`` (the recorder
    marked it discarded) or the kind of refusal. ``query`` is what the sender added to the
    address. Never raises at a body of any shape."""
    recorder = _asked(query, "uid")
    refusal = wrong_recorder(ctx, recorder)
    if refusal is not None:
        ctx.store.refuse(refusal, received_ms)
        return refusal
    found = shapes.parse(body, received_ms, ctx.zone(), key_name,
                         _asked(query, "session_id"))
    if isinstance(found, shapes.Refused):
        ctx.store.refuse(found.kind, received_ms)
        return found.kind
    ctx.store.heard(received_ms)
    if found.discarded and ctx.cfg().skip_discarded:
        return "skipped"
    return _finished(ctx, found, received_ms, recorder) if not found.stitched \
        else _stitched(ctx, found, received_ms, recorder)


def _finished(ctx: Ctx, p: Parsed, received_ms: int, recorder: str) -> str:
    store = ctx.store
    assert p.key is not None and p.started_ms is not None
    if store.by_key(p.key) is not None:
        return "duplicate"
    ceiling = received_ms + ctx.slack_ms()
    started = min(p.started_ms, ceiling)
    lines = [Line(s.uid, s.speaker, s.is_user, s.text, s.offset_ms,
                  min(p.started_ms + s.offset_ms, ceiling),
                  min(p.started_ms + s.end_offset_ms, ceiling))
             for s in p.segments]
    conv_id = store.insert("conversation", {
        "key": p.key, "scope": p.scope, "source": p.source, "title": p.title,
        "overview": p.overview, "category": p.category, "recorder": recorder,
        "started_ms": started, "ended_ms": started, "last_seen_ms": received_ms,
        "created_ms": ctx.now()},
        commit=False)
    store.add_segments(conv_id, p.scope, lines)
    store.fit_times(conv_id)
    # A sender that says when the conversation finished is believed about its length, even when
    # its lines carry no times of their own (rule ``a_senders_own_end_time_counts``).
    if p.ended_ms is not None:
        store.stretch_to(conv_id, min(p.ended_ms, ceiling))
    # What the recorder itself lists as to-dos was never checked against what was said: it is
    # kept, labelled so (``todo.source``), only when the owner's setting says to.
    for text in p.todos if ctx.cfg().keep_recorder_todos else []:
        store.insert("todo", {"conv_id": conv_id, "text": text, "status": "suggested",
                              "source": "sender", "created_ms": ctx.now()}, commit=False)
    store.conn.commit()
    return "stored"


def _stitched(ctx: Ctx, p: Parsed, received_ms: int, recorder: str) -> str:
    store = ctx.store
    fresh = [s for s in p.segments if not store.has_segment(p.scope, s.uid)]
    if not fresh:
        return "duplicate"
    conv = store.open_for(p.scope, received_ms - ctx.gap_ms())
    top = max(s.end_offset_ms for s in fresh)
    base = conv["base_ms"] if conv is not None and conv["base_ms"] is not None \
        else received_ms - top

    ceiling = received_ms + ctx.slack_ms()

    def when(seg: shapes.Seg, offset: int) -> int:
        if seg.abs_ms is not None:
            return min(seg.abs_ms, ceiling)
        return min(base + offset, received_ms)

    lines = [Line(s.uid, s.speaker, s.is_user, s.text, s.offset_ms, when(s, s.offset_ms),
                  when(s, s.end_offset_ms)) for s in fresh]
    if conv is None:
        first = min(line.start_ms for line in lines)
        conv_id = store.insert("conversation", {
            "key": store.free_key(p.scope), "scope": p.scope, "source": p.source,
            "base_ms": base if p.source == "live" else None, "recorder": recorder,
            "started_ms": first, "ended_ms": first, "last_seen_ms": received_ms,
            "created_ms": ctx.now()},
            commit=False)
    else:
        conv_id = conv["id"]
        store.conn.execute("UPDATE conversation SET last_seen_ms=?, "
                           "recorder=COALESCE(NULLIF(recorder, ''), ?) WHERE id=?",
                           (received_ms, recorder, conv_id))
    store.add_segments(conv_id, p.scope, lines)
    store.fit_times(conv_id)
    store.conn.commit()
    return "stored"


def register(ctx: Ctx) -> None:
    """Hang the three handlers on the app's inbound names: ``conversation`` (Omi's finished
    conversation), ``live`` (Omi's real-time list of lines) and ``transcript`` (any other
    sender). They differ in what the door lets through and in the size it expects; what
    is read, kept and refused is the same for all."""

    def take(message: Any) -> None:
        body = message.body
        if isinstance(body, list):  # Omi's real-time webhook sends the lines bare
            body = {"segments": body}
        query = getattr(message, "query", None)
        accept(ctx, body, int(message.received_ms), str(message.key_name or ""),
               query if isinstance(query, dict) else None)

    for name in ("conversation", "live", "transcript"):
        ctx.app.inbound(name)(take)
