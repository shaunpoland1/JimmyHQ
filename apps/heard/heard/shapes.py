"""Reading what a recorder sends: pure functions, no store, no clock of their own.

Three shapes are understood. Anything else in a message is ignored, and only the fields
named here are ever read: an audio address, a picture, a place or any other field is never
looked at, kept or fetched.

* ``omi``: a finished conversation as Omi's "conversation created" webhook sends it:
  ``{id, started_at, finished_at, structured: {title, overview, category, action_items},
  transcript_segments: [{text, speaker, speaker_id, speaker_name, is_user, start, end}],
  discarded}``. Segment times are seconds from the start of the conversation.
* ``live``: a batch of live transcript, ``{session_id, segments: [...]}`` with segments as
  above. Omi's own real-time webhook sends the list bare and puts the session in the
  address (``?session_id=``); the ``live`` door takes it bare, and ``intake`` wraps it and
  hands the session over as ``session``. Batches of one session are stitched into one
  conversation.
* ``generic``: ``{text, started_ms?, speaker?, id?, session_id?, is_user?}``, one line.

Every scope and every key a message makes is private to the key that sent it
(``namespace``): the sender's own conversation id, session id and line ids are text from
outside, so two keys that happen to send the same ones, or one that sends another's on
purpose, never share a conversation and never make the other's look like a repeat. A
session the sender did not name is the key's own one (``FALLBACK``), and a session id the
sender did name is written ``s:<id>``, so no id the sender sends can be that one. The limit:
a key is known by the label the owner gave it, which is unique among an app's keys; when a key
is removed and another is given the same label, the new key shares what the old one sent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any

from heard import clean

MAX_SEGMENTS = 2000  # in one message
MAX_TEXT = 5000  # one line of speech
MAX_TITLE, MAX_OVERVIEW, MAX_CATEGORY, MAX_NAME, MAX_ID = 200, 3000, 60, 60, 120
MAX_TODO, MAX_TODOS = 300, 20
FALLBACK = "f"  # the session of a key that named none; a named one is "s:<id>"
FIRST_MS = 946_684_800_000  # 2000-01-01: an earlier time is a clock that was never set
DAY_MS = 86_400_000

REFUSALS = {
    "unreadable": "The message could not be read as a transcript.",
    "unknown_shape": ("The message had no transcript_segments, no segments and no text, "
                      "so there was nothing to keep."),
    "bad_segments": "The segments were not a list of lines of speech.",
    "too_many": f"The message held more than {MAX_SEGMENTS} lines of speech.",
    "no_words": "The message had no words in it to keep (audio is never kept).",
    "other_recorder": ("The message came from a recorder id other than the one you chose "
                       "under \"Only accept this recorder id\", so it was not kept."),
    "no_recorder": ("The message carried no recorder id (?uid=), and you chose to accept only "
                    "one id, so it was not kept."),
}


@dataclass(frozen=True)
class Seg:
    speaker: str
    is_user: bool
    text: str
    offset_ms: int  # from the start of the sender's session or conversation
    uid: str
    abs_ms: int | None = None  # when the sender gave a real time (generic); else None
    end_offset_ms: int = 0


@dataclass(frozen=True)
class Parsed:
    source: str  # omi, live or generic
    scope: str  # what a line's uid is unique within
    key: str | None  # a finished conversation's own key; None when stitched
    segments: list[Seg]
    title: str = ""
    overview: str = ""
    category: str = ""
    started_ms: int | None = None  # a finished conversation's own start
    ended_ms: int | None = None  # a finished conversation's own end, when the sender said
    todos: list[str] = field(default_factory=list)
    discarded: bool = False

    @property
    def stitched(self) -> bool:
        return self.key is None


@dataclass(frozen=True)
class Refused:
    kind: str


def namespace(key_name: str) -> str:
    """What is private to one key: a short fixed-length tag made from its label (case does not
    count, as two keys of an app may not differ by case alone). It has no ``:`` in it, so a
    scope written ``kind:tag:id`` reads one way only."""
    return "k" + hashlib.sha256(str(key_name or "").casefold().encode()).hexdigest()[:12]


def make_uid(scope: str, offset_ms: int, text: str, extra: str = "") -> str:
    """A stable id for one line within its scope: the same line sent twice is the same
    id. A speaker's name is not part of it, so renaming a speaker never changes it."""
    raw = f"{scope}\x00{offset_ms}\x00{extra}\x00{text}".encode()
    return hashlib.sha1(raw).hexdigest()[:20]  # noqa: S324 - an identity, not security


def _number_ms(value: float) -> int | None:
    """A number as milliseconds: under 100 billion it is seconds, otherwise milliseconds."""
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return int(value * 1000) if abs(value) < 100_000_000_000 else int(value)


def parse_time(raw: Any, zone: tzinfo, received_ms: int) -> int | None:
    """A time as unix milliseconds: a number (seconds or milliseconds) or an ISO text (with
    no offset it is the owner's own time). None when it is missing, unreadable, before the
    year 2000 or more than a day after ``received_ms``."""
    when: int | None = None
    if isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, (int, float)):
            when = _number_ms(raw)
        elif isinstance(raw, str) and raw.strip():
            text = raw.strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", text):
                when = _number_ms(float(text))
            else:
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=zone)
                when = int(parsed.astimezone(UTC).timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return None
    if when is None or when < FIRST_MS or when > received_ms + DAY_MS:
        return None
    return when


def _seconds_ms(raw: Any) -> int | None:
    """A number of seconds as milliseconds; None when it is not a plain finite number."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw != raw or abs(raw) > 10_000_000:  # NaN, or longer than months
        return None
    return int(raw * 1000)


def speaker_of(raw: dict[str, Any]) -> tuple[str, bool]:
    """The words to show for who spoke, and whether it was the wearer. A name the sender
    gave wins; then "You"; then "Speaker N" from a speaker number; else the label itself."""
    is_user = raw.get("is_user") is True
    for key in ("speaker_name", "speakerName"):
        name = clean.line(raw.get(key), MAX_NAME)
        if name:
            return name, is_user
    if is_user:
        return "You", True
    number = raw.get("speaker_id", raw.get("speakerId"))
    if isinstance(number, int) and not isinstance(number, bool) and 0 <= number < 1000:
        return f"Speaker {number + 1}", False
    label = clean.line(raw.get("speaker"), MAX_NAME)
    numbered = re.fullmatch(r"(?i)speaker[\s_-]*(\d{1,3})", label)
    if numbered:
        return f"Speaker {int(numbered.group(1)) + 1}", False
    return label or "Speaker", False


def _segments(raw: Any, scope: str) -> list[Seg] | Refused:
    if not isinstance(raw, list) or any(not isinstance(s, dict) for s in raw):
        return Refused("bad_segments")
    if len(raw) > MAX_SEGMENTS:
        return Refused("too_many")
    out: list[Seg] = []
    last_end = 0
    for item in raw:
        text = clean.line(item.get("text"), MAX_TEXT)
        if not text:
            continue
        start = _seconds_ms(item.get("start"))
        end = _seconds_ms(item.get("end"))
        start = last_end if start is None or start < 0 else start
        end = start if end is None or end < start else end
        last_end = end
        speaker, is_user = speaker_of(item)
        sender_id = item.get("id")
        extra = f"id:{sender_id}" if isinstance(sender_id, (str, int)) \
            and not isinstance(sender_id, bool) and str(sender_id).strip() else ""
        out.append(Seg(speaker, is_user, text, start, make_uid(scope, start, text, extra),
                       end_offset_ms=end))
    if not out:
        return Refused("no_words")
    return out


def _sender_id(raw: Any) -> str:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return ""
    return clean.line(str(raw), MAX_ID)


def _session(in_body: Any, in_address: str) -> str:
    """The session a message belongs to inside its key: the one the sender named (``s:`` and
    its id), else the key's own (``FALLBACK``)."""
    named = _sender_id(in_body) or in_address
    return f"s:{named}" if named else FALLBACK


def parse(body: Any, received_ms: int, zone: tzinfo, key_name: str = "",
          session: str = "") -> Parsed | Refused:
    """What ``body`` is, or why not. ``session`` is the sender's ``?session_id=``, used for a
    live batch or a single line whose body names no session of its own. Never raises: a body
    of any shape is refused."""
    try:
        return _parse(body, received_ms, zone, key_name, session)
    except Exception:  # noqa: BLE001 - a body of any shape is refused, never raised at
        return Refused("unreadable")


def _parse(body: Any, received_ms: int, zone: tzinfo, key_name: str,
           session: str) -> Parsed | Refused:
    if not isinstance(body, dict):
        return Refused("unreadable")
    if "transcript_segments" in body:
        return _omi(body, received_ms, zone, key_name)
    if "segments" in body:
        return _live(body, received_ms, key_name, session)
    text = body.get("text", body.get("transcript"))
    if isinstance(text, str):
        return _generic(body, text, received_ms, zone, key_name, session)
    return Refused("unknown_shape")


def _omi(body: dict[str, Any], received_ms: int, zone: tzinfo,
         key_name: str) -> Parsed | Refused:
    found = _segments(body["transcript_segments"], "tmp")
    if isinstance(found, Refused):
        return found
    structured = body.get("structured") if isinstance(body.get("structured"), dict) else {}
    top = max(s.end_offset_ms for s in found)
    started = parse_time(body.get("started_at"), zone, received_ms)
    finished = parse_time(body.get("finished_at"), zone, received_ms)
    if started is None:
        started = (finished if finished is not None else received_ms) - top
    sent = _sender_id(body.get("id"))
    mine = namespace(key_name)
    if sent:
        scope = f"omi:{mine}:{sent}"
    else:  # no id: the first words and the start say which conversation this is
        scope = f"omi:{mine}:" + make_uid("", started, found[0].text)
    segments = _segments(body["transcript_segments"], scope)
    assert not isinstance(segments, Refused)  # the same list was accepted a moment ago
    todos: list[str] = []
    items = structured.get("action_items")
    for item in items[:200] if isinstance(items, list) else []:
        said = item.get("description") if isinstance(item, dict) else item
        line = clean.line(said, MAX_TODO)
        if line:
            todos.append(line)
        if len(todos) >= MAX_TODOS:
            break
    return Parsed(
        "omi", scope, scope, segments, title=clean.line(structured.get("title"), MAX_TITLE),
        overview=clean.block(structured.get("overview"), MAX_OVERVIEW),
        category=clean.line(structured.get("category"), MAX_CATEGORY), started_ms=started,
        ended_ms=finished, todos=todos, discarded=body.get("discarded") is True)


def _live(body: dict[str, Any], received_ms: int, key_name: str,
          sent_session: str) -> Parsed | Refused:
    scope = f"live:{namespace(key_name)}:{_session(body.get('session_id'), sent_session)}"
    found = _segments(body["segments"], scope)
    if isinstance(found, Refused):
        return found
    return Parsed("live", scope, None, found)


def _generic(body: dict[str, Any], text: str, received_ms: int, zone: tzinfo,
             key_name: str, sent_session: str) -> Parsed | Refused:
    line = clean.line(text, MAX_TEXT)
    if not line:
        return Refused("no_words")
    scope = f"generic:{namespace(key_name)}:{_session(body.get('session_id'), sent_session)}"
    said = parse_time(body.get("started_ms", body.get("started_at")), zone, received_ms)
    at = said if said is not None else received_ms
    speaker, is_user = speaker_of({"speaker": body.get("speaker"), "is_user": body.get("is_user"),
                                   "speaker_name": body.get("speaker_name")})
    sent = _sender_id(body.get("id"))
    uid = make_uid(scope, 0, "", f"id:{sent}") if sent else make_uid(scope, at, line)
    return Parsed("generic", scope, None,
                  [Seg(speaker, is_user, line, 0, uid, abs_ms=at)])
