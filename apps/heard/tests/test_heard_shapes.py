"""The three shapes a recorder sends are read by code, leniently, and never trusted."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from heard import shapes
from heard_rig import CHAT, PLAN, START, conversation

UTC = ZoneInfo("UTC")


def parse(body, key_name="Pendant"):
    return shapes.parse(body, START, UTC, key_name)


def test_an_omi_conversation_is_read_with_its_title_overview_category_and_times():
    got = parse(conversation())
    assert got.source == "omi" and not got.stitched
    assert got.key == f"omi:{shapes.namespace('Pendant')}:c-100"
    assert (got.title, got.overview, got.category) == (
        "Garden plan", "Talk about the garden.", "personal")
    assert got.started_ms == 1_799_998_200_000
    assert [s.text for s in got.segments][1] == PLAN
    assert [s.offset_ms for s in got.segments] == [0, 4000]


def test_a_speaker_is_named_you_when_it_is_the_wearer_and_otherwise_by_number_or_given_name():
    one = parse(conversation(transcript_segments=[
        {"text": "a", "speaker": "SPEAKER_00", "is_user": True},
        {"text": "b", "speaker": "SPEAKER_02", "speaker_id": 2},
        {"text": "c", "speaker": "SPEAKER_03"},
        {"text": "d", "speaker_name": "Alex"},
        {"text": "e", "speakerId": 4},
        {"text": "f"},
        {"text": "g", "speaker": "Robin"},
    ])).segments
    assert [s.speaker for s in one] == [
        "You", "Speaker 3", "Speaker 4", "Alex", "Speaker 5", "Speaker", "Robin"]
    assert [s.is_user for s in one] == [True] + [False] * 6


def test_unknown_fields_and_extra_segment_fields_are_ignored():
    body = conversation(geolocation={"lat": 1}, photos=["x"], plugins_results=[{"a": 1}])
    body["transcript_segments"] = [{**CHAT[0], "translations": [], "person_id": "p", "zzz": 1}]
    got = parse(body)
    assert isinstance(got, shapes.Parsed) and len(got.segments) == 1


def test_a_conversation_with_no_id_gets_a_stable_one_from_when_and_what_was_said():
    a = parse(conversation(id=None))
    b = parse(conversation(id=None))
    assert a.key == b.key and a.key.startswith("omi:") and len(a.key) > 8
    other = conversation(id=None, started_at="2027-01-15T07:40:00+00:00")
    assert parse(other).key != a.key


def test_action_items_from_the_recorder_become_suggestions_and_are_capped():
    body = conversation()
    body["structured"]["action_items"] = [{"description": "Buy seeds", "completed": False},
                                          {"description": "  "}, "Water the pots"] + \
        [{"description": f"Item {n}"} for n in range(40)]
    todos = parse(body).todos
    assert todos[:2] == ["Buy seeds", "Water the pots"] and len(todos) == shapes.MAX_TODOS


def test_a_missing_start_is_worked_out_from_the_finish_and_then_from_the_arrival():
    no_start = conversation(started_at=None)
    assert parse(no_start).started_ms == 1_799_998_260_000 - 8000
    neither = conversation(started_at=None, finished_at=None)
    assert parse(neither).started_ms == START - 8000


def test_times_may_be_seconds_milliseconds_or_text_and_a_time_with_no_offset_is_the_owners():
    ny = ZoneInfo("America/New_York")
    assert shapes.parse_time(1_800_000_000, UTC, START) == 1_800_000_000_000
    assert shapes.parse_time(1_800_000_000_000, UTC, START) == 1_800_000_000_000
    assert shapes.parse_time("1800000000", UTC, START) == 1_800_000_000_000
    assert shapes.parse_time("2027-01-15T08:00:00Z", UTC, START) == START
    assert shapes.parse_time("2027-01-15T08:00:00", ny, START) == START + 5 * 3_600_000
    for bad in (None, "", "yesterday", True, 12, "1999-01-01T00:00:00Z", float("nan"),
                "2030-01-01T00:00:00Z", []):
        assert shapes.parse_time(bad, UTC, START) is None, bad


def test_a_live_batch_needs_segments_and_takes_the_session_from_session_id_or_the_key():
    live = parse({"session_id": "s1", "segments": CHAT})
    assert live.source == "live" and live.scope == f"live:{shapes.namespace('Pendant')}:s:s1"
    assert live.stitched
    keyed = parse({"segments": CHAT}, key_name="Pendant")
    assert keyed.scope == f"live:{shapes.namespace('Pendant')}:{shapes.FALLBACK}"


def test_a_generic_line_takes_text_with_an_optional_time_speaker_and_id():
    got = parse({"text": "Hello there", "started_ms": START - 1000, "speaker": "Sam", "id": 7})
    assert got.source == "generic" and got.segments[0].speaker == "Sam"
    assert got.segments[0].abs_ms == START - 1000
    again = parse({"text": "Other words", "id": 7, "started_ms": START})
    assert again.segments[0].uid == got.segments[0].uid  # the sender's id says it is the same


def test_a_generic_line_without_an_id_is_the_same_when_its_time_and_words_are():
    one = parse({"text": "Hello", "started_ms": START - 5000})
    two = parse({"text": "Hello", "started_ms": START - 5000})
    other = parse({"text": "Hello", "started_ms": START - 4000})
    assert one.segments[0].uid == two.segments[0].uid != other.segments[0].uid


@pytest.mark.parametrize("body, kind", [
    ({"hello": "world"}, "unknown_shape"),
    ({"transcript_segments": "no"}, "bad_segments"),
    ({"segments": [1, 2]}, "bad_segments"),
    ({"transcript_segments": []}, "no_words"),
    ({"segments": [{"text": "  "}, {"speaker": "x"}]}, "no_words"),
    ({"text": "   "}, "no_words"),
    ({"audio": "AAAA", "audio_url": "https://example.com/a.wav"}, "unknown_shape"),
    ({"transcript_segments": [{"text": "x"}] * 3000}, "too_many"),
    ([1, 2], "unreadable"),
    ("text", "unreadable"),
])
def test_a_message_that_is_not_a_transcript_is_refused_for_a_plain_reason(body, kind):
    assert parse(body) == shapes.Refused(kind)
    assert shapes.REFUSALS[kind].strip()


def test_odd_times_and_numbers_never_raise():
    body = {"transcript_segments": [{"text": "x", "start": "soon", "end": float("inf")},
                                    {"text": "y", "start": -5, "end": 1e30}]}
    got = parse(body)
    assert isinstance(got, shapes.Parsed)
    assert all(s.offset_ms >= 0 and s.end_offset_ms >= s.offset_ms for s in got.segments)


def test_control_characters_are_taken_out_of_what_is_kept_and_long_lines_are_cut():
    body = {"text": "Hel\x00lo\x07 there" + "a" * 9000}
    got = parse(body)
    assert "\x00" not in got.segments[0].text and len(got.segments[0].text) == shapes.MAX_TEXT
