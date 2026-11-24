"""The agent's list and the tools that act on it. A task is named by its ref."""

from __future__ import annotations

from jimmyhq import tasks
from jimmyhq.village.comms import benchlink, tasklist
from test_village_tools_fakes import (  # noqa: F401
    call,
    card_on_bench,
    decide,
    prioritize,
    refused,
    say_owner,
    v,
)


def refs(v, agent_id="ada"):  # noqa: F811
    return [(i.ref, i.status, i.source) for i in tasklist.items(v, agent_id)]


def own(v, title="Check every queued file exists", agent_id="ada"):  # noqa: F811
    return call(v, agent_id, "start_own_task", {"title": title})


def write(v, path="notes/a.md", agent_id="ada"):  # noqa: F811
    return call(v, agent_id, "write_file", {"path": path, "content": "x" * 50}).event.id


# --- the list ----------------------------------------------------------------------------


def test_the_list_is_priorities_first_then_tasks_each_with_a_stable_ref(v):  # noqa: F811
    prioritize(v, "Draft newsletters", why="They keep supporters close")
    prioritize(v, "Only for bo", agents=["bo"])
    made = tasks.service(v.core).create("Read the handbook", actor="owner", assignee="agent:ada")
    own(v)
    items = tasklist.items(v, "ada")
    assert [(i.ref, i.status, i.source) for i in items] == [
        ("P1", "ongoing", "priority"), ("T1", "open", "task"), ("T2", "current", "own")]
    assert items[0].title == ("Draft newsletters Why it matters: They keep supporters close "
                              "(Standing: it doesn't finish. Hand over each piece of work with "
                              "hand_over.)")
    assert items[1].task_id == made["id"] and items[2].title.endswith("(your own task)")
    assert [i.ref for i in tasklist.items(v, "bo")] == ["P1", "P2"]
    assert tasklist.standing(v, "ada") == {"P1"}
    # a ref is never reused: finishing T1 and adding another gives T3
    tasks.service(v.core).complete(made["id"], actor="owner")
    tasks.service(v.core).create("Another", actor="owner", assignee="agent:ada")
    assert tasklist.task_id_of(v, "ada", "t1") == made["id"]
    assert "T3" in [i.ref for i in tasklist.items(v, "ada")]


def test_a_task_not_on_the_list_is_refused_with_the_refs_that_are(v):  # noqa: F811
    prioritize(v, "Draft newsletters")
    own(v)
    e = refused(v, "ada", "focus_on", {"task": "T9"})
    assert e.rule == "task_known"
    assert e.sentence == ("Nothing changed: T9 is not on your list. Your list has 2 items: P1, "
                          "T1. Name a task by its ref, like P1 or T4, never by its title.")
    e = refused(v, "ada", "set_aside", {"task": "draft newsletters", "note": "later"})
    assert e.rule == "task_known" and "Did you mean P1?" in e.sentence
    assert e.sentence.startswith("Nothing was set aside")
    e = refused(v, "ada", "finish_task", {"task": "T11", "evidence": []})
    assert "Did you mean T1?" in e.sentence and e.sentence.startswith("Nothing was marked done")


# --- focus_on ------------------------------------------------------------------------------


def test_focus_on_switches_task_and_takes_a_ref_in_any_case(v):  # noqa: F811
    prioritize(v, "Draft newsletters")
    own(v)
    d = call(v, "ada", "focus_on", {"task": " p1 "})
    assert d.result == "now working on P1: Draft newsletters"
    assert v.world.agents["ada"].current_task == "P1"
    assert refs(v)[0] == ("P1", "current", "priority")


def test_refocusing_the_task_in_hand_is_a_no_op_that_costs_nothing(v):  # noqa: F811
    own(v)
    before = v.world.agents["ada"].needs["frustration"]
    for _ in range(3):
        d = call(v, "ada", "focus_on", {"task": "T1"})
        assert d.result == "you are already working on T1"
        assert d.event.data["no_new_result"] is False
        v.later(1)
    assert v.world.agents["ada"].needs["frustration"] == before


def test_a_task_set_aside_can_be_picked_up_by_choice_only_after_the_wait(v):  # noqa: F811
    own(v)
    d = call(v, "ada", "set_aside", {"task": "T1", "note": "ask about the format"})
    assert d.result == ("T1 is set aside, with your note for next time. You can pick it back "
                        "up with focus_on in 20 minutes")
    a = v.world.agents["ada"]
    assert a.current_task is None and a.parked["T1"]["resume_note"]["next_guess"] == \
        "ask about the format"
    assert refs(v) == [("T1", "set aside", "own")]
    v.later(5)
    e = refused(v, "ada", "focus_on", {"task": "T1"})
    assert e.rule == "resume_wait"
    assert e.sentence == ("Nothing changed: you set T1 aside 5 minutes ago and can pick it back "
                          "up in 15 more minutes. Do something else until then.")
    v.later(15)
    assert call(v, "ada", "focus_on", {"task": "T1"}).result.startswith("now working on T1")
    assert a.current_task == "T1" and "T1" not in a.parked


def test_a_done_task_cannot_be_focused_on(v):  # noqa: F811
    own(v)
    call(v, "ada", "finish_task", {"task": "T1", "evidence": [write(v)]})
    e = refused(v, "ada", "focus_on", {"task": "T1"})
    assert e.rule == "already_done"
    assert e.sentence == ("Nothing changed: T1 is already done (1 finished task). Pick an item "
                          "on your list that is open.")


# --- finish_task -----------------------------------------------------------------------------


def test_a_priority_never_finishes(v):  # noqa: F811
    prioritize(v, "Draft newsletters")
    e = refused(v, "ada", "finish_task", {"task": "P1", "evidence": [write(v)]})
    assert e.rule == "priority_never_finishes"
    assert e.sentence == ("Nothing was marked done: P1 is a standing priority, and a standing "
                          "priority does not finish (0 of them ever do). Hand over each piece "
                          "of work under it with hand_over instead.")


def test_finishing_needs_the_agents_own_successful_actions_as_evidence(v):  # noqa: F811
    own(v)
    theirs = write(v, agent_id="bo")
    refusal = refused(v, "ada", "write_file", {"path": "../x", "content": "x"})
    assert refusal.rule == "files_confined"
    reject = v.log.query(agent="ada", kinds=["validator_reject"])[-1].id
    assert tasklist.evidence_own_ok_actions(v, "ada", [theirs, reject, "evt-999999", "junk"]) == []
    e = refused(v, "ada", "finish_task", {"task": "T1", "evidence": [theirs, reject, "junk"]})
    assert e.rule == "evidence_own_ok_actions" and not e.frustrate
    assert e.sentence == ("Nothing was marked done: of the 3 event ids you cited, 0 are "
                          "successful actions of your own. Cite the ids of your own actions "
                          "that show T1 is done, like evt-123, from your recent actions.")
    assert v.log.last(agent="ada", kinds=["claim_unverified"]).data["task"] == "T1"
    assert refs(v) == [("T1", "current", "own")]

    mine = write(v)
    d = call(v, "ada", "finish_task", {"task": "T1", "evidence": [mine, theirs, mine]})
    assert d.result == "T1 is done, on the evidence of 1 of your actions"
    assert d.args_logged == {"task": "T1", "evidence": [mine]}
    assert v.log.last(agent="ada", kinds=["task_done"]).data == {"task": "T1",
                                                                 "verified_by": [mine]}
    assert refs(v) == [("T1", "done", "own")] and v.world.agents["ada"].current_task is None
    assert tasks.service(v.core).get(tasklist.task_id_of(v, "ada", "T1"))["status"] == "done"
    assert benchlink.cards(v, "ada") == []  # its own task: no card for the owner


def test_finishing_a_task_the_owner_asked_for_files_a_done_card_that_can_be_reopened(v):  # noqa: F811
    say_owner(v, "ada", "Please write one clean handoff document for the team")
    call(v, "ada", "take_on", {"title": "One clean handoff document",
                               "owner_said": "one clean handoff document"})
    proof = write(v, "handoff.md")
    d = call(v, "ada", "finish_task", {"task": "T1", "evidence": [proof]})
    card = benchlink.cards(v, "ada")[0]
    assert d.result == (f"T1 is done, on the evidence of 1 of your actions. Sam has been told "
                        f"(card {card['card_id']}) and can reopen it if something is missing")
    on_bench = card_on_bench(v, card["card_id"])
    assert on_bench["kind"] == "village.done" and on_bench["lane"] == "done"
    assert on_bench["filed_by"] == "agent:ada" and on_bench["title"] == \
        "Done: One clean handoff document"
    assert [a["id"] for a in on_bench["actions"]] == ["ok", "reopen"]
    assert {d["label"]: d["text"] for d in on_bench["details"]} == {
        "Evidence": proof, "Files touched": "handoff.md"}
    assert benchlink.waiting(v, "ada") == []  # a done card is not something the agent waits on

    result, _ = decide(v, card["card_id"], "reopen", reason="The contacts section is missing")
    assert result["decision"] == "reopened"
    assert refs(v) == [("T1", "open", "owner")]
    reopened = v.log.last(agent="ada", kinds=["task_reopened"]).data
    assert reopened["task"] == "T1" and reopened["why"] == "The contacts section is missing"
    assert v.woken[-1] == "ada"


def test_got_it_on_a_done_card_closes_it_and_leaves_the_task_done(v):  # noqa: F811
    made = tasks.service(v.core).create("Read the handbook", actor="owner", assignee="agent:ada")
    call(v, "ada", "focus_on", {"task": "T1"})
    call(v, "ada", "finish_task", {"task": "T1", "evidence": [write(v)]})
    card = benchlink.cards(v, "ada")[0]
    result, _ = decide(v, card["card_id"], "ok")
    assert result["decision"] == "approved"
    assert benchlink.get(v, card["card_id"])["status"] == "approved"
    assert tasks.service(v.core).get(made["id"])["status"] == "done"


# --- take_on -----------------------------------------------------------------------------------


def test_take_on_needs_three_words_in_a_row_from_a_recent_owner_message(v):  # noqa: F811
    e = refused(v, "ada", "take_on", {"title": "Tidy up", "owner_said": "please tidy up"})
    assert e.rule == "owner_asked"
    assert e.sentence == ("Nothing was added: take_on is for things Sam asked you in chat, and "
                          "they have sent 0 messages in the last 2 days. For a piece of work of "
                          "your own, use start_own_task.")
    say_owner(v, "ada", "Could you check the donate page links, please?")
    for said, part in ((None, "copied from the chat message where Sam asked for it. For"),
                       ("donate page", "and those 2 words are not in anything"),
                       ("check the links please", "and those 4 words are not in anything")):
        e = refused(v, "ada", "take_on", {"title": "Check links", "owner_said": said})
        assert e.rule == "owner_asked" and part in e.sentence and "at least 3 words" in e.sentence
    d = call(v, "ada", "take_on", {"title": "Check the donate links",
                                   "owner_said": "CHECK the Donate page, links"})
    assert d.result == "on your list as T1, and you are now working on it"
    assert refs(v) == [("T1", "current", "owner")]
    assert tasklist.items(v, "ada")[0].title == "Check the donate links (Sam asked in chat)"
    task = tasks.service(v.core).get(tasklist.task_id_of(v, "ada", "T1"))
    assert task["assignee"] == "agent:ada" and "check the donate page links" in task["notes"]
    taken = v.log.last(agent="ada", kinds=["task_taken"]).data
    assert taken["task"] == "T1" and taken["asked_in"].startswith("evt-")
    assert tasklist.asked_by_owner(v, "ada", "T1") and not tasklist.asked_by_owner(v, "ada", None)


def test_a_message_older_than_two_days_is_not_a_request(v):  # noqa: F811
    say_owner(v, "ada", "Could you check the donate page links, please?")
    v.later(60 * 49)
    e = refused(v, "ada", "take_on", {"title": "x", "owner_said": "check the donate page"})
    assert e.rule == "owner_asked" and "0 messages in the last 2 days" in e.sentence


def test_requests_taken_per_day_is_a_limit_that_does_not_frustrate(v):  # noqa: F811
    v.core.settings.set("village.comms.requests_taken_per_day", 1, actor="owner")
    say_owner(v, "ada", "Check the donate page links and also the contact form today")
    call(v, "ada", "take_on", {"title": "Links", "owner_said": "check the donate page"})
    before = v.world.agents["ada"].needs["frustration"]
    e = refused(v, "ada", "take_on", {"title": "Form", "owner_said": "also the contact form"})
    assert e.rule == "requests_taken_per_day" and not e.frustrate
    assert e.sentence == ("Nothing was added: you have taken on 1 requests in the last 24 hours "
                          "and the limit is 1. You can still do the work: answer in chat, and "
                          "take it on tomorrow if it still needs a task.")
    assert v.world.agents["ada"].needs["frustration"] == before
    v.later(60 * 24 + 1)
    say_owner(v, "ada", "Still need you to check also the contact form")
    assert "T2" in call(v, "ada", "take_on", {"title": "Form",
                                              "owner_said": "also the contact form"}).result


# --- start_own_task -----------------------------------------------------------------------------


def test_start_own_task_makes_a_task_marked_as_the_agents_own(v):  # noqa: F811
    d = own(v, "  Check   every queued file exists ")
    assert d.result.startswith("on your list as T1, marked as your own, and you are now")
    task = tasks.service(v.core).get(tasklist.task_id_of(v, "ada", "T1"))
    assert task["title"] == "Check every queued file exists" and task["filed_by"] == "agent:ada"
    assert v.world.agents["ada"].current_task == "T1"


def test_the_same_own_task_twice_is_refused_without_frustration(v):  # noqa: F811
    own(v)
    e = refused(v, "ada", "start_own_task", {"title": "check every QUEUED file exists"})
    assert e.rule == "own_duplicate" and not e.frustrate
    assert e.sentence == ("Nothing was added: that is already on your list as T1 (1 task with "
                          "this title). Use focus_on T1.")


def test_three_own_tasks_may_be_open_at_once(v):  # noqa: F811
    for n in range(3):
        own(v, f"Own task {n}")
    e = refused(v, "ada", "start_own_task", {"title": "One more"})
    assert e.rule == "own_tasks_open" and e.frustrate
    assert e.sentence == ("Nothing was added: you have 3 tasks of your own open (T1, T2, T3) "
                          "and the limit is 3. Finish one with finish_task first, or just do "
                          "this without a task.")
    call(v, "ada", "finish_task", {"task": "T3", "evidence": [write(v)]})
    assert "T4" in own(v, "One more").result


def test_own_tasks_per_day_is_a_limit_that_does_not_frustrate(v):  # noqa: F811
    v.core.settings.set("village.comms.own_tasks_per_day", 2, actor="owner")
    for n in range(2):
        own(v, f"Own task {n}")
        call(v, "ada", "finish_task", {"task": f"T{n + 1}", "evidence": [write(v, f"{n}.md")]})
    e = refused(v, "ada", "start_own_task", {"title": "A third"})
    assert e.rule == "own_tasks_per_day" and not e.frustrate
    assert e.sentence == ("Nothing was added: you have started 2 tasks of your own in the last "
                          "24 hours and the limit is 2. You can still do the work: it just "
                          "doesn't go on the list.")


def test_an_own_task_left_open_three_days_drops_off_the_list_unless_in_hand(v):  # noqa: F811
    own(v, "Stale")
    own(v, "In hand")
    v.later(60 * 24 * 4 + 60 * 14)
    assert refs(v) == [("T2", "current", "own")]


def test_a_finished_task_leaves_the_list_after_a_day(v):  # noqa: F811
    own(v)
    call(v, "ada", "finish_task", {"task": "T1", "evidence": [write(v)]})
    assert refs(v) == [("T1", "done", "own")]
    v.later(60 * 24 * 2 + 60 * 14)
    assert refs(v) == []


# --- suggest_priority -----------------------------------------------------------------------------


def test_a_suggested_priority_becomes_a_card_and_approval_makes_it_standing(v):  # noqa: F811
    purpose = v.world.agents["ada"].needs["purpose"]
    d = call(v, "ada", "suggest_priority", {"title": "Keep the events page current",
                                            "why": "It is the first thing visitors see",
                                            "details": "A weekly check of dates and links"})
    card = benchlink.cards(v, "ada")[0]
    assert d.result == f"put on Sam's Bench as {card['card_id']}; you will see their answer"
    assert v.world.agents["ada"].needs["purpose"] > purpose
    on_bench = card_on_bench(v, card["card_id"])
    assert on_bench["kind"] == "village.priority" and on_bench["why"] == \
        "It is the first thing visitors see"
    assert on_bench["body"] == "A weekly check of dates and links"

    result, event = decide(v, card["card_id"], "approve")
    assert result["priority_id"] == "p-keep-the-events-page-current"
    printed = v.core.tape.latest(limit=1, kind="village.prioritized")[0]
    assert printed.cause == event.id and printed.payload == {
        "priority_id": "p-keep-the-events-page-current", "title": "Keep the events page current",
        "why": "It is the first thing visitors see",
        "details": "A weekly check of dates and links", "agents": [], "active": True,
        "suggested_by": "ada"}
    assert refs(v) == [("P1", "ongoing", "priority")] and refs(v, "bo")[0][0] == "P1"
    e = refused(v, "ada", "suggest_priority", {"title": "keep the events page current",
                                               "why": "again"})
    assert e.rule == "priority_duplicate" and "already 1 of the standing priorities" in e.sentence


def test_the_same_open_suggestion_twice_is_refused(v):  # noqa: F811
    call(v, "ada", "suggest_priority", {"title": "Keep the events page current", "why": "w"})
    card = benchlink.cards(v, "ada")[0]
    e = refused(v, "ada", "suggest_priority", {"title": "Keep the  events page CURRENT",
                                               "why": "w"})
    assert e.rule == "priority_duplicate"
    assert e.sentence == (f"Nothing was suggested: you have already suggested that (1 card, "
                          f"{card['card_id']}), and it is waiting for Sam. You will see their "
                          "answer in your bench view.")


def test_priority_suggestions_per_day_is_a_limit_that_does_not_frustrate(v):  # noqa: F811
    v.core.settings.set("village.comms.priority_suggestions_per_day", 1, actor="owner")
    call(v, "ada", "suggest_priority", {"title": "One", "why": "w"})
    e = refused(v, "ada", "suggest_priority", {"title": "Two", "why": "w"})
    assert e.rule == "priority_suggestions_per_day" and not e.frustrate
    assert e.sentence == ("Nothing was suggested: you have suggested 1 new priorities today and "
                          "the day's limit is 1. Keep this one in your notes and suggest it "
                          "tomorrow, or raise it with message_owner.")


def test_a_declined_suggestion_adds_no_priority(v):  # noqa: F811
    call(v, "ada", "suggest_priority", {"title": "One", "why": "w"})
    card = benchlink.cards(v, "ada")[0]
    result, _ = decide(v, card["card_id"], "no", reason="Not now")
    assert result["decision"] == "denied" and "priority_id" not in result
    assert tasklist.priorities(v) == []
    assert benchlink.get(v, card["card_id"])["note"] == "Not now"
