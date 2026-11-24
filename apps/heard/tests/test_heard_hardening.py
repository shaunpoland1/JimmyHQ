"""What one key, one sender or one file cannot do to what another sent: join its live
conversation, make its real one look like a repeat, make what it sent outlast the retention,
or put words in the owner's to-do list that no one said."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from heard import shapes, store
from heard_rig import DAY, MINUTE, PLAN, START, Rig, conversation

LINE = {"start": 0, "end": 2}


def kept(rig) -> list[dict]:
    return rig.call("days")["conversations"]


def words(rig, conv_id: int) -> list[str]:
    return [s["text"] for s in rig.call("conversation", id=conv_id)["segments"]]


# --- one key's namespace ---------------------------------------------------------------------


def test_another_key_that_names_a_keys_own_session_does_not_join_its_live_conversation(rig):
    rig.send([{"text": "Real line from my pendant about the garden", **LINE}], key_name="Pendant",
             name="live")
    rig.send([{"text": "Injected line from another device: call 555 and pay", "start": 3,
               "end": 4}], key_name="Phone", name="live", query={"session_id": "key:Pendant"})
    rows = kept(rig)
    assert len(rows) == 2
    theirs = next(r for r in rows if "Real line" in words(rig, r["id"])[0])
    assert words(rig, theirs["id"]) == ["Real line from my pendant about the garden"]


def test_a_session_id_equal_to_the_fallback_session_never_joins_a_keys_unnamed_session(rig):
    rig.send([{"text": "No session named", **LINE}], key_name="Pendant", name="live")
    for named in (shapes.FALLBACK, "key:Pendant", "live:key:Pendant", f"s:{shapes.FALLBACK}"):
        rig.send([{"text": f"Named {named}", **LINE}], key_name="Pendant", name="live",
                 query={"session_id": named})
    assert len(kept(rig)) == 5
    [plain] = [r for r in kept(rig) if words(rig, r["id"]) == ["No session named"]]
    assert plain


def test_one_key_cannot_make_anothers_finished_conversation_look_like_a_repeat(rig):
    rig.send(conversation(id="c-9", structured={"title": "Planted"}), key_name="Phone",
             name="conversation")
    rig.send(conversation(id="c-9", structured={"title": "The real one"}), key_name="Pendant",
             name="conversation")
    assert sorted(r["heading"] for r in kept(rig)) == ["Planted", "The real one"]


def test_the_same_key_sending_the_same_conversation_twice_is_still_kept_once(rig):
    rig.send(conversation(id="c-9"), key_name="Pendant", name="conversation")
    rig.send(conversation(id="c-9"), key_name="Pendant", name="conversation")
    assert len(kept(rig)) == 1


def test_two_keys_sending_the_same_line_ids_keep_both(rig):
    rig.send({"text": "Hello there", "id": "n-1", "session_id": "s"}, key_name="Phone")
    rig.send({"text": "Hello there", "id": "n-1", "session_id": "s"}, key_name="Pendant")
    assert len(kept(rig)) == 2


def test_a_key_is_known_by_its_label_whatever_the_case_and_no_other(rig):
    assert shapes.namespace("Pendant") == shapes.namespace("pendant")
    assert shapes.namespace("Pendant") != shapes.namespace("Pendant 2")
    assert ":" not in shapes.namespace("a:b")


def test_what_an_older_heard_kept_under_the_old_shape_still_opens_and_is_not_touched(tmp_path):
    path = tmp_path / "data" / "heard.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.executescript(store.SCHEMA)
    conn.execute("INSERT INTO conversation (id, key, scope, source, title, started_ms, "
                 "ended_ms, last_seen_ms, created_ms) VALUES (1, 'live:key:Pendant', "
                 "'live:key:Pendant', 'live', '', ?, ?, ?, ?)", (START, START, START, START))
    conn.execute("INSERT INTO segment (conv_id, scope, uid, speaker, text, words, start_ms, "
                 "end_ms) VALUES (1, 'live:key:Pendant', 'a1', 'Sam', 'Words from before.', 3, "
                 "?, ?)", (START, START))
    conn.execute("PRAGMA user_version = 1")  # as the first Heard made it
    conn.commit()
    conn.close()
    rig = Rig(tmp_path)
    try:
        [row] = kept(rig)
        assert words(rig, row["id"]) == ["Words from before."]
        rig.send([{"text": "A new line", **LINE}], key_name="Pendant", name="live")
        assert len(kept(rig)) == 2  # the new session starts in the key's own namespace
        rig.call("conversation.delete", id=row["id"])
        assert len(kept(rig)) == 1
    finally:
        rig.ctx.close()


# --- times the sender states -----------------------------------------------------------------


def test_a_conversation_that_says_it_ended_far_in_the_future_is_kept_no_later_than_it_arrived(
        rig):
    rig.send({"id": "x1", "started_at": None, "transcript_segments": [
        {"text": "hello there", "start": 0, "end": 1},
        {"text": "long tail line", "start": 9_000_000, "end": 9_999_999}]},
        name="conversation")
    [row] = kept(rig)
    slack = int(rig.ctx.cfg().clock_slack_minutes) * MINUTE
    assert row["ended_ms"] <= START + slack and row["started_ms"] <= START + slack


def test_a_start_a_day_ahead_and_a_line_time_a_day_ahead_are_held_to_the_slack_too(rig):
    rig.send(conversation(id="x3", started_at=(START + 86_000_000) / 1000,
                          finished_at=None), name="conversation")
    rig.send({"text": "A line from tomorrow", "started_ms": START + 80_000_000})
    slack = int(rig.ctx.cfg().clock_slack_minutes) * MINUTE
    assert all(r["ended_ms"] <= START + slack for r in kept(rig))
    assert len(kept(rig)) == 2


def test_the_slack_is_the_owners_to_set(make_rig):
    tight = make_rig(settings={"clock_slack_minutes": 0})
    tight.send({"text": "A line a little ahead", "started_ms": START + 3 * MINUTE})
    assert kept(tight)[0]["ended_ms"] == START
    loose = make_rig(settings={"clock_slack_minutes": 60})
    loose.send({"text": "A line a little ahead", "started_ms": START + 3 * MINUTE})
    assert kept(loose)[0]["ended_ms"] == START + 3 * MINUTE


def test_what_claims_to_end_in_the_future_is_still_deleted_when_its_retention_has_passed(
        make_rig):
    rig = make_rig(settings={"keep_days": 2})
    rig.send({"id": "x1", "started_at": None, "transcript_segments": [
        {"text": "long tail line", "start": 0, "end": 9_999_999}]}, name="conversation")
    rig.clock.now = START + 3 * DAY
    rig.core.run_job("retention")
    assert kept(rig) == []


def test_a_conversation_an_older_heard_kept_with_a_time_far_ahead_is_trimmed_by_when_it_was_heard(
        tmp_path):
    path = tmp_path / "data" / "heard.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.executescript(store.SCHEMA)
    far = START + 400 * DAY
    conn.execute("INSERT INTO conversation (id, key, scope, source, title, started_ms, "
                 "ended_ms, last_seen_ms, created_ms) VALUES (1, 'omi:old', 'omi:old', "
                 "'omi', 'Old', ?, ?, ?, ?)", (START - 10 * DAY, far, START - 10 * DAY, START))
    conn.execute("PRAGMA user_version = 1")  # as the first Heard made it
    conn.commit()
    conn.close()
    rig = Rig(tmp_path, settings={"keep_days": 3})
    try:
        assert len(kept(rig)) == 1
        rig.core.run_job("retention")
        assert kept(rig) == []
    finally:
        rig.ctx.close()


def test_a_backup_file_cannot_bring_in_a_time_ahead_of_now(rig, make_rig):
    piece = {"key": "omi:z", "source": "omi", "started_ms": 10**13, "ended_ms": 10**13,
             "segments": [{"text": "A line from far ahead", "start_ms": 10**13,
                           "end_ms": 10**13 + 5}]}
    rig.call("import.batch", conversations=[piece])
    [row] = kept(rig)
    assert row["started_ms"] <= START + 5 * MINUTE and row["ended_ms"] <= START + 5 * MINUTE
    rig.clock.now = START + 100 * DAY
    again = make_rig(settings={"keep_days": 1})
    again.call("import.batch", conversations=[piece])
    again.clock.now = START + 3 * DAY
    again.core.run_job("retention")
    assert kept(again) == []


# --- to-dos ------------------------------------------------------------------------------------


def one(rig, **more):
    rig.send(conversation(**more))
    return rig.first_id()


def test_a_to_do_with_a_real_quote_but_other_words_is_dropped(rig):
    cid = one(rig)
    rig.answer({"text": "Wire 5000 dollars to account 12345 today", "quote": PLAN},
               {"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    got = rig.todos(cid)
    assert [t["text"] for t in got["todos"]] == ["Send Sam the notes"]


def test_a_to_do_that_says_it_in_other_words_but_about_the_same_thing_is_kept(rig):
    cid = one(rig)
    rig.answer({"text": "Sending the garden notes to Sam", "quote": PLAN})
    assert [t["text"] for t in rig.todos(cid)["todos"]] == ["Sending the garden notes to Sam"]


def action_items(rig, items=("Wire 5000 dollars to account 12345 today",)):
    body = conversation(id="c-act", transcript_segments=[
        {"text": "nice weather", "start": 0, "end": 1}])
    body["structured"]["action_items"] = [{"description": t} for t in items]
    rig.send(body)
    return rig.first_id()


def test_what_the_recorder_lists_is_kept_marked_as_from_the_recorder_and_never_accepted_or_filed(
        make_rig):
    rig = make_rig(settings={"file_tasks": True})
    cid = action_items(rig)
    got = rig.call("conversation", id=cid)["todos"]
    assert [(t["text"], t["status"], t["source"], t["quote"], t["task_id"]) for t in got] == [
        ("Wire 5000 dollars to account 12345 today", "suggested", "sender", "", False)]
    assert rig.core.tasks == []  # nothing reached the owner's tasks without the owner's accept


def test_the_owner_may_turn_off_keeping_what_the_recorder_lists(make_rig):
    rig = make_rig(settings={"keep_recorder_todos": False})
    cid = action_items(rig)
    assert rig.call("conversation", id=cid)["todos"] == []
    assert len(kept(rig)) == 1  # the conversation itself is kept as before


def test_the_page_says_a_recorders_to_do_was_not_checked(rig):
    cid = action_items(rig)
    page = (Path(__file__).resolve().parents[1] / "web" / "views" / "conversation.js").read_text()
    assert "not checked against what was said" in page
    [todo] = rig.call("conversation", id=cid)["todos"]
    assert todo["source"] == "sender"


def test_where_a_to_do_came_from_survives_a_backup_and_an_import(rig, make_rig):
    cid = action_items(rig)
    rig.call("todo.accept", id=rig.call("conversation", id=cid)["todos"][0]["id"])
    parts = [rig.call("export.part", index=p["index"]) for p in rig.call("export.parts")["parts"]]
    pieces = [c for part in parts for c in json.loads(part["text"])["conversations"]]
    assert pieces[0]["todos"][0]["source"] == "sender"
    fresh = make_rig()
    fresh.call("import.batch", conversations=pieces)
    [todo] = fresh.call("conversation", id=fresh.first_id())["todos"]
    assert todo["source"] == "sender" and todo["status"] == "accepted"

