"""The village world: bodies, guards, sleep and the honesty check, on a simulated clock."""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jimmyhq.village.world import MemoryLog, RealClock, SimClock, World, divergence, getter, state
from jimmyhq.village.world.clock import local_hour
from jimmyhq.village.world.temperament import roll, roll_group

TZ = "Europe/Paris"


def make_world(hour=10, overrides=None, tz=TZ, log=None):
    clock = SimClock(datetime(2026, 11, 9, hour, 0, tzinfo=ZoneInfo(tz)), tz)
    w = World(getter(overrides), log or MemoryLog(), clock)
    t = roll(random.Random(1))
    t["frustration_tolerance"] = 1.0
    t["sleep_offset_minutes"] = 0
    w.add_agent("a", t)
    return w


def step(w, minutes, times=1):
    for _ in range(times):
        w.clock.advance(minutes)
        w.advance(minutes)


def phrases(w):
    return [f["phrase"] for f in w.feelings("a")]


# --- settings and the clock --------------------------------------------------------


def test_a_changed_setting_takes_effect_on_the_next_action_without_a_restart():
    overrides = {}
    w = make_world(overrides=overrides)
    assert w.S("guards.repeat_window") == 6 and w.agents["a"].guard.window == 6
    overrides["guards.repeat_window"] = 2
    w.act("a", "click", {"el": 1}, before="s1", result="r1", ok=True)
    assert w.agents["a"].guard.window == 2
    w.act("a", "click", {"el": 2}, before="s2", result="r2", ok=True)
    w.act("a", "click", {"el": 3}, before="s3", result="r3", ok=True)
    # the first click has left the two-action window, so it is not a repeat any more
    assert w.check("a", "click", {"el": 1}, "s1") is None
    assert w.check("a", "click", {"el": 3}, "s3") is not None


def test_a_setting_the_world_does_not_know_is_an_error_not_a_silent_default():
    w = make_world()
    with pytest.raises(KeyError):
        w.S("energy.no_such_number")


def test_the_clock_reads_local_time_in_the_time_zone_it_is_given():
    noon_utc = datetime(2026, 11, 9, 12, 0, tzinfo=ZoneInfo("UTC"))
    assert local_hour(SimClock(noon_utc, "UTC")) == 12
    assert local_hour(SimClock(noon_utc, "Asia/Tokyo")) == 21
    assert local_hour(SimClock(noon_utc, "America/Chicago")) == 6
    assert SimClock(noon_utc, "Asia/Tokyo").now() == noon_utc.timestamp()


def test_the_real_clock_follows_a_time_zone_that_changes_while_it_runs():
    zone = {"name": "Asia/Tokyo"}
    clock = RealClock(lambda: zone["name"])
    assert clock.local_time().utcoffset().total_seconds() == 9 * 3600
    zone["name"] = "UTC"
    assert clock.local_time().utcoffset().total_seconds() == 0
    assert RealClock("").local_time().tzinfo is not None  # empty: this computer's own zone


def test_each_agent_keeps_its_own_body_clock_offset():
    w = make_world(hour=23)
    late = roll(random.Random(2))
    late["sleep_offset_minutes"] = 60
    w.add_agent("b", late)
    step(w, 5)
    assert w.agents["a"].asleep  # 23:05 by its own clock: bedtime
    assert not w.agents["b"].asleep  # 22:05 by its own clock: still up


# --- the honest body -----------------------------------------------------------------


def test_every_need_change_has_a_cause_and_evidence():
    w = make_world()
    rng = random.Random(7)
    for _ in range(50):
        step(w, 5)
        w.act("a", "click", {"x": rng.random()}, before=None, result=str(rng.random()),
              ok=rng.random() > 0.3, task="t")
    changes = [e for e in w.log.events if e.kind == "need_change"]
    assert changes and all(e.data["cause"] and e.data["evidence"] for e in changes)


def test_an_unverified_completion_gives_no_purpose():
    w = make_world()
    p = w.agents["a"].needs["purpose"]
    w.complete("a", "t", verified_by=None)
    assert w.agents["a"].needs["purpose"] == p
    assert w.log.events[-1].kind == "claim_unverified"


def test_a_verified_completion_gives_purpose_and_relief():
    w = make_world()
    a = w.agents["a"]
    a.needs["frustration"] = 40.0
    ev = w.act("a", "edit_file", {"path": "d.md"}, before=None, result="edited", ok=True,
               task="t")
    p, f = a.needs["purpose"], a.needs["frustration"]
    w.complete("a", "t", verified_by=[ev.id])
    assert a.needs["purpose"] == pytest.approx(p + w.S("purpose.verified_completion"))
    assert a.needs["frustration"] == pytest.approx(f - w.S("frustration.small_win"))
    assert a.current_task is None


def test_body_feelings_cite_real_events_and_pass_the_divergence_check():
    w = make_world()
    for i in range(6):
        w.act("a", "click", {"el": i % 2}, before=f"s{i}", result="error", ok=False, task="t")
    assert "stuck" in phrases(w)
    claim = next(e for e in reversed(w.log.events)
                 if e.kind == "feeling" and e.data["phrase"] == "stuck")
    assert all(w.log.get(i) for i in claim.data["evidence"])
    assert divergence.check_claim(w.log, claim, w.S) is None


def test_a_stuck_claim_made_during_steady_progress_is_flagged_as_a_platform_fault():
    w = make_world()
    for i in range(6):
        w.act("a", "edit_file", {"path": f"d{i}.md"}, before=None, result=f"edited d{i}",
              ok=True, task=f"t{i}")
    claim = w.log.append("feeling", "a", w.clock.now(), phrase="stuck", evidence=[],
                         source="model")
    flag = divergence.check_claim(w.log, claim, w.S)
    assert flag and flag["type"] == "claim_contradicted" and flag["fault"] == "platform"


def test_mood_is_computed_from_needs_and_stays_between_minus_one_and_one():
    w = make_world()
    a = w.agents["a"]
    good = w.mood(a)[0]
    a.needs.update(energy=5, frustration=95, social=5, curiosity=95, purpose=5)
    bad = w.mood(a)[0]
    assert -1 <= bad < good <= 1


# --- repeat guard --------------------------------------------------------------------


def test_a_repeat_is_the_same_action_on_the_same_state_not_just_the_same_action():
    w = make_world()
    # scrolling: same action, new state every time -> allowed
    for i in range(10):
        assert w.act("a", "scroll", {"d": "down"}, before=f"rows {i}", result=f"rows {i + 1}",
                     ok=True)
    # clicking the same thing on the same screen -> refused
    assert w.act("a", "click", {"el": 3}, before="screen X", result="screen X", ok=True)
    assert w.act("a", "click", {"el": 3}, before="screen X", result="screen X", ok=True) is None
    rej = [e for e in w.log.events if e.kind == "validator_reject"]
    assert len(rej) == 1 and rej[0].data["reason"] == "repeat_same_state"


def test_clock_times_on_a_screen_do_not_defeat_the_repeat_guard():
    w = make_world()
    w.act("a", "click", {"el": 1}, before="status 10:31 AM ok", result="r", ok=True)
    assert w.act("a", "click", {"el": 1}, before="status 10:32 AM ok", result="r",
                 ok=True) is None


def test_closing_the_last_tab_is_not_a_repeat_with_nothing_new():
    # Every close of the last tab ends on the same blank screen. That is tidying up, not
    # a rut, so it must not be charged as "same action, nothing new".
    w = make_world()
    for page in ("first page", "second page", "third page"):
        assert w.act("a", "close_tab", {"tab": 1}, before=page, result="blank", ok=True)
    assert not any(e.data["cause"] == "same_action_no_new_result" for e in w.log.events
                   if e.kind == "need_change")
    # closing an already-blank screen twice is still refused by the before-state guard
    w.act("a", "close_tab", {"tab": 1}, before="blank", result="blank", ok=True)
    assert w.act("a", "close_tab", {"tab": 1}, before="blank", result="blank", ok=True) is None


def test_waking_forgets_last_nights_repeats():
    # A page that timed out in the evening must be allowed again in the morning, even if
    # the screen it is opened from looks the same.
    w = make_world(hour=20)
    a = w.agents["a"]
    w.act("a", "open_page", {"url": "u"}, before="listing", result="timeout", ok=False)
    assert w.check("a", "open_page", {"url": "u"}, "listing") is not None
    slept = False
    for _ in range(12 * 14):
        step(w, 5)
        slept |= a.asleep
        if slept and not a.asleep:
            break
    assert slept and not a.asleep
    assert w.check("a", "open_page", {"url": "u"}, "listing") is None


# --- page wall -----------------------------------------------------------------------


def test_a_page_becomes_a_wall_after_fruitless_browser_actions():
    w = make_world()
    a = w.agents["a"]
    for i in range(4):  # four clicks that go nowhere, on changing screens (so no repeat)
        w.act("a", "click", {"element": 21}, before=f"screen {i}",
              result="it would not take the click", ok=False)
    assert w.at_wall(a)
    assert w.act("a", "click", {"element": 19}, before="screen 9", result="new", ok=True) is None
    rej = [e for e in w.log.events if e.kind == "validator_reject"][-1]
    assert rej.data["reason"] == "page_wall" and rej.data["args"] == {"element": 19}
    assert w.act("a", "press_key", {"key": "Escape"}, before="screen 9", result="new",
                 ok=True) is None
    # tools that do not touch the browser are never walled
    assert w.act("a", "write_file", {"path": "notes.md"}, before=None, result="wrote", ok=True)
    assert w.at_wall(a)
    # leaving the page is always allowed, and a page that gives something new clears the wall
    assert w.act("a", "open_page", {"url": "https://example.org/b"}, before="screen 9",
                 result="opened b", ok=True)
    assert not w.at_wall(a)
    assert w.act("a", "click", {"element": 1}, before="b", result="clicked [1]", ok=True)


def test_the_page_wall_counts_refusals_and_nothing_new_and_still_allows_scrolling():
    w = make_world()
    a = w.agents["a"]
    w.act("a", "click", {"element": 1}, before="s0", result="same", ok=True)
    w.act("a", "click", {"element": 1}, before="s1", result="same", ok=True)  # nothing new: 1
    for _ in range(3):  # exact repeats are refused: 2, 3, 4
        assert w.act("a", "click", {"element": 1}, before="s1", result="same", ok=True) is None
    assert w.at_wall(a)
    assert w.act("a", "scroll", {"direction": "down"}, before="s1", result="part 2", ok=True)
    for i in range(2, 6):
        w.act("a", "click", {"element": 2}, before=f"p{i}", result="x", ok=False)
    assert w.at_wall(a)
    w._sleep(a, "nap", [w.log.events[-1].id])
    assert a.wall_streak == 0  # sleep starts the count again


# --- feelings ------------------------------------------------------------------------


def test_three_fruitless_actions_in_a_row_feel_stuck_even_without_a_task():
    w = make_world()
    for i in range(3):
        w.act("a", "click", {"element": 2}, before=f"s{i}", result="would not take the click",
              ok=False)
    assert "stuck" in phrases(w)


def test_refusals_with_no_task_between_real_work_do_not_feel_stuck():
    w = make_world()
    for i in range(3):
        w.refuse("a", "lookup", {"what": "lists"}, "you have used all of today's look-ups")
        w.act("a", "edit_file", {"path": f"d{i}.md"}, before=None, result=f"edited d{i}",
              ok=True, task="t")
    assert "stuck" not in phrases(w)
    for _ in range(3):  # three in a row with nothing between still is stuck
        w.refuse("a", "lookup", {"what": "lists"}, "you have used all of today's look-ups")
    assert "stuck" in phrases(w)


def test_a_stuck_streak_broken_by_real_work_no_longer_feels_stuck():
    # Only a streak still running counts. One good action ends it at once, not at the
    # end of the hour.
    w = make_world()
    for i in range(3):
        w.act("a", "click", {"element": 2}, before=f"s{i}", result="would not take the click",
              ok=False)
    assert "stuck" in phrases(w)
    w.act("a", "edit_file", {"path": "d.md"}, before=None, result="edited d", ok=True)
    assert "stuck" not in phrases(w)


# --- frustration, set-asides and waiting -------------------------------------------


def test_a_real_file_change_while_working_calms_a_little_but_never_below_the_floor():
    # With no task on the list there are no finished-task wins, so without this
    # frustration could only climb.
    w = make_world()
    a = w.agents["a"]
    a.needs["frustration"] = 40.0
    w.act("a", "write_file", {"path": "x.md", "content": "one"}, before=None,
          result="created x.md", ok=True)
    assert a.needs["frustration"] == pytest.approx(40.0 - w.S("frustration.progress_relief"))
    # reading is not a win, and neither is a failed write
    w.act("a", "read_file", {"path": "x.md"}, before=None, result="one", ok=True)
    w.act("a", "write_file", {"path": "y.md", "content": "a"}, before=None, result="too big",
          ok=False)
    assert a.needs["frustration"] > 40.0 - w.S("frustration.progress_relief")
    f = a.needs["frustration"]
    w.act("a", "add_to_file", {"path": "x.md", "content": "two"}, before=None, result="added",
          ok=True)
    assert a.needs["frustration"] < f
    # never below the resting floor
    a.needs["frustration"] = w.S("frustration.relief_floor")
    w.act("a", "write_file", {"path": "z.md", "content": "z"}, before=None,
          result="created z.md", ok=True)
    assert a.needs["frustration"] == pytest.approx(w.S("frustration.relief_floor"))


def test_a_task_that_keeps_failing_is_set_aside_with_a_note_and_comes_back_when_calm():
    w = make_world()
    a = w.agents["a"]
    for i in range(30):
        w.act("a", "click", {"el": i % 3}, before=f"s{i}", result="error: not found", ok=False,
              task="trap")
        if "trap" in a.parked:
            break
    assert "trap" in a.parked and w.parked_tasks("a") == {"trap"}
    note = a.parked["trap"]["resume_note"]
    assert note["trying"] == "trap" and note["tried"]
    assert w.act("a", "click", {"el": 99}, before="new", result="x", ok=True, task="trap") is None
    assert w.log.events[-2].data["reason"] == "task_set_aside"
    a.needs["frustration"] = 10
    step(w, 5)
    assert "trap" not in a.parked
    assert any(e.kind == "resume" and e.data["resume_note"]["trying"] == "trap"
               for e in w.log.events)


def test_a_task_picked_back_up_by_choice_gets_a_margin_before_it_is_set_aside_again():
    w = make_world()
    a = w.agents["a"]
    w.set_aside("a", "t", "try the other form")
    assert a.parked["t"]["resume_note"]["next_guess"] == "try the other form"
    a.needs["frustration"] = 55.0  # already over the set-aside level
    w.resume_by_choice("a", "t")
    assert a.current_task == "t" and a.set_aside_floor == pytest.approx(67.0)
    w.act("a", "click", {"el": 1}, before="s1", result="no", ok=False, task="t")  # +5 -> 60
    assert "t" not in a.parked
    w.act("a", "click", {"el": 2}, before="s2", result="nope", ok=False, task="t")
    w.act("a", "click", {"el": 3}, before="s3", result="still no", ok=False, task="t")  # 70
    assert "t" in a.parked


def test_a_task_chosen_while_frustrated_gets_the_same_margin():
    w = make_world()
    a = w.agents["a"]
    a.needs["frustration"] = 55.0
    w.choose_task("a", "req")
    assert a.current_task == "req"
    assert a.set_aside_floor == pytest.approx(55.0 + w.S("frustration.chosen_resume_margin"))
    step(w, 1)
    assert "req" not in a.parked


def test_frustration_from_waiting_is_capped():
    w = make_world()
    for i in range(20):
        w.submit("a", f"item-{i}")
    start = w.agents["a"].needs["frustration"]
    step(w, 5, times=12 * 24)  # a whole day, sleep included
    caused = sum(x["caused"] for x in w.agents["a"].waiting.values())
    assert caused <= w.S("frustration.wait_cap_total") + 1e-9
    assert w.agents["a"].needs["frustration"] - start <= w.S("frustration.wait_cap_total") + 1e-9


def test_an_answer_from_the_bench_ends_the_wait_and_gives_purpose():
    w = make_world()
    a = w.agents["a"]
    w.submit("a", "draft")
    assert "draft" in a.waiting
    p = a.needs["purpose"]
    w.respond("a", "draft", "approved")
    assert "draft" not in a.waiting
    assert a.needs["purpose"] == pytest.approx(p + w.S("purpose.acknowledgement"))


# --- company -------------------------------------------------------------------------


def test_a_lone_agent_with_no_channel_to_the_owner_has_a_dormant_social_need():
    w = make_world()
    a = w.agents["a"]
    social = a.needs["social"]
    step(w, 5, times=12)
    assert a.needs["social"] == social  # nobody to talk to: draining it would be a lie
    w.owner_channel_open = lambda: True
    step(w, 5)
    assert a.needs["social"] < social


def test_a_message_fills_the_social_need_and_the_listener_gets_a_share():
    w = make_world()
    w.add_agent("b", dict(w.agents["a"].temperament))
    for agent in w.agents.values():
        agent.needs["social"] = 40.0
    w.message("a", "b")
    w.message("b", "a", heard=True)
    said = w.agents["a"].needs["social"] - 40.0
    heard = w.agents["b"].needs["social"] - 40.0
    assert said > 0 and heard == pytest.approx(said * w.S("social.heard_share"))


def test_an_empty_break_room_sends_the_agent_back_to_work_for_a_while():
    w = make_world()
    w.owner_channel_open = lambda: True
    a = w.agents["a"]
    a.needs["social"] = 10.0
    assert w.pick_mode(a) == "social"
    w.social_unanswered("a", [w.log.events[-1].id])
    assert a.mode == "work" and w.pick_mode(a) == "work"
    w.clock.advance(w.S("social.empty_room_cooldown_min") + 1)
    assert w.pick_mode(a) == "social"


# --- modes ---------------------------------------------------------------------------


def test_modes_follow_the_needs_and_hold_until_the_need_has_recovered():
    w = make_world()
    a = w.agents["a"]
    assert w.pick_mode(a) == "work"
    a.needs["frustration"] = 62.0
    assert w.pick_mode(a) == "outlet"
    a.needs["frustration"] = 45.0
    assert w.pick_mode(a) == "outlet"  # still within 20 of the trigger
    a.needs["frustration"] = 41.0
    assert w.pick_mode(a) == "work"
    a.needs["curiosity"] = 75.0
    assert w.pick_mode(a) == "explore"
    a.needs["curiosity"] = 51.0
    assert w.pick_mode(a) == "explore"
    a.needs["curiosity"] = 49.0
    assert w.pick_mode(a) == "work"
    a.needs["energy"] = 19.0
    assert w.pick_mode(a) == "rest"
    a.needs["energy"] = 39.0
    assert w.pick_mode(a) == "rest"
    a.needs["energy"] = 41.0
    assert w.pick_mode(a) == "work"
    changes = [(e.data["old"], e.data["new"]) for e in w.log.events if e.kind == "mode_change"]
    assert changes == [("work", "outlet"), ("outlet", "work"), ("work", "explore"),
                       ("explore", "work"), ("work", "rest"), ("rest", "work")]


# --- rest and sleep ------------------------------------------------------------------


def test_agents_sleep_at_night_and_wake_in_the_morning():
    w = make_world(hour=20)
    a = w.agents["a"]
    slept = woke = False
    for _ in range(12 * 14):
        step(w, 5)
        slept |= a.asleep
        woke |= slept and not a.asleep
    assert slept and woke


def test_an_exhausted_agent_falls_asleep_at_any_hour_and_wakes_at_half_energy():
    w = make_world(hour=10)
    a = w.agents["a"]
    a.needs["energy"] = 4.0
    step(w, 1)
    assert a.asleep and a.sleep_kind == "forced"
    a.needs["energy"] = 50.0
    step(w, 1)
    assert not a.asleep and a.mode == "work"


def test_a_tired_afternoon_brings_one_nap_a_day():
    w = make_world(hour=13)
    a = w.agents["a"]
    a.needs["energy"] = 24.0
    step(w, 1)
    assert a.asleep and a.sleep_kind == "nap"
    step(w, 5, times=19)
    assert not a.asleep  # ninety minutes later
    a.needs["energy"] = 24.0
    step(w, 1)
    assert not a.asleep  # it has had today's nap


def test_evening_rest_or_a_tired_idle_evening_becomes_an_early_night():
    # Without this an evening rest runs for hours and a tired agent with nothing left to
    # do has no way to end its day.
    def evening(hour, **body):
        w = make_world(hour=hour)
        a = w.agents["a"]
        a.mode, a.needs["energy"], a.current_task = body["mode"], body["energy"], body.get("task")
        step(w, 1)
        return w, a

    assert not evening(19, mode="rest", energy=18)[1].asleep  # 19:00 rest is still just rest
    w, a = evening(20, mode="rest", energy=18)
    assert a.asleep and a.sleep_kind == "night"
    w, a = evening(21, mode="work", energy=39)
    assert a.asleep and a.sleep_kind == "night"
    assert not evening(21, mode="work", energy=39, task="req-x")[1].asleep  # still on a task
    assert not evening(21, mode="work", energy=60)[1].asleep  # not tired


def test_a_standing_priority_does_not_keep_a_tired_agent_up():
    # A standing priority never finishes, so being on one is not "having a task" at bedtime.
    w = make_world(hour=21)
    a = w.agents["a"]
    w.standing = lambda agent_id: {"inbox"}
    a.needs["energy"], a.current_task = 39, "inbox"
    step(w, 1)
    assert a.asleep and a.sleep_kind == "night"


def test_rest_restores_energy_faster_than_sleep():
    # At half this rate a rest took nearly three hours to climb twenty points.
    w = make_world(hour=15)
    assert w.S("energy.rest_gain_per_min") >= 0.25
    assert w.S("energy.rest_gain_per_min") > w.S("energy.sleep_gain_per_min")


def test_an_agent_can_choose_to_rest_in_the_evening_and_it_becomes_bed():
    # An agent needs an action that means "stop". Without one it tries to end its day by
    # other means, again and again, and every refusal adds frustration.
    w = make_world(hour=11)
    a = w.agents["a"]
    a.needs["energy"] = 70
    why = w.rest("a", 60)
    assert "evening" in why  # a rested morning is not for resting
    assert "start_own_task" in why  # and an empty list in the morning points at its own work
    assert "owner" in why

    # The refusal gives the time on the wall and when rest opens for this agent, not the
    # hour by its own body clock.
    w = make_world(hour=16)
    a = w.agents["a"]
    a.temperament["sleep_offset_minutes"], a.needs["energy"] = 53, 70
    w.clock.advance(30)
    why = w.rest("a", 60)
    assert "it's 16:30" in why and "from 16:53" in why and "15:37" not in why
    w.clock.advance(23)
    assert w.rest("a", 60) is None

    w = make_world(hour=16)
    a = w.agents["a"]
    a.needs["energy"], a.current_task = 70, "req-x"
    assert "set_aside" in w.rest("a", 60)  # not in the middle of a task
    a.current_task = None
    assert w.rest("a", 999) is None and a.mode == "rest"
    assert a.rest_until - w.clock.now() == 180 * 60  # capped
    assert w.rest("a", 90) is None
    step(w, 60)
    assert w.pick_mode(a) == "rest" and a.needs["energy"] > 70  # stays resting though not tired
    w.clock.advance(31)
    w.advance(1)
    assert w.pick_mode(a) == "work" and a.rest_until is None  # the chosen rest ends

    w = make_world(hour=19)
    a = w.agents["a"]
    a.needs["energy"] = 60
    assert w.rest("a", 120) is None
    step(w, 1, times=70)
    assert a.asleep and a.sleep_kind == "night" and a.rest_until is None  # 20:00: rest became bed


def test_an_urgent_call_wakes_a_napper_at_once_but_a_night_sleeper_only_after_consolidation():
    w = make_world(hour=13)
    a = w.agents["a"]
    a.needs["energy"] = 24.0
    step(w, 1)
    assert a.sleep_kind == "nap"
    call = w.log.append("call", "a", w.clock.now())
    assert w.wake_for_call("a", call).data["cause"] == "urgent_call"
    assert not a.asleep and w.wake_for_call("a", call) is None

    w = make_world(hour=23)
    a = w.agents["a"]
    step(w, 5)
    assert a.sleep_kind == "night"
    call = w.log.append("call", "a", w.clock.now())
    assert w.wake_for_call("a", call) is None  # its memories are not safe yet
    w.log.append("consolidation", "a", w.clock.now(), failed=True)
    assert w.wake_for_call("a", call) is None
    w.log.append("consolidation", "a", w.clock.now())
    assert w.wake_for_call("a", call) is not None and not a.asleep


# --- temperament and saved state -----------------------------------------------------


def test_a_group_is_never_rolled_with_every_agent_short_tempered():
    for seed in range(50):
        g = roll_group(random.Random(seed), 2)
        assert not all(t["frustration_tolerance"] < 0.8 for t in g)


def test_a_rolled_temperament_stays_inside_its_ranges():
    for seed in range(50):
        t = roll(random.Random(seed))
        assert 0.6 <= t["energy_drain_rate"] <= 1.4 and 0.6 <= t["boredom_rate"] <= 1.4
        assert 0.5 <= t["social_capacity"] <= 1.5 and 0.5 <= t["frustration_tolerance"] <= 1.5
        assert sum(t["mood_weights"].values()) == pytest.approx(1.0, abs=0.01)
        assert -60 <= t["sleep_offset_minutes"] <= 60
        assert 2 <= len(t["seed_curiosities"]) <= 3


def test_a_saved_body_comes_back_the_same_after_a_restart(tmp_path):
    w = make_world()
    a = w.agents["a"]
    w.act("a", "click", {"el": 1}, before="s1", result="r1", ok=True, task="t")
    w.submit("a", "draft", task="t")
    w.set_aside("a", "other", "later")
    step(w, 5)
    path = tmp_path / "bodies" / "a.json"
    state.save_json(path, state.dump_agent(a))
    assert not path.with_suffix(".json.tmp").exists()

    back = state.load_agent(state.load_json(path))
    assert state.dump_agent(back) == state.dump_agent(a)
    assert back.needs == a.needs and back.parked.keys() == a.parked.keys()
    assert list(back.guard.before) == list(a.guard.before)

    w2 = World(getter(), w.log, w.clock)
    w2.restore_agent(back)
    assert w2.check("a", "click", {"el": 1}, "s1") is not None  # the guard remembered
    assert state.load_json(tmp_path / "missing.json") is None
