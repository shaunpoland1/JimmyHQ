"""What the door keeps, stitches, ignores and refuses, and that sending twice is harmless."""

from __future__ import annotations

from heard import intake
from heard_rig import CHAT, HOUR, MINUTE, PLAN, START, conversation


def stored(rig) -> list[dict]:
    return rig.call("days")["conversations"]


def lines(rig, conv_id=None) -> list[dict]:
    return rig.call("conversation", id=conv_id or rig.first_id())["segments"]


def test_a_finished_conversation_is_kept_with_its_title_speakers_and_times(rig):
    rig.send(conversation())
    [row] = stored(rig)
    assert row["heading"] == "Garden plan" and row["day"] == "2027-01-15"
    assert row["seconds"] == 60 and row["lines"] == 2 and row["speakers"] == ["You", "Speaker 2"]
    got = rig.call("conversation", id=row["id"])
    assert got["overview"] == "Talk about the garden."
    assert [(s["speaker"], s["text"]) for s in got["segments"]][1] == ("You", PLAN)


def test_the_same_finished_conversation_sent_twice_is_kept_once_and_edits_survive(rig):
    rig.send(conversation())
    conv_id = rig.first_id()
    rig.call("speaker.rename", id=conv_id, **{"from": "Speaker 2", "to": "Sam"})
    rig.send(conversation())
    assert len(stored(rig)) == 1
    assert "Sam" in [s["speaker"] for s in lines(rig)]  # a resend does not undo a rename


def test_every_message_is_answered_with_what_became_of_it(rig):
    assert intake.accept(rig.ctx, conversation(), START) == "stored"
    assert intake.accept(rig.ctx, conversation(), START) == "duplicate"
    assert intake.accept(rig.ctx, {"nothing": 1}, START) == "unknown_shape"
    assert intake.accept(rig.ctx, conversation(id="d", discarded=True), START) == "skipped"
    assert len(stored(rig)) == 1


def test_a_conversation_the_recorder_discarded_is_kept_only_when_the_owner_says_so(make_rig):
    rig = make_rig(settings={"skip_discarded": False})
    rig.send(conversation(discarded=True))
    assert len(stored(rig)) == 1


def test_live_batches_of_one_session_are_stitched_into_one_conversation(rig):
    rig.send({"session_id": "s1", "segments": CHAT[:1]})
    rig.clock.advance(2 * MINUTE)
    rig.send({"session_id": "s1", "segments": [{**CHAT[1], "start": 130.0, "end": 134.0}]})
    [row] = stored(rig)
    assert row["lines"] == 2 and row["open"] is True
    texts = [s["text"] for s in lines(rig)]
    assert texts[1] == PLAN


def test_a_live_conversation_closes_after_the_quiet_gap_and_the_next_words_start_another(rig):
    rig.send({"session_id": "s1", "segments": CHAT[:1]})
    rig.clock.advance(10 * MINUTE)
    assert stored(rig)[0]["open"] is False
    rig.send({"session_id": "s1", "segments": [{**CHAT[1], "start": 700.0, "end": 704.0}]})
    assert len(stored(rig)) == 2


def test_the_quiet_gap_is_a_setting(make_rig):
    rig = make_rig(settings={"gap_minutes": 30})
    rig.send({"session_id": "s1", "segments": CHAT[:1]})
    rig.clock.advance(20 * MINUTE)
    rig.send({"session_id": "s1", "segments": [{**CHAT[1], "start": 1200.0, "end": 1204.0}]})
    assert len(stored(rig)) == 1


def test_a_live_batch_sent_twice_adds_nothing_even_after_the_conversation_closed(rig):
    batch = {"session_id": "s1", "segments": CHAT}
    rig.send(batch)
    rig.clock.advance(HOUR)
    rig.send(batch)
    [row] = stored(rig)
    assert row["lines"] == 2


def test_two_sessions_never_mix(rig):
    rig.send({"session_id": "a", "segments": CHAT[:1]})
    rig.send({"session_id": "b", "segments": CHAT[1:]})
    assert len(stored(rig)) == 2


def test_live_lines_are_placed_in_time_from_when_the_batch_arrived(rig):
    rig.send({"session_id": "s1", "segments": CHAT})
    got = lines(rig)
    assert got[-1]["end_ms"] == START and got[0]["start_ms"] == START - 8000


def test_generic_lines_from_one_device_are_stitched_by_arrival_and_keep_their_own_times(rig):
    rig.send({"text": "First thing", "started_ms": START - 30_000, "speaker": "Sam"})
    rig.clock.advance(MINUTE)
    rig.send({"text": "Second thing", "started_ms": START + 20_000})
    [row] = stored(rig)
    assert row["lines"] == 2 and row["speakers"] == ["Sam", "Speaker"]
    got = lines(rig)
    assert got[0]["start_ms"] == START - 30_000


def test_a_generic_line_with_an_id_is_kept_once_however_often_it_is_sent(rig):
    for _ in range(3):
        rig.send({"text": "Only once", "id": "m-1"})
        rig.clock.advance(1000)
    assert stored(rig)[0]["lines"] == 1


def test_different_devices_are_different_conversations_by_their_key_name(rig):
    rig.send({"text": "From the pendant"}, key_name="Pendant")
    rig.send({"text": "From the phone"}, key_name="Phone")
    assert len(stored(rig)) == 2


def test_a_refused_message_is_counted_by_kind_and_nothing_it_said_is_kept(rig):
    rig.send({"stuff": "secret words nobody should keep"})
    rig.send({"transcript_segments": []})
    rig.send({"stuff": "again"})
    tally = {r["kind"]: r["n"] for r in rig.call("overview")["refused"]}
    assert tally == {"unknown_shape": 2, "no_words": 1}
    assert rig.call("overview")["connected"] is True
    assert stored(rig) == []
    assert "secret words" not in repr(rig.call("overview"))


def test_a_new_install_is_not_connected_until_something_arrives(rig):
    assert rig.call("overview")["connected"] is False


def test_the_recorders_own_action_items_wait_as_suggestions_for_the_owner(rig):
    body = conversation()
    body["structured"]["action_items"] = [{"description": "Order bulbs", "completed": False}]
    rig.send(body)
    [todo] = rig.call("conversation", id=rig.first_id())["todos"]
    assert (todo["text"], todo["status"], todo["source"]) == ("Order bulbs", "suggested", "sender")
