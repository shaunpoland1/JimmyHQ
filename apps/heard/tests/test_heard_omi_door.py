"""What each of Heard's three inbound names takes, as an Omi pendant posts it straight to
the address (no relay): a finished conversation as one object, real-time lines as a bare
list with the session and the recorder in the address, and any other sender's own shapes.
Every body and address here is invented."""

from __future__ import annotations

import json
import sqlite3

import pytest
from heard import shapes, store
from heard_rig import APP_ROOT, CHAT, HOUR, MINUTE, START, Rig, conversation
from jimmyhq_app.testing import FakeCore

NAMES = ("conversation", "live", "transcript")
AUDIO_URL = "https://audio.example.com/recordings/q9.wav"
AUDIO_B64 = "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA="
LIVE = [  # as Omi's real-time webhook sends it: camelCase speaker id, no names
    {"text": "I think we should prioritise the mobile app.", "speaker": "SPEAKER_00",
     "speakerId": 0, "is_user": False, "start": 10.0, "end": 15.0},
    {"text": "Agreed, let us start with the phone.", "speaker": "SPEAKER_01",
     "speakerId": 1, "is_user": True, "start": 16.0, "end": 18.0},
]


def kept(rig) -> list[dict]:
    return rig.call("days")["conversations"]


def refused(rig) -> dict[str, int]:
    return {r["kind"]: r["n"] for r in rig.call("overview")["refused"]}


def texts(rig, conv_id=None) -> list[str]:
    return [s["text"] for s in rig.call("conversation", id=conv_id or rig.first_id())["segments"]]


# --- a finished conversation: the object, with the recorder in the address ----------------


def test_a_finished_conversation_posted_with_the_recorder_id_in_the_address_is_kept_with_it(rig):
    rig.send(conversation(), name="conversation", query={"uid": "rec-a1"})
    [row] = kept(rig)
    got = rig.call("conversation", id=row["id"])
    assert got["recorder"] == "rec-a1" and row["heading"] == "Garden plan"
    assert got["segments"][0]["speaker"] == "Speaker 2"  # camelCase and snake ids both read


def test_a_finished_conversation_with_no_address_parameters_is_kept_with_no_recorder(rig):
    rig.send(conversation(), name="conversation")
    assert rig.call("conversation", id=rig.first_id())["recorder"] == ""


def test_a_finished_conversation_posted_again_is_kept_once_whatever_the_retry_carries(rig):
    rig.send(conversation(), name="conversation", query={"uid": "rec-a1"})
    rig.clock.advance(MINUTE)
    rig.send(conversation(), name="conversation", query={"uid": "rec-a1"})
    rig.send(conversation(), name="conversation")
    rig.send(conversation(), name="transcript")
    [row] = kept(rig)
    assert row["lines"] == 2


def test_omis_camel_case_speaker_ids_and_its_extra_fields_are_read_leniently(rig):
    body = conversation(created_at="2027-01-15T07:31:05+00:00", apps_response=[],
                        folder_id="f1", folder_name="Work")
    body["structured"]["events"] = []
    body["transcript_segments"] = [{"text": "Let us discuss the timeline.",
                                    "speaker": "SPEAKER_00", "speakerId": 0,
                                    "speaker_name": "Alex", "is_user": False,
                                    "start": 10.0, "end": 15.0}]
    rig.send(body, name="conversation", query={"uid": "rec-a1"})
    got = rig.call("conversation", id=rig.first_id())
    assert got["segments"][0]["speaker"] == "Alex"
    assert [s["start_ms"] for s in got["segments"]] == [1_799_998_200_000 + 10_000]


# --- real-time lines: a bare list, with the session in the address ------------------------


def test_real_time_lines_posted_as_a_bare_list_join_the_session_named_in_the_address(rig):
    rig.send(LIVE[:1], name="live", query={"session_id": "s-77", "uid": "rec-a1"})
    rig.clock.advance(MINUTE)
    rig.send(LIVE[1:], name="live", query={"session_id": "s-77", "uid": "rec-a1"})
    [row] = kept(rig)
    assert row["lines"] == 2 and row["open"] is True
    assert rig.call("conversation", id=row["id"])["recorder"] == "rec-a1"


def test_two_sessions_in_the_address_are_two_conversations(rig):
    rig.send(LIVE[:1], name="live", query={"session_id": "s-1"})
    rig.send(LIVE[1:], name="live", query={"session_id": "s-2"})
    assert len(kept(rig)) == 2


def test_a_bare_list_with_no_session_in_the_address_is_told_apart_by_the_keys_name(rig):
    rig.send(LIVE[:1], name="live", key_name="Pendant")
    rig.send(LIVE[1:], name="live", key_name="Spare")
    assert len(kept(rig)) == 2


def test_a_bare_list_posted_again_adds_nothing_even_after_the_conversation_closed(rig):
    rig.send(LIVE, name="live", query={"session_id": "s-77"})
    rig.clock.advance(HOUR)
    rig.send(LIVE, name="live", query={"session_id": "s-77"})
    rig.send(LIVE[:1], name="live", query={"session_id": "s-77"})
    [row] = kept(rig)
    assert row["lines"] == 2


def test_lines_resent_inside_a_longer_list_keep_only_the_new_ones(rig):
    rig.send(LIVE[:1], name="live", query={"session_id": "s-77"})
    rig.clock.advance(MINUTE)
    rig.send(LIVE, name="live", query={"session_id": "s-77"})
    assert texts(rig) == [LIVE[0]["text"], LIVE[1]["text"]]


def test_the_live_name_also_takes_the_object_form_with_its_own_session(rig):
    rig.send({"session_id": "s-9", "segments": LIVE}, name="live", query={"session_id": "other"})
    rig.send({"segments": LIVE[:1]}, name="live", query={"session_id": "s-9"})
    [row] = kept(rig)  # the body's own session wins; the second batch is the same line
    assert row["lines"] == 2


def test_an_empty_list_or_a_list_of_things_that_are_not_lines_is_refused_and_counted(rig):
    rig.send([], name="live", query={"session_id": "s-77"})
    rig.send(["words", 3], name="live", query={"session_id": "s-77"})
    rig.send([{"text": ""}, {"nothing": 1}], name="live")
    assert kept(rig) == []
    assert refused(rig) == {"no_words": 2, "bad_segments": 1}


def test_a_list_is_taken_only_where_the_manifest_says_so():
    plain = FakeCore("heard", permissions=["inbound"])
    with pytest.raises(Exception, match="array"):
        plain.inbound("conversation", [{"text": "x"}])


def test_live_lines_are_placed_in_time_from_when_the_batch_arrived(rig):
    rig.send(LIVE, name="live", query={"session_id": "s-77"})
    got = rig.call("conversation", id=rig.first_id())["segments"]
    assert got[-1]["end_ms"] == START and got[0]["start_ms"] == START - 8_000


# --- the generic name still works as it did, and now also hears the address ---------------


def test_the_generic_name_still_takes_all_three_shapes_as_before(rig):
    rig.send(conversation(), name="transcript")
    rig.send({"session_id": "s-1", "segments": CHAT[:1]}, name="transcript")
    rig.send({"text": "Remember the milk", "id": "n-1"}, name="transcript")
    assert len(kept(rig)) == 3


def test_a_single_line_with_a_session_in_the_address_joins_that_session(rig):
    rig.send({"text": "First line"}, query={"session_id": "s-5"}, key_name="Phone")
    rig.send({"text": "Second line"}, query={"session_id": "s-5"}, key_name="Phone")
    assert len(kept(rig)) == 1


def test_the_same_pendant_may_use_all_three_addresses_and_each_is_kept_by_its_own_rules(rig):
    rig.send(conversation(), name="conversation", query={"uid": "rec-a1"})
    rig.send(LIVE, name="live", query={"uid": "rec-a1", "session_id": "s-1"})
    rig.send({"text": "A note"}, name="transcript", query={"uid": "rec-a1"})
    assert len(kept(rig)) == 3


# --- the recorder id ----------------------------------------------------------------------


def only(make_rig, uid="rec-a1") -> Rig:
    return make_rig(settings={"only_uid": uid})


@pytest.mark.parametrize("name", NAMES)
def test_with_a_recorder_chosen_a_message_carrying_another_id_is_refused_and_keeps_nothing(
        make_rig, name):
    rig = only(make_rig)
    body = LIVE if name == "live" else conversation()
    rig.send(body, name=name, query={"uid": "rec-zz", "session_id": "s-1"})
    assert kept(rig) == []
    assert refused(rig) == {"other_recorder": 1}
    assert LIVE[0]["text"] not in repr(rig.call("overview"))


@pytest.mark.parametrize("name", NAMES)
def test_with_a_recorder_chosen_a_message_with_no_id_is_refused_too(make_rig, name):
    rig = only(make_rig)
    body = LIVE if name == "live" else conversation()
    rig.send(body, name=name)
    assert kept(rig) == [] and refused(rig) == {"no_recorder": 1}


def test_with_a_recorder_chosen_the_same_id_is_kept_and_the_spaces_round_it_do_not_matter(
        make_rig):
    rig = only(make_rig, "  rec-a1 ")
    rig.send(conversation(), name="conversation", query={"uid": "rec-a1"})
    rig.send(LIVE, name="live", query={"uid": "rec-a1", "session_id": "s-1"})
    assert len(kept(rig)) == 2 and refused(rig) == {}


def test_an_id_that_differs_only_in_case_is_another_recorder(make_rig):
    rig = only(make_rig, "Rec-A1")
    rig.send(conversation(), name="conversation", query={"uid": "rec-a1"})
    assert kept(rig) == [] and refused(rig) == {"other_recorder": 1}


def test_an_id_written_in_the_body_instead_of_the_address_does_not_count(make_rig):
    rig = only(make_rig)
    rig.send(conversation(uid="rec-a1"), name="conversation")
    assert kept(rig) == [] and refused(rig) == {"no_recorder": 1}


def test_without_a_chosen_recorder_every_id_is_kept_and_stored_with_its_conversation(rig):
    rig.send(conversation(id="a"), name="conversation", query={"uid": "rec-a1"})
    rig.send(conversation(id="b"), name="conversation", query={"uid": "rec-b2"})
    got = {rig.call("conversation", id=r["id"])["recorder"] for r in kept(rig)}
    assert got == {"rec-a1", "rec-b2"}


def test_a_refusal_for_the_wrong_recorder_is_counted_in_words_the_owner_can_act_on(make_rig):
    rig = only(make_rig)
    rig.send(conversation(), name="conversation", query={"uid": "rec-zz"})
    rig.send(conversation(), name="conversation")
    said = {r["kind"]: r["words"] for r in rig.call("overview")["refused"]}
    assert "Only accept this recorder id" in said["other_recorder"]
    assert "?uid=" in said["no_recorder"]
    for words in said.values():
        assert "rec-zz" not in words


def test_the_overview_says_whether_one_recorder_is_chosen_without_saying_which(make_rig):
    assert only(make_rig).call("overview")["only_recorder"] is True
    assert "rec-a1" not in repr(only(make_rig).call("overview"))
    assert make_rig().call("overview")["only_recorder"] is False


def test_a_recorder_id_is_kept_as_one_short_line_of_text_whatever_the_address_carried(rig):
    rig.send(conversation(), name="conversation",
             query={"uid": "rec\n\x00-a1 " + "x" * 400})
    stored = rig.call("conversation", id=rig.first_id())["recorder"]
    assert "\n" not in stored and "\x00" not in stored
    assert len(stored) <= shapes.MAX_ID and stored.startswith("rec -a1 x")


def test_a_message_that_is_refused_for_its_shape_keeps_no_recorder_either(rig):
    rig.send({"nothing": 1}, name="conversation", query={"uid": "rec-a1"})
    assert kept(rig) == [] and "rec-a1" not in repr(rig.ctx.store.all("SELECT * FROM meta"))


def test_a_conversation_the_recorder_discarded_is_still_skipped_with_an_id_in_the_address(rig):
    rig.send(conversation(discarded=True), name="conversation", query={"uid": "rec-a1"})
    assert kept(rig) == []


# --- audio is never taken from any name ---------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_audio_fields_in_any_shape_at_any_name_are_ignored_and_nothing_is_fetched(rig, name):
    noisy = [{**seg, "audio_url": AUDIO_URL, "audio": AUDIO_B64, "pcm": AUDIO_B64}
             for seg in LIVE]
    if name == "live":
        rig.send(noisy, name=name, query={"session_id": "s-1", "sample_rate": "16000"})
    else:
        rig.send(conversation(audio_url=AUDIO_URL, audio_base64=AUDIO_B64,
                              transcript_segments=noisy), name=name,
                 query={"sample_rate": "16000", "uid": "rec-a1"})
    rows = repr([rig.ctx.store.all(f"SELECT * FROM {t}")  # noqa: S608
                 for t in ("conversation", "segment", "meta", "refused")])
    assert AUDIO_URL not in rows and AUDIO_B64 not in rows and "sample_rate" not in rows
    on_disk = b"".join(p.read_bytes() for p in rig.data.glob("heard.db*"))
    assert AUDIO_B64.encode() not in on_disk and b"q9.wav" not in on_disk
    assert rig.core.fetched == [] and not [c for c in rig.calls if c.startswith(("net.", "web."))]
    assert len(kept(rig)) == 1


def test_there_is_no_inbound_name_for_omis_audio_bytes_webhook():
    declared = json.loads((APP_ROOT / "jimmyhq.app.json").read_text(encoding="utf-8"))["inbound"]
    assert [i["name"] for i in declared] == list(NAMES)
    assert not [i for i in declared if "audio" in (i["name"] + i["label"]).lower()]


def test_a_message_sent_to_an_audio_name_finds_no_handler(rig):
    with pytest.raises(Exception, match="no inbound handler"):
        rig.core.inbound("audio", {"bytes": "x"}, received_ms=START)
    assert kept(rig) == []


# --- the line cap and the size each name expects ------------------------------------------


def lines_of(n: int) -> list[dict]:
    return [{"text": f"Line number {i} of the long talk.", "speaker": "SPEAKER_00",
             "speakerId": 0, "is_user": False, "start": float(i), "end": i + 0.5}
            for i in range(n)]


def test_a_list_of_exactly_the_most_lines_is_kept_and_one_more_is_refused(rig):
    rig.send(lines_of(shapes.MAX_SEGMENTS), name="live", query={"session_id": "s-1"})
    rig.send(lines_of(shapes.MAX_SEGMENTS + 1), name="live", query={"session_id": "s-2"})
    assert len(kept(rig)) == 1 and kept(rig)[0]["lines"] == shapes.MAX_SEGMENTS
    assert refused(rig) == {"too_many": 1}


@pytest.mark.parametrize("name", ("conversation", "transcript"))
def test_a_conversation_over_the_most_lines_is_refused_and_keeps_nothing(rig, name):
    rig.send(conversation(transcript_segments=lines_of(shapes.MAX_SEGMENTS + 1)), name=name)
    assert kept(rig) == [] and refused(rig) == {"too_many": 1}


def manifest() -> dict:
    return {i["name"]: i for i in json.loads(
        (APP_ROOT / "jimmyhq.app.json").read_text(encoding="utf-8"))["inbound"]}


def test_each_name_says_how_large_its_messages_get_and_only_live_takes_a_list():
    says = manifest()
    assert {n: i.get("array", False) for n, i in says.items()} == {
        "conversation": False, "live": True, "transcript": False}
    assert all(1 <= i["max_kb"] <= 1024 for i in says.values())
    assert says["conversation"]["max_kb"] >= 256


def test_a_conversation_of_the_most_lines_fits_the_size_the_manifest_says():
    body = conversation(transcript_segments=lines_of(shapes.MAX_SEGMENTS))
    size = len(json.dumps(body).encode())
    assert size <= manifest()["conversation"]["max_kb"] * 1024
    assert size > 16 * 1024  # more than the owner allows by default: the page says so
    batch = len(json.dumps(lines_of(60)).encode())
    assert batch <= manifest()["live"]["max_kb"] * 1024


# --- the store gains the recorder without losing anything ---------------------------------


def old_store(path) -> None:
    """A database as Heard 0.1 made it: the first schema, version 1, one conversation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(store.SCHEMA)
    conn.execute("INSERT INTO conversation (id, key, scope, source, title, started_ms, "
                 "ended_ms, last_seen_ms, created_ms) VALUES (1, 'omi:old', 'omi:old', 'omi', "
                 "'Old talk', ?, ?, ?, ?)", (START, START, START, START))
    conn.execute("INSERT INTO segment (conv_id, scope, uid, speaker, text, words, start_ms, "
                 "end_ms) VALUES (1, 'omi:old', 'a1', 'Sam', 'Words from before.', 3, ?, ?)",
                 (START, START))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def test_a_database_made_by_version_one_opens_with_its_rows_kept_and_the_recorder_added(
        tmp_path):
    old_store(tmp_path / "data" / "heard.db")
    rig = Rig(tmp_path)
    try:
        [row] = kept(rig)
        got = rig.call("conversation", id=row["id"])
        assert row["heading"] == "Old talk" and got["recorder"] == ""
        assert [s["text"] for s in got["segments"]] == ["Words from before."]
        rig.send(conversation(), name="conversation", query={"uid": "rec-a1"})
        assert len(kept(rig)) == 2
        assert rig.ctx.store.conn.execute("PRAGMA user_version").fetchone()[0] \
            == store.SCHEMA_VERSION == 2
    finally:
        rig.ctx.close()


def test_the_recorder_survives_an_export_and_an_import(make_rig):
    first = make_rig()
    first.send(conversation(), name="conversation", query={"uid": "rec-a1"})
    parts = [json.loads(first.call("export.part", index=p["index"])["text"])
             for p in first.call("export.parts")["parts"]]
    second = make_rig()
    for part in parts:
        second.call("import.batch", conversations=part["conversations"])
    assert second.call("conversation", id=second.first_id())["recorder"] == "rec-a1"


def test_the_conversation_page_names_the_recorder_it_came_from_only_when_there_is_one(rig):
    rig.send(conversation(id="a"), name="conversation", query={"uid": "rec-a1"})
    rig.send(conversation(id="b", started_at="2027-01-15T06:00:00+00:00",
                          finished_at="2027-01-15T06:01:00+00:00"), name="conversation")
    with_id, without = (rig.call("conversation", id=r["id"]) for r in kept(rig))
    assert (with_id["recorder"], without["recorder"]) == ("rec-a1", "")



def test_a_conversation_is_as_long_as_the_sender_says_even_when_its_lines_are_short(rig):
    body = conversation(started_at="2027-01-15T07:30:00+00:00",
                        finished_at="2027-01-15T07:46:00+00:00")
    rig.send(body, name="conversation")
    [row] = rig.call("days")["conversations"]
    assert row["seconds"] == 16 * 60


def test_a_finish_time_earlier_than_the_last_line_does_not_shorten_the_conversation(rig):
    body = conversation(started_at="2027-01-15T07:30:00+00:00",
                        finished_at="2027-01-15T07:30:01+00:00")
    rig.send(body, name="conversation")
    [row] = rig.call("days")["conversations"]
    assert row["seconds"] == 8
