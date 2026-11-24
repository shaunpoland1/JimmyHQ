"""Talking: chat with the owner, the break room, and the work board."""

from __future__ import annotations

from jimmyhq.village.comms import board, breakroom, chat, status
from test_village_tools_fakes import call, refused, say_owner, v  # noqa: F401

# --- message_owner -----------------------------------------------------------------------


def test_a_message_reaches_the_tape_and_the_log_once(v):  # noqa: F811
    d = call(v, "ada", "message_owner", {"text": "The draft is ready when you are."},
             mode="outlet")
    assert d.result == "sent to Sam" and d.args_logged == {"length": 32}
    said = v.core.tape.latest(limit=1, kind="village.said")[0]
    assert said.payload == {"agent_id": "ada", "sender": "agent",
                            "text": "The draft is ready when you are.", "urgent": False}
    assert said.actor == "agent:ada" and said.subject == "agent:ada"
    assert chat.mirror(v, said) is None  # the village's own tail must not log it again
    assert [(m["sender"], m["text"]) for m in chat.history(v, "ada")] == [
        ("ada", "The draft is ready when you are.")]
    assert "ready when you are" not in str(d.event.data)  # only its length is in the action log


def test_the_owners_message_is_mirrored_once_and_wakes_the_agent(v):  # noqa: F811
    event = v.core.tape.print("village.said", {"agent_id": "ada", "sender": "owner",
                                               "text": "How is it going?", "urgent": False},
                              actor="owner", subject="agent:ada")
    assert chat.mirror(v, event).data == {"sender": "owner", "text": "How is it going?",
                                          "tape": event.id}
    assert chat.mirror(v, event) is None
    assert v.woken == ["ada"] and chat.unanswered(v, "ada")
    assert len(chat.history(v, "ada")) == 1 and chat.history(v, "bo") == []
    call(v, "ada", "message_owner", {"text": "Well, thanks."})
    assert not chat.unanswered(v, "ada")


def test_an_urgent_call_wakes_a_napping_agent_and_stays_open_until_answered(v):  # noqa: F811
    a = v.world.agents["ada"]
    a.asleep, a.sleep_kind, a.mode = True, "nap", "sleep"
    say_owner(v, "ada", "Call me back, the page is down", urgent=True)
    assert not a.asleep
    assert chat.open_call(v, "ada")["text"] == "Call me back, the page is down"
    say_owner(v, "ada", "Are you there?")
    assert chat.open_call(v, "ada") is not None
    call(v, "ada", "message_owner", {"text": "Here. Looking at it now."})
    assert chat.open_call(v, "ada") is None


def test_chat_messages_per_day_counts_only_messages_the_agent_starts(v):  # noqa: F811
    v.core.settings.set("village.comms.chat_messages_per_day", 2, actor="owner")
    call(v, "ada", "message_owner", {"text": "one"})
    call(v, "ada", "message_owner", {"text": "two"})
    assert chat.sent_today(v, "ada") == 2
    before = v.world.agents["ada"].needs["frustration"]
    e = refused(v, "ada", "message_owner", {"text": "three"})
    assert e.rule == "chat_messages_per_day" and not e.frustrate
    assert e.sentence == ("Nothing was sent: you have started 2 messages to Sam today and the "
                          "day's limit is 2. A reply to a message of theirs is always allowed. "
                          "Keep this for tomorrow, or put it in a file.")
    assert v.world.agents["ada"].needs["frustration"] == before
    say_owner(v, "ada", "What was the third thing?")
    assert call(v, "ada", "message_owner", {"text": "three"}).result == "sent to Sam"
    assert chat.sent_today(v, "ada") == 2  # the reply was free
    assert refused(v, "ada", "message_owner", {"text": "four"}).rule == "chat_messages_per_day"
    v.later(60 * 24)
    assert call(v, "ada", "message_owner", {"text": "a new day"}).result == "sent to Sam"


def test_a_message_over_4000_characters_is_not_sent(v):  # noqa: F811
    e = refused(v, "ada", "message_owner", {"text": "x" * 4001})
    assert e.rule == "chat_too_long" and not e.frustrate
    assert e.sentence == ("Nothing was sent: that is 4001 characters and a message takes 4000. "
                          "Send the short version, and put anything long in a file and name it "
                          "in the message.")
    assert chat.history(v, "ada") == []
    assert call(v, "ada", "message_owner", {"text": "x" * 4000}).result == "sent to Sam"


# --- say ---------------------------------------------------------------------------------------


def test_say_is_heard_fills_the_speaker_and_wakes_the_listeners(v):  # noqa: F811
    social = v.world.agents["ada"].needs["social"]
    d = call(v, "ada", "say", {"text": "Morning. Anyone seen the events page?"}, mode="social")
    assert d.result == "said in the break room" and d.args_logged == {"length": 37}
    assert v.world.agents["ada"].needs["social"] > social
    assert v.woken == ["bo"]
    assert [e.data["text"] for e in breakroom.recent(v, "bo")] == [
        "Morning. Anyone seen the events page?"]
    assert breakroom.invited(v, "bo") and not breakroom.invited(v, "ada")
    assert breakroom.unanswered_since(v, "ada").data["text"].startswith("Morning")
    heard = v.world.agents["bo"].needs["social"]
    call(v, "bo", "say", {"text": "I have. It is out of date."}, mode="social")
    assert breakroom.unanswered_since(v, "ada") is None
    assert v.world.agents["ada"].needs["social"] > social  # being answered fills too
    assert v.world.agents["bo"].needs["social"] > heard


def test_talking_to_an_empty_room_fills_nothing(v):  # noqa: F811
    b = v.world.agents["bo"]
    b.asleep, b.mode = True, "sleep"
    social = v.world.agents["ada"].needs["social"]
    d = call(v, "ada", "say", {"text": "Hello?"}, mode="social")
    assert d.result == "said in the break room; nobody else is around right now"
    assert v.world.agents["ada"].needs["social"] == social and v.woken == []


def test_a_calm_agents_long_message_is_not_said(v):  # noqa: F811
    e = refused(v, "ada", "say", {"text": "x" * 701}, mode="social")
    assert e.rule == "say_too_long" and not e.frustrate
    assert e.sentence == ("Nothing was said: that is 701 characters and the break room takes "
                          "700. Say the short version here. Anything longer belongs on the work "
                          "board (post_to_board) or in a file, with one line here pointing at "
                          "it.")
    assert breakroom.recent(v) == []
    assert call(v, "ada", "say", {"text": "x" * 700}, mode="social").result.startswith("said")


def test_a_short_tempered_agent_is_cut_short_instead(v):  # noqa: F811
    v.world.agents["ada"].needs["frustration"] = 60
    assert breakroom.short_tempered(v, "ada") and not breakroom.short_tempered(v, "bo")
    d = call(v, "ada", "say", {"text": "y" * 400}, mode="social")
    assert d.result == "said in the break room (you were too short-tempered to say more)"
    assert breakroom.recent(v)[-1].data["text"] == "y" * 160 + "…"


def test_the_break_room_cap_is_logged_as_a_fault_and_does_not_frustrate(v):  # noqa: F811
    v.core.settings.set("village.comms.breakroom_per_hour", 2, actor="owner")
    call(v, "ada", "say", {"text": "one"}, mode="social")
    call(v, "bo", "say", {"text": "two"}, mode="social")
    before = v.world.agents["ada"].needs["frustration"]
    e = refused(v, "ada", "say", {"text": "three"}, mode="social")
    assert e.rule == "breakroom_per_hour" and not e.frustrate
    assert e.sentence == ("Nothing was said: the break room has had 2 messages this hour and "
                          "takes 2, so it is closed for the rest of the hour. Go back to what "
                          "you were doing; if a coworker needs something from you, use the work "
                          "board (post_to_board).")
    assert v.world.agents["ada"].needs["frustration"] == before
    fault = v.log.last(kinds=["platform_fault"])
    assert fault.agent == "ada" and fault.data["what"] == "break-room cap reached"
    v.later(61)
    assert call(v, "ada", "say", {"text": "three"}, mode="social").result.startswith("said")


# --- the board ----------------------------------------------------------------------------------


def test_a_post_goes_on_the_board_and_a_coworker_can_resolve_it(v):  # noqa: F811
    d = call(v, "ada", "post_to_board", {"kind": "need_help", "text": "Who knows the mail tool?"})
    post_id = v.log.last(kinds=["board_post"]).id
    assert d.result == f"posted on the work board as {post_id}"
    assert board.open_posts(v, "bo") == [{"id": post_id, "by": "ada", "kind": "need_help",
                                          "text": "Who knows the mail tool?",
                                          "ts": v.clock.now()}]
    d = call(v, "bo", "resolve_post", {"post": post_id, "note": "I do. See shared/bo/mail.md"})
    assert d.result == ("marked as dealt with: it has left the board and nobody sees it there "
                        "any more")
    assert board.open_posts(v) == []
    assert v.log.last(kinds=["board_resolved"]).data["poster"] == "ada"


def test_a_post_over_the_size_is_not_posted(v):  # noqa: F811
    e = refused(v, "ada", "post_to_board", {"kind": "handoff", "text": "x" * 1501})
    assert e.rule == "board_post_size" and not e.frustrate
    assert e.sentence == ("Nothing was posted: that is 1501 characters and a post takes 1500. "
                          "Post the part a coworker needs to act on. Anything long (a draft to "
                          "review) goes in a file in your shared folder, shared/<your id>/, "
                          "which your coworkers can read: name the file in the post.")
    assert e.args_logged == {"kind": "handoff", "length": 1501} and board.open_posts(v) == []
    v.core.settings.set("village.comms.board_post_chars", 2000, actor="owner")
    assert call(v, "ada", "post_to_board", {"kind": "handoff", "text": "x" * 1501}).result \
        .startswith("posted")


def test_only_a_board_post_can_be_resolved_and_a_card_id_points_to_correct_finding(v):  # noqa: F811
    other = call(v, "ada", "write_file", {"path": "a.md", "content": "x"}).event.id
    e = refused(v, "ada", "resolve_post", {"post": other, "note": "done"})
    assert e.rule == "board_post_only"
    assert e.sentence == (f"Nothing was marked: {other} is not a work-board post. Use the id "
                          "shown next to the post on the board, like evt-123.")
    e = refused(v, "ada", "resolve_post", {"post": "01JABCDEFGHJKMNPQRSTVWXYZ0", "note": "x"})
    assert e.sentence == ("Nothing was marked: 01JABCDEFGHJKMNPQRSTVWXYZ0 is not a work-board "
                          "post. It is 1 of the cards on the Bench: to fix one of your own, use "
                          "correct_finding.")


def test_a_reader_sees_the_newest_posts_by_others_whole_and_the_rest_cut(v):  # noqa: F811
    long = "word " * 100
    ids = []
    for n in range(6):
        board.post(v, "bo", "good_at", f"{n} {long}")
        ids.append(v.log.last(kinds=["board_post"]).id)
        v.later(1)
    board.post(v, "ada", "good_at", f"mine {long}")
    mine = v.log.last(kinds=["board_post"]).id
    shown = board.shown(v, "ada")
    assert ["shortened here" in shown[i] for i in ids] == [True, True, False, False, False,
                                                           False]
    assert shown[ids[0]].startswith("0 word") and "shortened here to save room" in shown[ids[0]]
    assert "shortened here" in shown[mine]  # its own post it already knows
    v.later(60 * 25)
    assert all("shortened here" in text for text in board.shown(v, "ada").values())
    assert len(board.open_posts(v, limit=3)) == 3


# --- what coworkers see -------------------------------------------------------------------------


def test_a_status_line_is_behaviour_never_numbers(v):  # noqa: F811
    call(v, "bo", "start_own_task", {"title": "Check the events page"})
    assert status.status_line(v, "bo").endswith("heads down on Check the events page")
    assert status.coworkers_text(v, "ada").startswith("# Your coworkers right now\n- Bo: ")
    b = v.world.agents["bo"]
    b.needs["frustration"] = 60
    assert status.status_line(v, "bo").startswith("short-tempered, ")
    b.needs["frustration"], b.needs["energy"] = 10, 20
    assert status.status_line(v, "bo").startswith("worn out, ")
    b.asleep, b.sleep_kind = True, "nap"
    assert status.status_line(v, "bo") == "napping"
    b.sleep_kind = "night"
    assert status.status_line(v, "bo") == "asleep"
    assert not any(ch.isdigit() for ch in status.coworkers_text(v, "ada"))


def test_an_agent_alone_has_no_coworkers_text(core):
    from jimmyhq.village.comms import benchlink
    from test_village_tools_fakes import FakeVillage

    alone = FakeVillage(core, agents=("ada",))
    assert status.coworkers_text(alone, "ada") == ""
    benchlink.close(alone)
