"""The page's views drawn from what the program really answers, with no browser: every piece
of text reaches the page as text (never as markup), the views say what to do when there is
nothing, and the page's own logic passes its node tests. What this cannot check: layout,
colour, focus, the real bridge and the shell's dialogs; those need a browser."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from heard_rig import CHAT, conversation

HERE = Path(__file__).resolve().parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

HOSTILE = '<script>steal()</script><img src=x onerror="alert(1)">'


def node(*args, stdin=None):
    done = subprocess.run(
        [NODE or "node", "--no-warnings", *args], input=stdin, capture_output=True, text=True,
        timeout=120, check=False)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-3000:]
    return done.stdout


def draw(rig, *, open_id=None, query="garden") -> dict:
    cid = open_id or rig.first_id() if rig.call("days")["conversations"] else None
    answers = {
        "days": rig.call("days"), "search": rig.call("search", query=query),
        "overview": rig.call("overview"),
        "export.parts": rig.call("export.parts"),
    }
    if cid:
        answers["conversation"] = rig.call("conversation", id=cid)
        answers["summarise.check"] = {"state": "idle"}
    given = json.dumps({"answers": answers, "overview": rig.call("overview"),
                        "open": {"id": cid, "line": None} if open_id else None})
    return json.loads(node(str(HERE / "render_views.mjs"), stdin=given))["drawn"]


def test_the_pages_own_logic_passes_its_node_tests():
    node("--test", str(HERE / "page_lib.test.mjs"))


def test_a_new_install_shows_the_steps_to_connect_a_recorder(rig):
    drawn = draw(rig)
    assert "Nothing has been heard yet." in drawn["days"]["text"]
    steps = drawn["days"]["text"]
    for word in ("Memory Creation Webhook", "Real-Time Transcript Webhook",
                 "Audio Bytes Webhook", "can only be given an address", "Largest message"):
        assert word in steps, word
    assert "relay" not in steps.lower()
    assert "Search" in drawn["search"]["text"] or drawn["search"]["buttons"]


def test_the_list_shows_title_time_length_and_who_spoke_for_each_conversation(rig):
    rig.send(conversation())
    text = draw(rig)["days"]["text"]
    for word in ("Today", "Garden plan", "07:30", "1 min", "19 words",
                 "You and Speaker 2"):
        assert word in text, word
    assert draw(rig)["days"]["buttons"][0]["click"] is True


def test_hostile_text_in_a_conversation_reaches_the_page_only_as_text(rig):
    rig.send(conversation(
        structured={"title": HOSTILE, "overview": HOSTILE, "category": HOSTILE},
        transcript_segments=[{"text": HOSTILE, "speaker_name": HOSTILE, "start": 0, "end": 1}]))
    rig.send({"text": f"garden {HOSTILE}", "speaker": HOSTILE})
    drawn = draw(rig, open_id=rig.call("search", query="steal")["results"][0]["conv_id"])
    for name, view in drawn.items():
        assert "script" not in view["tags"] and "img" not in view["tags"], name
    assert HOSTILE in drawn["days"]["text"] and HOSTILE in drawn["conversation"]["text"]
    assert HOSTILE in drawn["search"]["text"]


def test_a_search_marks_the_words_that_matched(rig):
    rig.send(conversation())
    drawn = draw(rig, query="garden plan")
    assert drawn["search"]["marks"][:2] == ["garden", "plan"]
    assert "2 matching lines" in drawn["search"]["text"]


def test_a_conversation_page_has_the_transcript_summary_todos_and_the_buttons_for_them(rig):
    rig.send(conversation())
    drawn = draw(rig, open_id=rig.first_id())["conversation"]
    for word in ("Garden plan", "Summary", "To-dos", "Who spoke", "Transcript",
                 "I will send Sam the notes about the garden plan tomorrow.", "Speaker 2"):
        assert word in drawn["text"], word
    labels = {b["text"] for b in drawn["buttons"]}
    assert {"Back", "Summarise", "Pull out to-dos", "Save as a file", "Delete",
            "Rename"} <= labels
    assert all(b["click"] for b in drawn["buttons"])
    assert drawn["classes"]["who-you"] >= 1


def test_the_model_buttons_are_off_and_say_why_when_the_owner_has_not_allowed_a_model(make_rig):
    rig = make_rig(settings={"model_reads": False})
    rig.send(conversation())
    drawn = draw(rig, open_id=rig.first_id())["conversation"]
    off = {b["text"] for b in drawn["buttons"] if b["disabled"]}
    assert {"Summarise", "Pull out to-dos"} <= off
    assert "A model is not allowed to read your conversations" in drawn["text"]


def test_suggested_to_dos_show_what_was_said_and_can_be_accepted_edited_or_declined(rig):
    rig.send(conversation())
    cid = rig.first_id()
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    rig.todos(cid)
    drawn = draw(rig, open_id=cid)["conversation"]
    assert "Send Sam the notes" in drawn["text"] and "I will send Sam the notes" in drawn["text"]
    assert {"Accept", "Edit", "Decline"} <= {b["text"] for b in drawn["buttons"]}


def test_the_settings_page_says_what_was_refused_and_offers_export_import_and_delete(rig):
    rig.send({"nonsense": 1})
    rig.send(conversation())
    drawn = draw(rig)["settings"]
    assert "The message had no transcript_segments" in drawn["text"]
    assert "1 conversation kept" in drawn["text"]
    labels = {b["text"] for b in drawn["buttons"]}
    assert {"Export everything", "Delete everything", "Change settings"} <= labels
    assert "never fetched" in drawn["text"]


def test_a_segment_list_with_hundreds_of_lines_is_drawn_whole(rig):
    rig.send({"session_id": "s", "segments": [
        {**CHAT[n % 2], "start": n, "end": n + 1, "text": f"Line {n} here"} for n in range(300)]})
    drawn = draw(rig, open_id=rig.first_id())["conversation"]
    assert "Line 299 here" in drawn["text"]


def test_the_conversation_page_names_the_recorder_only_when_the_message_carried_one(rig):
    rig.send(conversation(id="a"), name="conversation", query={"uid": "rec-a1"})
    assert "From recorder rec-a1" in draw(rig, open_id=rig.first_id())["conversation"]["text"]
    other = rig.core
    other.inbound("conversation", conversation(id="b", started_at="2027-01-15T06:00:00+00:00",
                                               finished_at="2027-01-15T06:01:00+00:00"),
                  received_ms=rig.clock.now)
    older = rig.call("days")["conversations"][-1]["id"]
    assert "From recorder" not in draw(rig, open_id=older)["conversation"]["text"]


def test_the_settings_page_says_whether_one_recorder_is_chosen(make_rig):
    chosen = draw(make_rig(settings={"only_uid": "rec-a1"}))["settings"]["text"]
    assert "Only messages that carry the recorder id you chose are kept." in chosen
    assert "rec-a1" not in chosen
    assert "Messages from any recorder are kept" in draw(make_rig())["settings"]["text"]

