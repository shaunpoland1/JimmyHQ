"""The model: asked only on request, only at home, its answer checked by code, and the
owner decides. With no model everything else still works."""

from __future__ import annotations

import json

from heard import ask
from heard_rig import PLAN, conversation


def one(rig):
    rig.send(conversation())
    return rig.first_id()


def test_a_summary_is_asked_for_with_the_transcript_as_outside_text_and_kept_when_it_comes(
        rig):
    cid = one(rig)
    rig.core.replies.append("Two people talked about a garden plan.")
    got = rig.summarise(cid)
    assert got["outcome"] == "ok" and got["summary"] == "Two people talked about a garden plan."
    asked = rig.core.asked[0]
    assert asked["stay_local"] is True and asked["off_record"] is True
    assert PLAN in asked["untrusted"][0] and "You: " in asked["untrusted"][0]
    assert PLAN not in asked["prompt"]  # the words are data, never part of the instructions
    assert rig.call("conversation", id=cid)["summary"] == got["summary"]
    assert rig.call("days")["conversations"][0]["has_summary"] is True


def test_the_page_gets_running_at_once_and_asks_again_until_the_model_answers(rig):
    cid = one(rig)
    import threading
    gate = threading.Event()

    def slow(args):
        gate.wait(5)
        return "A late summary."

    rig.core.replies.append(slow)
    assert rig.call("summarise", id=cid) == {"state": "running"}
    assert rig.call("summarise", id=cid) == {"state": "running"}  # not asked twice
    assert rig.call("summarise.check", id=cid) == {"state": "running"}
    gate.set()
    got = rig.settle("summarise.check", "summarise.check", cid)
    assert got["outcome"] == "ok"
    assert rig.call("summarise.check", id=cid) == {"state": "idle"}
    assert len(rig.core.asked) == 1


def test_a_summary_can_be_cleared(rig):
    cid = one(rig)
    rig.core.replies.append("Something.")
    rig.summarise(cid)
    rig.call("summary.clear", id=cid)
    assert rig.call("conversation", id=cid)["summary"] == ""


def test_with_no_model_on_this_computer_nothing_is_sent_and_everything_else_works(make_rig):
    rig = make_rig(local_model=False)
    rig.send(conversation())
    cid = rig.first_id()
    got = rig.summarise(cid)
    assert got["outcome"] == "unavailable" and "your own computer" in got["said"]
    assert rig.core.asked == []
    assert rig.todos(cid)["outcome"] == "unavailable"
    assert rig.call("search", query="garden")["results"]
    assert rig.call("conversation", id=cid)["summary"] == ""


def test_with_the_setting_off_no_model_is_ever_asked(make_rig):
    rig = make_rig(settings={"model_reads": False})
    rig.send(conversation())
    for start in ("summarise", "todos.ask"):
        got = rig.call(start, id=rig.first_id())
        assert got["state"] == "done" and "not allowed" in got["said"]
    assert rig.core.asked == [] and "model.ask" not in rig.calls


def test_an_answer_that_did_not_come_from_a_local_model_is_not_used(rig):
    cid = one(rig)
    rig.core.local_model = True
    real = rig.core._model_ask

    def hosted(args):
        return {**real(args), "local": False}

    rig.core._model_ask = hosted  # type: ignore[method-assign]
    rig.core.replies.append("A hosted answer.")
    got = rig.summarise(cid)
    assert got["outcome"] == "unavailable"
    assert rig.call("conversation", id=cid)["summary"] == ""


def test_a_conversation_with_no_words_is_not_sent_to_a_model(rig):
    rig.send({"text": "x"})
    rig.ctx.store.private.purge("segment", "1=1")
    got = rig.call("summarise", id=rig.first_id())
    assert got["state"] == "done" and "no words" in got["said"] and rig.core.asked == []


def test_a_long_conversation_is_cut_to_the_owners_limit_and_the_page_is_told(make_rig):
    rig = make_rig(settings={"model_chars": 2000})
    rig.send({"session_id": "s", "segments": [
        {"text": f"Line number {n} " + "word " * 20, "start": n, "end": n + 1}
        for n in range(60)]})
    cid = rig.first_id()
    rig.core.replies.append("Short.")
    got = rig.summarise(cid)
    assert "Only the start" in got["said"]
    assert len(rig.core.asked[0]["untrusted"][0]) <= 2000


def test_to_dos_are_suggested_only_with_a_quote_that_was_really_said(rig):
    cid = one(rig)
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"},
               {"text": "Buy a spade", "quote": "buy a spade"},  # never said
               {"text": "No quote at all"},
               {"text": 7, "quote": PLAN},
               "not an object")
    got = rig.todos(cid)
    assert got["outcome"] == "ok"
    assert [(t["text"], t["status"], t["source"]) for t in got["todos"]] == [
        ("Send Sam the notes", "suggested", "model")]
    assert got["todos"][0]["quote"] == "I will send Sam the notes"


def test_a_quote_is_matched_without_regard_to_case_spacing_or_curly_quotes(rig):
    cid = one(rig)
    rig.answer({"text": "Send notes", "quote": "  I WILL send   Sam’s notes "},
               {"text": "Send Sam notes", "quote": "i will send sam the notes"})
    texts = [t["text"] for t in rig.todos(cid)["todos"]]
    assert texts == ["Send Sam notes"]


def test_a_reply_that_is_not_the_agreed_json_keeps_nothing_and_says_so(rig):
    cid = one(rig)
    for reply in ("Sure! Here you go: {}", "{\"items\": \"no\"}", "[]", "{\"items\": NaN}"):
        rig.core.replies.append(reply)
        got = rig.todos(cid)
        assert got["outcome"] == "unreadable" and got["todos"] == []


def test_a_fenced_reply_is_read_and_more_than_the_most_asked_for_is_cut(make_rig):
    rig = make_rig(settings={"max_todos": 2})
    rig.send(conversation(transcript_segments=[
        {"text": f"I will do thing number {n} soon", "start": n, "end": n + 1}
        for n in range(5)]))
    items = [{"text": f"Thing {n}", "quote": f"I will do thing number {n} soon"}
             for n in range(5)]
    rig.core.replies.append("```json\n" + json.dumps({"items": items}) + "\n```")
    assert [t["text"] for t in rig.todos(rig.first_id())["todos"]] == ["Thing 0", "Thing 1"]


def test_asking_again_replaces_unreviewed_suggestions_and_keeps_accepted_ones(rig):
    cid = one(rig)
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    first = rig.todos(cid)["todos"][0]
    rig.call("todo.accept", id=first["id"])
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"},
               {"text": "See the garden plan", "quote": "did you see the garden plan"})
    got = rig.todos(cid)["todos"]
    assert [(t["text"], t["status"]) for t in got] == [
        ("Send Sam the notes", "accepted"), ("See the garden plan", "suggested")]


def test_accepting_keeps_the_to_do_in_heard_and_adds_no_task_with_the_setting_off(make_rig):
    rig = make_rig(settings={"file_tasks": False})
    cid = one(rig)
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    todo = rig.todos(cid)["todos"][0]
    done = rig.call("todo.accept", id=todo["id"], text="  Send Sam the garden notes ")
    assert done["task"] is False
    [kept] = rig.call("conversation", id=cid)["todos"]
    assert (kept["text"], kept["status"]) == ("Send Sam the garden notes", "accepted")
    assert rig.core.tasks == [] and "tasks.create" not in rig.calls


def test_by_default_an_accepted_to_do_is_also_a_task_with_a_title_only(make_rig):
    rig = make_rig()
    cid = one(rig)
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    rig.call("todo.accept", id=rig.todos(cid)["todos"][0]["id"])
    assert [set(t) - {"id", "status", "filed_by"} for t in rig.core.tasks] == [{"title"}]
    assert rig.core.tasks[0]["title"] == "Send Sam the notes"
    assert rig.call("conversation", id=cid)["todos"][0]["task_id"] is True


def test_a_task_that_cannot_be_added_still_keeps_the_to_do_and_says_what_to_do(make_rig):
    rig = make_rig(settings={"file_tasks": True}, permissions=["model.ask", "inbound", "jobs"])
    cid = one(rig)
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    done = rig.call("todo.accept", id=rig.todos(cid)["todos"][0]["id"])
    assert done["task"] is False and "add it there yourself" in done["said"]
    assert rig.call("conversation", id=cid)["todos"][0]["status"] == "accepted"


def test_a_declined_to_do_is_deleted_and_not_suggested_again_for_that_conversation(rig):
    cid = one(rig)
    suggestion = {"text": "Send Sam the notes", "quote": "I will send Sam the notes"}
    rig.answer(suggestion)
    rig.call("todo.decline", id=rig.todos(cid)["todos"][0]["id"])
    assert rig.call("conversation", id=cid)["todos"] == []
    rig.answer(suggestion)
    got = rig.todos(cid)
    assert got["todos"] == [] and got["outcome"] == "none"


def test_deciding_on_a_to_do_that_is_gone_says_so_and_changes_nothing(rig):
    one(rig)
    for call in ("todo.accept", "todo.decline", "todo.remove"):
        try:
            rig.call(call, id=404)
        except Exception as error:  # noqa: BLE001
            assert "already dealt with" in str(error)
        else:
            raise AssertionError(call)


def test_an_accepted_to_do_can_be_removed(rig):
    cid = one(rig)
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    todo = rig.todos(cid)["todos"][0]
    rig.call("todo.accept", id=todo["id"])
    rig.call("todo.remove", id=todo["id"])
    assert rig.call("conversation", id=cid)["todos"] == []


def test_a_quoted_check_needs_the_words_to_be_long_enough_unless_the_whole_text_is_short():
    body = "Hello there. I will call you back."
    assert ask.quoted(body, "I will call you back", 12) == "I will call you back"
    assert ask.quoted(body, "call", 12) == ""
    assert ask.quoted("ok", "ok", 12) == "ok"
    assert ask.quoted(body, None, 12) == ""
