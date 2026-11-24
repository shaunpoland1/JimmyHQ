"""The beat: one turn of an agent, with a scripted model, fake tools and a simulated
clock. One test or more for each numbered loop rule."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jimmyhq.village.beat import act, pace, rest, resume_note, room, validate
from jimmyhq.village.beat.run import STAGES, beat, safe_beat
from jimmyhq.village.context.situation import reason_words
from jimmyhq.village.rules import RULES
from test_village_beat_fakes import (
    ME,
    Box,
    call,
    fails,
    kinds,
    make_village,
    owner_says,
    reply,
    step,
)

LOOP_RULES = (
    "asleep_no_beat", "rest_no_model", "owner_rouses_rest", "no_act_asleep", "refusal_retry",
    "reply_cut_off", "no_action_chosen", "unreadable_arguments", "argument_repair",
    "thought_first", "beat_fault", "heartbeat", "wake_early", "resume_note", "empty_room",
    "say_by_choice", "whole_answer_next_beat", "repeat_same_state", "task_set_aside",
    "page_wall", "night_tries",
)


def run(coro):
    return asyncio.run(coro)


def frustration(v, agent=ME) -> float:
    return v.world.agents[agent].needs["frustration"]


def refusals(v):
    return kinds(v, "validator_reject")


def test_every_numbered_loop_rule_is_a_named_function():
    import jimmyhq.village.intake  # noqa: F401 - registers its rule

    for name in LOOP_RULES:
        assert name in RULES and callable(RULES[name].fn), name


# ------------------------------------------------------------- end to end
def test_a_beat_runs_every_stage_in_order_and_does_the_chosen_action(core):
    v = make_village(core, call("write_file", path="notes/a.md", content="hello",
                                thought="I will start my notes."))
    wait = run(beat(v, ME))
    st, brain = v.state(ME), v.fakes["brain"]
    assert st.trace == list(STAGES)
    assert (v.files_dir(ME) / "notes" / "a.md").read_text(encoding="utf-8") == "hello"
    thought, action = kinds(v, "thought")[-1], kinds(v, "action")[-1]
    assert thought.data["text"] == "I will start my notes."
    assert thought.data["tool"] == "write_file" and "thought" not in thought.data["args"]
    assert action.data["tool"] == "write_file" and action.data["ok"] is True
    assert action.data["args"] == {"path": "notes/a.md"} and action.data["category"] == "work"
    assert wait == 10  # going well: the fastest pace
    assert (v.agent_dir(ME) / "state.json").is_file()
    asked = brain.calls[0]
    assert asked["tool_choice"] == "required" and asked["caller"] == "village.beat"
    assert asked["actor"] == "agent:ada" and asked["role"] == "default"
    assert asked["local_only"] is False and asked["max_tokens"] == 6000
    assert asked["temperature"] == 0.7 and "effort" not in asked
    offered = {t["name"] for t in asked["tools"]}
    assert {"write_file", "read_file", "write_diary", "focus_on"} <= offered
    assert "say" not in offered and "open_page" not in offered
    assert "For now you are called Ada" in asked["system"]
    text = asked["messages"][0]["content"]
    assert text.index("# Your priorities and tasks") < text.index("# Right now") \
        < text.index("# Your last actions") < text.index("# Your diary today") \
        < text.index("# How you feel") < text.index("# Time")
    assert text.endswith("Choose one action.")


def test_the_agents_own_model_voice_and_limits_are_used_for_the_call(core):
    v = make_village(core, call("write_diary", text="x"))
    v.set_cfg(ME, {"beat": {"provider_id": "home", "model": "m1"}, "local_only": True,
                   "capabilities": ["desktop"]})
    v.world.agents[ME].temperament["voice"]["temperature"] = 1.9
    core.settings.set("village.beat.effort", "low", actor="owner")
    run(beat(v, ME))
    asked = v.fakes["brain"].calls[0]
    assert (asked["provider_id"], asked["model"]) == ("home", "m1") and "role" not in asked
    assert asked["local_only"] is True and asked["effort"] == "low"
    assert asked["temperature"] == 0.85  # the voice, inside the owner's bounds
    assert "open_page" in {t["name"] for t in asked["tools"]}  # its capability


# ------------------------------------------------------------ rules 1 to 4
def asleep(v, agent=ME):
    a = v.world.agents[agent]
    a.asleep, a.sleep_kind, a.mode = True, "night", "sleep"
    return a


def test_a_sleeping_agent_has_no_model_call_and_waits_five_minutes(core):
    v = make_village(core)  # an empty script: any model call fails the test
    asleep(v)
    assert run(beat(v, ME)) == 300
    assert v.state(ME).trace == ["asleep", "save"] and v.fakes["brain"].calls == []


def test_a_sleeping_beat_asks_the_night_whether_memory_work_is_due(core):
    asked = []

    async def run_if_due(v, agent_id):
        asked.append(agent_id)

    v = make_village(core, **{"memory.night": SimpleNamespace(run_if_due=run_if_due)})
    asleep(v)
    run(beat(v, ME))
    run(beat(v, ME))
    assert asked == [ME, ME]


def test_a_night_that_fails_is_logged_and_the_loop_goes_on(core):
    async def run_if_due(v, agent_id):
        raise RuntimeError("no model answered")

    v = make_village(core, **{"memory.night": SimpleNamespace(run_if_due=run_if_due)})
    asleep(v)
    assert run(safe_beat(v, ME)) == 300
    fault = kinds(v, "platform_fault")[-1]
    assert fault.data["what"] == "night failed" and "no model answered" in fault.data["detail"]
    assert kinds(v, "brain_error") == []


def resting(v, agent=ME):
    a = v.world.agents[agent]
    a.mode, a.rest_until = "rest", v.clock.now() + 3600
    return a


def test_a_resting_agent_has_no_model_call_and_waits_five_minutes(core):
    v = make_village(core)
    resting(v)
    assert run(beat(v, ME)) == 300
    assert v.state(ME).trace[-2:] == ["rest", "save"] and v.fakes["brain"].calls == []
    assert rest.rest_wait("rest", False, 300) == 300
    assert rest.rest_wait("rest", True, 300) is None
    assert rest.rest_wait("work", False, 300) is None


def test_an_unanswered_owner_message_gets_a_resting_agent_up_for_one_beat(core):
    v = make_village(core, call("write_diary", text="The owner wrote; answering."))
    a = resting(v)
    message = owner_says(v, "Are you there?")
    run(beat(v, ME))
    assert len(v.fakes["brain"].calls) == 1 and a.rest_until is None
    change = kinds(v, "mode_change")[-1]
    assert change.data["cause"] == "owner_wrote" and change.data["evidence"] == [message.id]
    # Resting again with the same message still the newest: it was already roused for it.
    resting(v)
    assert run(beat(v, ME)) == 300 and len(v.fakes["brain"].calls) == 1


def test_a_message_the_agent_already_had_in_front_of_it_does_not_rouse_it(core):
    v = make_village(core)
    resting(v)
    owner_says(v, "Good work today.")
    st = v.state(ME)
    v.log.append("thought", ME, v.clock.now(), text="one", tool=None)
    assert rest.owner_waiting(v, st, ME) is not None  # one thought may have missed it
    v.log.append("thought", ME, v.clock.now(), text="two", tool=None)
    assert rest.owner_waiting(v, st, ME) is None
    assert run(beat(v, ME)) == 300


def test_an_agent_that_answered_is_not_roused_by_its_own_last_message(core):
    v = make_village(core)
    owner_says(v, "Are you there?")
    v.log.append("chat", ME, v.clock.now(), sender=ME, text="Yes.")
    assert rest.owner_waiting(v, v.state(ME), ME) is None


def test_a_reply_that_lands_after_sleep_began_is_dropped(core):
    def falls_asleep(messages, options):
        asleep(v)
        return call("write_file", path="late.md", content="too late")

    v = make_village(core, falls_asleep)
    assert run(beat(v, ME)) == 300
    assert kinds(v, "action") == [] and not (v.files_dir(ME) / "late.md").exists()
    assert "act" not in v.state(ME).trace
    assert validate.dropped_asleep(v.world.agents[ME]) is True


# ---------------------------------------------------------- rules 5 to 10
def test_a_refused_action_gets_another_go_in_the_same_beat_with_the_refusal_shown(core):
    v = make_village(core, call("finish_task", task="T9", _id="c1"),
                     call("write_diary", text="Not that one, then."))
    wait = run(beat(v, ME))
    calls = v.fakes["brain"].calls
    assert len(calls) == 2
    retry = calls[1]["messages"]
    assert retry[1]["role"] == "assistant" and retry[1]["tool_calls"][0]["id"] == "c1"
    result = retry[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_call_id"] == "c1"
    assert result["is_error"] is True
    assert result["content"].startswith("That was refused: T9 is not on your list")
    assert "Choose a different action now." in result["content"]
    assert calls[1]["tools"] == calls[0]["tools"]
    assert refusals(v)[-1].data["args"] == {"task": "T9"}  # what the tool chose to log
    assert kinds(v, "thought")[-1].data["retry"] is True
    assert kinds(v, "action")[-1].data["tool"] == "write_diary" and wait == 10
    assert v.state(ME).trace.count("think") == 2


def test_the_number_of_second_goes_is_a_setting(core):
    core.settings.set("village.beat.retries_after_refusal", 0, actor="owner")
    v = make_village(core, call("finish_task", task="T9"))
    run(beat(v, ME))
    assert len(v.fakes["brain"].calls) == 1


def test_a_real_refusal_frustrates_and_a_village_limit_does_not(core):
    core.settings.set("village.beat.retries_after_refusal", 0, actor="owner")
    v = make_village(core, call("message_owner", text="hello"), call("finish_task", task="T9"))
    before = frustration(v)
    run(beat(v, ME))
    assert frustration(v) == before  # the day's limit is the village's, not a mistake
    assert "nothing was sent" in refusals(v)[-1].data["reason"]
    run(beat(v, ME))
    assert frustration(v) > before


def test_a_reply_cut_off_mid_action_is_refused_with_advice_to_write_in_parts(core):
    v = make_village(core, call("write_file", path="a.md", content="x" * 50, _tokens=6000),
                     call("write_diary", text="In parts, then."))
    before = frustration(v)
    run(beat(v, ME))
    refused = refusals(v)[0]
    assert "cut off at the 6000-token limit" in refused.data["reason"]
    assert "Write long text in parts" in refused.data["reason"]
    assert "nothing happened" in refused.data["reason"]
    assert frustration(v) == before and not (v.files_dir(ME) / "a.md").exists()
    assert kinds(v, "thought")[0].data["cut_off"] is True


def test_a_reply_cut_off_while_still_thinking_is_told_to_think_less(core):
    core.settings.set("village.beat.max_reply_tokens", 2000, actor="owner")
    v = make_village(core, fails("truncated"), call("write_diary", text="Shorter."))
    before = frustration(v)
    run(beat(v, ME))
    reason = refusals(v)[0].data["reason"]
    assert "2000-token limit while you were still thinking" in reason
    assert "Think less before acting" in reason and frustration(v) == before
    # No call came back to answer, so the refusal is added to what the agent read.
    assert v.fakes["brain"].calls[1]["messages"][0]["content"].endswith(
        "Choose a different action now.")
    assert validate.cut_off_sentence(6000, chose_tool=False) != \
        validate.cut_off_sentence(6000, chose_tool=True)


def test_a_reply_that_chooses_no_action_is_refused_without_frustration(core):
    for slip in (reply("I think I will just think."), fails("no_tools")):
        v = make_village(core, slip, call("write_diary", text="Acting now."))
        before = frustration(v)
        run(beat(v, ME))
        refused = refusals(v)[0]
        assert refused.data["tool"] == "(none)"
        assert "did not choose an action, so nothing happened" in refused.data["reason"]
        assert frustration(v) == before and len(v.fakes["brain"].calls) == 2


def test_arguments_that_cannot_be_read_are_refused_without_frustration(core):
    slips = [
        (call("fly", height=3), "there is no tool called fly"),
        (call("say", text="hello"), "say is not available right now (work)"),
        (call("write_file", path="a.md"), "write_file needs content"),
        (call("post_to_board", kind="rumour", text="x"),
         "post_to_board: kind must be one of need_help, good_at"),
        (call("read_file", path="a.md", start="soon"), "read_file: start should be a integer"),
        (fails("bad_tool_call"), "its arguments could not be read"),
    ]
    for slip, words in slips:
        core.settings.set("village.beat.retries_after_refusal", 0, actor="owner")
        v = make_village(core, slip)
        before = frustration(v)
        run(beat(v, ME))
        reason = refusals(v)[-1].data["reason"]
        assert words in reason and "nothing happened" in reason, reason
        assert frustration(v) == before and kinds(v, "action") == []


def test_a_refusal_for_arguments_names_the_tool_they_fit(core):
    core.settings.set("village.beat.retries_after_refusal", 0, actor="owner")
    v = make_village(core, call("write_diary", path="a.md", content="my notes"))
    run(beat(v, ME))
    assert ("write_diary needs text. Those arguments fit write_file: use that instead"
            in refusals(v)[-1].data["reason"])


def test_garbled_arguments_are_mended_before_they_are_judged(core):
    mended = [
        call("write_diary", arguments='{"text": "wrapped"}', thought=""),
        call("write_file", file="a.md", body="aliases"),
        call("read_file", path="a.md", start=""),
        call("write_diary", thought='Noting it.</thinking><parameter name="text">in markup'),
    ]
    v = make_village(core, *mended)
    for _ in mended:
        run(beat(v, ME))
    assert refusals(v) == []
    done = [e.data["tool"] for e in kinds(v, "action")]
    assert done == ["write_diary", "write_file", "read_file", "write_diary"]
    assert (v.files_dir(ME) / "a.md").read_text(encoding="utf-8") == "aliases"
    last = kinds(v, "thought")[-1].data
    assert last["text"] == "Noting it." and last["args"] == {"text": "in markup"}


def test_every_tool_offered_takes_the_thought_as_its_first_required_argument(core):
    v = make_village(core, call("write_diary", text="x"))
    run(beat(v, ME))
    for schema in v.fakes["brain"].calls[0]["tools"]:
        assert schema["parameters"]["required"][0] == "thought", schema["name"]
        assert next(iter(schema["parameters"]["properties"])) == "thought"


def test_what_goes_in_the_log_is_shortened_and_has_no_secrets_in_it(core):
    key = "sk-" + "a1B2c3D4" * 5
    clipped = validate.clip_args({"content": "x" * 5000, "text": f"the key is {key}", "n": 3})
    assert len(clipped["content"]) == 300 and clipped["n"] == 3
    assert key not in str(clipped) and "looked like a secret" in clipped["text"]


# ------------------------------------------------------------ rule 11
def test_a_beat_that_raises_is_logged_and_waits_two_minutes(core):
    v = make_village(core, call("post_to_board", kind="need_help", text="x"),
                     call("write_diary", text="Still here."))
    assert run(safe_beat(v, ME)) == 120
    error = kinds(v, "brain_error")[-1]
    assert error.data["error"] == "RuntimeError: the tool broke"
    assert v.state(ME).busy is False
    assert run(safe_beat(v, ME)) == 10  # the next beat is an ordinary one


def test_a_model_call_that_fails_is_logged_and_waits_two_minutes(core):
    v = make_village(core, fails("unreachable"))
    assert run(safe_beat(v, ME)) == 120
    fault = kinds(v, "platform_fault")[-1]
    assert fault.data["what"] == "model call failed"
    assert kinds(v, "action") == [] and kinds(v, "brain_error") == []


def test_an_agent_with_no_tools_on_offer_waits_instead_of_calling_a_model(core):
    v = make_village(core, tools=SimpleNamespace(ALL=[]))
    assert run(beat(v, ME)) == 300
    assert kinds(v, "platform_fault")[-1].data["what"] == "no tools on offer"


# ------------------------------------------------------------ rule 12
def test_the_heartbeat_is_fastest_when_going_well_and_slows_when_frustrated_or_tired():
    beat_s = pace.heartbeat_seconds
    assert beat_s(10, 300, True, "work", 10, 80) == 10
    assert beat_s(10, 300, False, "work", 10, 80) == 39  # 10 + 290 * 0.1
    assert beat_s(10, 300, True, "social", 10, 80) == 39
    assert beat_s(10, 300, True, "work", 80, 80) == 68  # + (80 - 50) / 150
    assert beat_s(10, 300, True, "work", 10, 20) == 68  # + (40 - 20) / 100
    assert beat_s(10, 300, False, "work", 100, 0) == 252
    assert beat_s(10, 300, False, "work", 500, -500) == 300  # never past the slowest


def test_the_heartbeat_bounds_come_from_settings(core):
    core.settings.set("village.heartbeat_min_s", 20, actor="owner")
    core.settings.set("village.heartbeat_max_s", 120, actor="owner")
    v = make_village(core, call("write_diary", text="x"), reply("no action"), reply("none"))
    assert run(beat(v, ME)) == 20
    assert run(beat(v, ME)) == 30


# ------------------------------------------------------------ rule 13
def test_a_wake_ends_the_wait_between_beats_early(core):
    v = make_village(core)

    async def waits():
        st = v.state(ME)
        asyncio.get_running_loop().call_later(0.01, lambda: v.wake(ME))
        early = await pace.wait(st, 30)
        late = await pace.wait(st, 0.01)
        return early, late

    assert run(waits()) == (True, False)


def test_something_said_in_the_break_room_wakes_every_awake_coworker(core):
    v = make_village(core, call("say", text="Anyone about?"), agents=(ME, "ben", "cy"))
    asleep(v, "cy")
    run(beat(v, ME))
    assert kinds(v, "action")[-1].data["tool"] == "say"
    assert v.state("ben").wake.is_set() and not v.state("cy").wake.is_set()
    assert not v.state(ME).wake.is_set()


# ------------------------------------------------------------ rule 14
def force_aside(v, task="T1"):
    a = v.world.agents[ME]
    v.world.choose_task(ME, task)
    v.world.act(ME, "write_file", {"path": "a.md"}, None, "error: disk full", False, task)
    while task not in a.parked:
        v.world.refuse(ME, "write_file", {"path": "a.md"}, "no", task)
    return a


def test_a_task_forced_aside_gets_one_short_note_for_next_time(core):
    v = make_village(core, reply("  Try the other folder first.  " + "x" * 600),
                     call("write_diary", text="Moving on."))
    assert resume_note.unhandled(v, v.state(ME), ME) == []  # the loop has started
    a = force_aside(v)
    run(beat(v, ME))
    asked = v.fakes["brain"].calls[0]
    assert asked["max_tokens"] == 200 and "tools" not in asked
    assert asked["caller"] == "village.resume_note"
    assert "too frustrated with 'T1'" in asked["messages"][0]["content"]
    assert "write_file" in asked["messages"][0]["content"]
    note = a.parked["T1"]["resume_note"]["next_guess"]
    assert note.startswith("Try the other folder first.") and len(note) == 500
    assert kinds(v, "resume_note_written")[-1].data["task"] == "T1"
    assert len(v.fakes["brain"].calls) == 2  # one note, then the beat's own call


def test_a_task_the_agent_put_down_itself_needs_no_note_and_old_ones_are_not_redone(core):
    v = make_village(core, call("write_diary", text="x"), call("write_diary", text="y"))
    force_aside(v)  # before the loop first looked: already in the past
    run(beat(v, ME))
    v.world.set_aside(ME, "P1", "back to this tomorrow")
    run(beat(v, ME))
    assert kinds(v, "resume_note_written") == [] and len(v.fakes["brain"].calls) == 2


def test_a_note_that_cannot_be_written_does_not_cost_the_beat(core):
    v = make_village(core, fails("unreachable"), call("write_diary", text="Carrying on."))
    resume_note.unhandled(v, v.state(ME), ME)
    a = force_aside(v)
    run(beat(v, ME))
    assert a.parked["T1"]["resume_note"]["next_guess"] is None
    assert kinds(v, "action")[-1].data["tool"] == "write_diary"


# ------------------------------------------------------- rules 15 and 16
def test_a_room_nobody_answers_in_closes_once_for_that_silence(core):
    v = make_village(core, agents=(ME, "ben"))
    a = v.world.agents[ME]
    first = v.log.append("breakroom", ME, v.clock.now(), text="Hello?")
    v.clock.advance(10)
    assert room.check_empty_room(v, a) is False  # under the fifteen-minute wait
    v.clock.advance(6)
    assert room.check_empty_room(v, a) is True
    closed = kinds(v, "social_unanswered")
    assert len(closed) == 1 and closed[0].data["evidence"] == [first.id]
    assert a.social_cooldown_until == v.clock.now() + 60 * 60
    v.clock.advance(30)
    assert room.check_empty_room(v, a) is False  # still closed
    v.clock.advance(45)
    assert room.check_empty_room(v, a) is False  # the same silence does not close it twice
    assert len(kinds(v, "social_unanswered")) == 1


def test_a_room_where_someone_answered_stays_open(core):
    v = make_village(core, agents=(ME, "ben"))
    v.log.append("breakroom", ME, v.clock.now(), text="Hello?")
    v.log.append("breakroom", "ben", v.clock.now(), text="Hello!")
    v.clock.advance(30)
    assert room.check_empty_room(v, v.world.agents[ME]) is False


def test_say_is_offered_outside_social_mode_when_someone_is_awake_and_there_is_room(core):
    v = make_village(core, agents=(ME, "ben"))
    a = v.world.agents[ME]
    assert a.needs["social"] == 55 and room.can_say(v, ME) is True
    assert room.extra_tools(v, ME, "work") == ("say",)
    assert room.extra_tools(v, ME, "social") == ()  # social mode offers it anyway
    a.needs["social"] = 82.5  # full is 85 and the room is 3: just over
    assert room.can_say(v, ME) is False
    core.settings.set("village.beat.say_room", 1, actor="owner")
    assert room.can_say(v, ME) is True
    asleep(v, "ben")
    assert room.can_say(v, ME) is False  # nobody awake to hear it


def test_an_agent_alone_in_the_village_is_not_offered_say(core):
    v = make_village(core)
    assert room.can_say(v, ME) is False and room.extra_tools(v, ME, "work") == ()


def test_a_coworker_who_just_spoke_can_be_answered_even_when_full(core):
    v = make_village(core, call("say", text="Morning!"), agents=(ME, "ben"))
    v.world.agents[ME].needs["social"] = 84
    v.log.append("breakroom", "ben", v.clock.now(), text="Morning, Ada.")
    v.clock.advance(9)
    assert room.can_say(v, ME) is True
    run(beat(v, ME))
    assert "say" in {t["name"] for t in v.fakes["brain"].calls[0]["tools"]}
    assert room.invited(v, ME) is False  # answered: no longer invited
    v.log.append("breakroom", "ben", v.clock.now(), text="How is the work?")
    v.clock.advance(11)
    assert room.can_say(v, ME) is False  # past the ten minutes


# ------------------------------------------------------------ rule 17
def test_which_answers_are_shown_whole_next_beat():
    whole = act.whole_answer
    assert whole("self", "write_diary", {}, "x" * 201, True) is True
    assert whole("self", "write_diary", {}, "x" * 200, True) is False
    assert whole("files", "read_file", {"path": "a.md"}, "x" * 9000, True) is False
    assert whole("files", "read_file", {"path": "a.md", "start": 30000}, "x", True) is True
    assert whole("files", "read_file", {"path": "a.md", "start": ""}, "x", True) is False
    assert whole("files", "read_file", {"path": "gone.md"}, "no such file", False) is True


def test_a_long_answer_is_in_front_of_the_agent_whole_on_the_next_beat(core):
    long_text = "A long entry. " * 30 + "THE END"
    v = make_village(core, call("write_diary", text=long_text), call("write_diary", text="ok"),
                     call("write_diary", text="ok"))
    run(beat(v, ME))
    assert v.state(ME).last_result == ("write_diary", long_text)
    assert not kinds(v, "action")[-1].data["result"].endswith("THE END")  # the log keeps a line
    run(beat(v, ME))
    seen = v.fakes["brain"].calls[1]["messages"][0]["content"]
    assert "# What came back from write_diary\n" + long_text in seen
    assert v.state(ME).last_result is None
    run(beat(v, ME))
    assert "# What came back from" not in v.fakes["brain"].calls[2]["messages"][0]["content"]


def test_a_failed_file_action_and_a_refused_one_are_shown_whole_next_beat(core):
    core.settings.set("village.beat.retries_after_refusal", 0, actor="owner")
    v = make_village(core, call("read_file", path="gone.md"))
    before = frustration(v)
    run(beat(v, ME))
    assert v.state(ME).last_result == ("read_file", "there is no file called gone.md")
    assert kinds(v, "action")[-1].data["ok"] is False and frustration(v) > before


# ------------------------------------------------------------ rule 18
def test_the_same_action_against_the_same_state_is_refused_by_the_worlds_guard(core):
    v = make_village(core)
    a, tool = v.world.agents[ME], v.tools()[3]  # write_diary

    async def twice():
        first = await act.act(v, v.state(ME), a, tool, {"text": "x"}, "work", before="screen")
        second = await act.act(v, v.state(ME), a, tool, {"text": "x"}, "work", before="screen")
        third = await act.act(v, v.state(ME), a, tool, {"text": "x"}, "work", before="other")
        return first, second, third

    assert run(twice()) == (True, False, True)
    assert refusals(v)[-1].data["reason"] == "repeat_same_state"
    assert reason_words("repeat_same_state").startswith("you just did exactly this")
    assert len(kinds(v, "action")) == 2


def test_an_action_on_a_task_that_is_set_aside_is_refused_before_it_happens(core):
    v = make_village(core)
    a = v.world.agents[ME]
    a.current_task = "T1"
    a.parked["T1"] = {"since": v.clock.now(), "resume_note": {}}
    done = run(act.act(v, v.state(ME), a, v.tools()[0], {"path": "a.md", "content": "x"}, "work"))
    assert done is False and not (v.files_dir(ME) / "a.md").exists()
    assert refusals(v)[-1].data["reason"] == "task_set_aside"
    assert reason_words("task_set_aside") == "that task is set aside for now"


def test_a_page_that_is_a_wall_is_refused_with_words_that_say_what_to_do(core):
    v = make_village(core)
    v.world.agents[ME].wall_streak = int(v.S("guards.wall_after"))
    refused = v.world.check(ME, "click", {"element": 3}, "the same page")
    assert refused.data["reason"] == "page_wall"
    assert "it is a wall, not a puzzle" in reason_words("page_wall")
    assert reason_words("anything a tool said") == "anything a tool said"


def test_an_action_that_changes_the_task_is_not_recorded_against_the_old_one(core):
    v = make_village(core, call("focus_on", task="T1"), call("write_diary", text="x"))
    a = v.world.agents[ME]
    a.current_task = "P1"
    run(beat(v, ME))
    assert a.current_task == "T1" and kinds(v, "action")[-1].data["task"] is None
    run(beat(v, ME))
    assert kinds(v, "action")[-1].data["task"] == "T1" and a.current_task == "T1"


def test_held_work_is_flushed_before_the_agent_decides_anything(core):
    work, findings = Box(flush=None, queued=[], review_wait=[]), Box(flush=None, queued=[])
    v = make_village(core, call("write_diary", text="x"),
                     **{"comms.work": work, "comms.findings": findings})
    run(beat(v, ME))
    assert work.calls[0] == "flush" and findings.calls[0] == "flush"


def test_time_passing_in_the_world_does_not_disturb_a_beat(core):
    v = make_village(core, call("write_diary", text="x"), call("write_diary", text="y"))
    run(beat(v, ME))
    step(v, 5, times=3)
    assert run(beat(v, ME)) == 10 and len(kinds(v, "action")) == 2
