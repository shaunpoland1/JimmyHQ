"""What the runtime adds around the world: the faults found in the first port, each
fixed here with a test, and what reaches the village from the web process."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jimmyhq.village import intake, record, roster
from jimmyhq.village.rules import RULES, Refusal
from jimmyhq.village.runtime import Village, dump_state, load_state
from jimmyhq.village.world import MemoryLog, SimClock, World, getter
from test_village_beat_fakes import ME, Box, kinds, make_village, owner_says, step


def waiting_frustration(v) -> float:
    return sum(e.data["delta"] for e in kinds(v, "need_change")
               if e.data["cause"].startswith("waiting_on:"))


# ------------------------------------------------- an urgent call at night
def asleep_at_night(core, **over):
    v = make_village(core, hour=23, **over)
    step(v, 5)
    a = v.world.agents[ME]
    assert a.asleep and a.sleep_kind == "night"
    v.log.append("consolidation", ME, v.clock.now(), failed=False)  # the night's work is done
    return v, a


def test_an_urgent_call_at_night_keeps_the_agent_awake_until_it_answers(core):
    v, a = asleep_at_night(core)
    intake.wake(v, ME, call=owner_says(v, "Wake up, please.", urgent=True))
    assert not a.asleep and v.state(ME).wake.is_set()
    assert kinds(v, "wake")[-1].data["cause"] == "urgent_call"
    step(v, 5, times=6)  # half an hour inside the sleep window
    assert not a.asleep and v.world.held_awake(a)
    v.log.append("chat", ME, v.clock.now(), sender=ME, text="I'm here.")
    assert not v.world.held_awake(a)
    step(v, 5)
    assert a.asleep and a.sleep_kind == "night"


def test_an_unanswered_call_keeps_an_agent_up_for_a_set_time_only(core):
    v, a = asleep_at_night(core)
    intake.wake(v, ME, call=owner_says(v, "Wake up, please.", urgent=True))
    step(v, 5, times=11)
    assert not a.asleep  # 55 minutes: still inside the hour
    step(v, 5)
    assert a.asleep
    assert RULES["call_keeps_awake"].setting == "village.beat.call_awake_minutes"


def test_how_long_a_call_keeps_an_agent_up_is_a_setting(core):
    core.settings.set("village.beat.call_awake_minutes", 10, actor="owner")
    v, a = asleep_at_night(core)
    intake.wake(v, ME, call=owner_says(v, "Wake up, please.", urgent=True))
    step(v, 5)
    assert not a.asleep
    step(v, 5)
    assert a.asleep


def test_a_call_late_in_the_evening_keeps_an_awake_agent_from_going_to_bed(core):
    v = make_village(core, hour=22)
    step(v, 55)
    a = v.world.agents[ME]
    intake.wake(v, ME, call=owner_says(v, "One thing before bed.", urgent=True))
    step(v, 5, times=3)  # 23:10
    assert not a.asleep


def test_an_exhausted_agent_sleeps_even_with_a_call_open(core):
    v, a = asleep_at_night(core)
    intake.wake(v, ME, call=owner_says(v, "Wake up, please.", urgent=True))
    a.needs["energy"] = 4.0
    step(v, 5)
    assert a.asleep


def test_a_call_does_not_wake_an_agent_whose_night_work_is_not_done(core):
    v = make_village(core, hour=23)
    step(v, 5)
    a = v.world.agents[ME]
    intake.wake(v, ME, call=owner_says(v, "Wake up, please.", urgent=True))
    assert a.asleep  # its memories come first; it sees the call when it wakes


# ------------------------------------------------------- saved with the body
def test_the_margin_given_to_a_chosen_task_survives_a_restart(core):
    v = make_village(core)
    a = v.world.agents[ME]
    a.needs["frustration"] = 55.0
    v.world.choose_task(ME, "T1")
    assert a.set_aside_floor == 67.0
    a.call_until = v.clock.now() + 600
    a.waited_answered = {"day": "2026-11-09", "caused": 5.0}
    v.save(ME)
    saved = json.loads((v.agent_dir(ME) / "state.json").read_bytes())
    assert saved["set_aside_floor"] == 67.0

    again = Village(core, clock=v.clock, log=v.log, fakes=v.fakes, only_fakes=True)
    assert again.load() == [ME]
    b = again.world.agents[ME]
    assert b.set_aside_floor == 67.0 and b.call_until == a.call_until
    assert b.waited_answered == {"day": "2026-11-09", "caused": 5.0}
    # A little more frustration, still under the margin: the chosen task is not set aside.
    again.world.refuse(ME, "write_file", {}, "no", "T1")
    assert b.current_task == "T1" and b.parked == {}
    assert load_state(dump_state(b)).set_aside_floor == 67.0


def test_state_saved_before_these_were_kept_still_loads(core):
    v = make_village(core)
    old = dump_state(v.world.agents[ME])
    for key in ("set_aside_floor", "call_until", "waited_answered"):
        del old[key]
    a = load_state(old)
    assert a.set_aside_floor is None and a.call_until is None and a.waited_answered is None


# -------------------------------------------- picking up a task not set aside
def test_picking_back_up_a_task_that_is_not_set_aside_is_a_refusal_not_a_crash(core):
    v = make_village(core)
    with pytest.raises(Refusal) as refused:
        v.world.resume_by_choice(ME, "T1")
    assert refused.value.rule == "resume_needs_parked" and refused.value.frustrate is False
    assert refused.value.sentence == (
        "You have not set T1 aside, so nothing was picked back up. To work on it, use "
        "focus_on with its reference from your list.")
    v.world.set_aside(ME, "T1", "later")
    assert v.world.resume_by_choice(ME, "T1").data["chosen"] is True
    assert v.world.agents[ME].current_task == "T1"


# ------------------------------------------------------- the waiting cap
def test_the_cap_on_frustration_from_waiting_counts_items_already_answered(core):
    core.settings.set("village.frustration.wait_per_hour", 20.0, actor="owner")
    core.settings.set("village.frustration.wait_cap_per_item", 6.0, actor="owner")
    v = make_village(core)
    for item in ("card-1", "card-2", "card-3"):
        v.world.submit(ME, item)
        step(v, 60)
        v.world.respond(ME, item, "approved")
    # Two items reach six each; the third finds the day's twelve already used.
    assert waiting_frustration(v) == pytest.approx(12.0)
    assert v.world.agents[ME].waited_answered == {"day": "2026-11-09", "caused": 12.0}
    assert v.world.S("frustration.wait_cap_total") == 12.0  # the setting itself is untouched


def test_answered_waiting_counts_for_its_own_day_only(core):
    core.settings.set("village.frustration.wait_per_hour", 20.0, actor="owner")
    v = make_village(core)
    a = v.world.agents[ME]
    a.waited_answered = {"day": "2026-11-08", "caused": 12.0}
    assert v.world.answered_today(a) == 0.0
    v.world.submit(ME, "card-1")
    step(v, 60)
    assert waiting_frustration(v) == pytest.approx(5.0)  # one item's cap


# ------------------------------------------------ numbers that were fixed in code
def test_the_worlds_once_fixed_numbers_are_settings_with_the_same_defaults(core):
    expected = {"sleep.forced_wake_energy": 50.0, "sleep.rest_refusal_energy": 40.0,
                "sleep.rest_min_minutes": 20.0, "sleep.rest_max_minutes": 180.0,
                "sleep.nap_from_hour": 12.0, "sleep.nap_until_hour": 18.0,
                "modes.rest_margin": 20.0, "modes.outlet_margin": 20.0,
                "modes.explore_margin": 25.0}
    v = make_village(core)
    for key, default in expected.items():
        assert v.S(key) == default, key


def test_the_bare_world_without_those_settings_behaves_as_before():
    clock = SimClock(1_800_000_000.0, "UTC")
    w = World(getter(), MemoryLog(), clock)
    assert w._opt("sleep.rest_max_minutes", 180) == 180
    assert w._opt("modes.rest_margin", 20) == 20
    assert World(getter({"modes.rest_margin": 5}), MemoryLog(), clock)._opt(
        "modes.rest_margin", 20) == 5


def test_the_rest_limits_and_refusal_energy_follow_their_settings(core):
    v = make_village(core, hour=17)
    a = v.world.agents[ME]
    assert v.world.rest(ME, 600) is None and a.rest_until == v.clock.now() + 180 * 60
    core.settings.set("village.sleep.rest_max_minutes", 240.0, actor="owner")
    core.settings.set("village.sleep.rest_min_minutes", 30.0, actor="owner")
    assert v.world.rest(ME, 600) is None and a.rest_until == v.clock.now() + 240 * 60
    assert v.world.rest(ME, 1) is None and a.rest_until == v.clock.now() + 30 * 60
    morning = make_village(core, hour=10)
    assert "you have energy" in morning.world.rest(ME, 60)  # energy 80, refused above 40
    core.settings.set("village.sleep.rest_refusal_energy", 90.0, actor="owner")
    assert morning.world.rest(ME, 60) is None


def test_the_mode_margins_and_nap_hours_follow_their_settings(core):
    v = make_village(core)
    a = v.world.agents[ME]
    a.mode, a.needs["energy"] = "rest", 35.0  # rest holds until 20 + 20
    assert v.world.pick_mode(a) == "rest"
    core.settings.set("village.modes.rest_margin", 10.0, actor="owner")
    assert v.world.pick_mode(a) == "work"
    a.mode, a.needs["frustration"] = "outlet", 45.0  # a break holds above 62 - 20
    assert v.world.pick_mode(a) == "outlet"
    core.settings.set("village.modes.outlet_margin", 10.0, actor="owner")
    assert v.world.pick_mode(a) == "work"
    a.mode, a.needs["frustration"], a.needs["curiosity"] = "explore", 10.0, 55.0
    assert v.world.pick_mode(a) == "explore"  # exploring holds above 75 - 25
    core.settings.set("village.modes.explore_margin", 10.0, actor="owner")
    assert v.world.pick_mode(a) == "work"

    core.settings.set("village.sleep.nap_from_hour", 10.0, actor="owner")
    early = make_village(core, hour=10)
    early.world.agents[ME].needs["energy"] = 20.0
    step(early, 5)
    assert early.world.agents[ME].sleep_kind == "nap"  # the default opens naps at noon
    early.world.agents[ME].needs["energy"] = 55.0
    step(early, 95)
    assert not early.world.agents[ME].asleep


def test_a_collapse_ends_at_the_energy_its_setting_names(core):
    core.settings.set("village.sleep.forced_wake_energy", 30.0, actor="owner")
    v = make_village(core, hour=9)
    a = v.world.agents[ME]
    a.needs["energy"] = 4.0
    step(v, 1)
    assert a.asleep and a.sleep_kind == "forced"
    a.needs["energy"] = 31.0
    step(v, 1)
    assert not a.asleep


# ------------------------------------------------------------------- intake
class Tape:
    """Stands in for the Tape: events after a cursor, of the kinds asked for."""

    def __init__(self, v, monkeypatch):
        self.events, self.v = [], v
        monkeypatch.setattr(v.core.tape, "after", self.after)

    def add(self, kind, **payload):
        seq = len(self.events) + 1
        self.events.append(SimpleNamespace(
            id=f"tape-{seq}", seq=seq, ts=round(self.v.clock.now() * 1000), kind=kind,
            payload=payload, readable=True))

    def after(self, seq=0, *, limit=500, kinds=None, **_):
        return [e for e in self.events if e.seq > seq and e.kind in kinds][:limit]


def test_an_owner_message_on_the_tape_becomes_a_chat_line_and_wakes_the_agent(core, monkeypatch):
    v = make_village(core)
    tape = Tape(v, monkeypatch)
    tape.add("village.said", agent_id=ME, sender="owner", text="Good morning.", urgent=False)
    assert intake.read_tape(v) == 1
    line = kinds(v, "chat")[-1]
    assert line.data == {"sender": "owner", "text": "Good morning.", "tape": "tape-1"}
    assert line.ts == v.clock.now() and v.state(ME).wake.is_set()
    assert roster.cursor(core, intake.CURSOR) == 1
    assert intake.read_tape(v) == 0 and len(kinds(v, "chat")) == 1  # read once only
    # as if it stopped before the cursor was saved
    roster.db(core).run("UPDATE cursors SET seq = 0 WHERE name = ?", (intake.CURSOR,))
    intake.read_tape(v)
    assert len(kinds(v, "chat")) == 1


def test_the_agents_own_messages_and_messages_for_strangers_are_not_mirrored(core, monkeypatch):
    v = make_village(core)
    tape = Tape(v, monkeypatch)
    mine = v.log.append("chat", ME, v.clock.now(), sender=ME, text="Mine.", tape="tape-1")
    tape.add("village.said", agent_id=ME, sender="agent", text="Mine.", urgent=False)
    tape.add("village.said", agent_id="nobody", sender="owner", text="Hello?", urgent=False)
    assert intake.read_tape(v) == 2
    assert v.log.query(kinds=["chat"]) == [mine]  # logged where it was sent, and only there
    assert not v.state(ME).wake.is_set()


def test_an_agents_line_found_only_on_the_tape_is_logged_as_the_agents_own(core, monkeypatch):
    v = make_village(core)
    tape = Tape(v, monkeypatch)
    tape.add("village.said", agent_id=ME, sender="agent", text="Sent before a stop.",
             urgent=False)
    intake.read_tape(v)
    assert kinds(v, "chat")[-1].data["sender"] == ME and not v.state(ME).wake.is_set()


def test_an_urgent_message_wakes_a_napping_agent(core, monkeypatch):
    v = make_village(core, hour=14)
    a = v.world.agents[ME]
    a.needs["energy"] = 20.0
    step(v, 5)
    assert a.asleep and a.sleep_kind == "nap"
    tape = Tape(v, monkeypatch)
    tape.add("village.said", agent_id=ME, sender="owner", text="Now, please.", urgent=True)
    intake.read_tape(v)
    assert not a.asleep and kinds(v, "chat")[-1].data["urgent"] is True
    assert kinds(v, "wake")[-1].data["evidence"] == [kinds(v, "chat")[-1].id]


def test_a_decision_on_a_village_card_is_applied_and_wakes_the_agent_who_filed_it(
        core, monkeypatch):
    seen = []

    def apply_decision(v, event):  # the real one checks the card and wakes its agent
        seen.append(event.payload["card_id"])
        v.wake(event.payload["filed_by"].split(":", 1)[1])

    link = SimpleNamespace(apply_decision=apply_decision)
    v = make_village(core, agents=(ME, "ben"), **{"comms.benchlink": link})
    tape = Tape(v, monkeypatch)
    tape.add("bench.decided", card_id="card-1", card_kind="village.work", action="approve",
             filed_by="agent:ada")
    tape.add("bench.decided", card_id="card-2", card_kind="posts.draft", action="no",
             filed_by="agent:ben")
    intake.read_tape(v)
    assert seen == ["card-1"]
    assert v.state(ME).wake.is_set() and not v.state("ben").wake.is_set()


def test_a_new_configuration_and_a_new_priority_reach_the_running_village(core):
    v = make_village(core, agents=(ME, "ben", "cy"))
    assert v.cfg(ME)["beat"] == {"role": "default"} and v.cfg(ME)["local_only"] is False
    record.configured(core, ME, {"role": "researcher", "beat": {"role": "cheap"},
                                 "local_only": True, "capabilities": ["desktop"]})
    assert v.cfg(ME)["local_only"] is False  # until the village reads the Tape
    record.prioritized(core, {"priority_id": "p-1", "title": "Letters", "agents": ["ben"],
                              "active": True})
    intake.read_tape(v)
    cfg = v.cfg(ME)
    assert cfg["beat"] == {"role": "cheap"} and cfg["local_only"] is True
    assert cfg["night"] == {"role": "smart"} and cfg["capabilities"] == ["desktop"]
    assert v.state("ben").wake.is_set() and not v.state("cy").wake.is_set()
    record.prioritized(core, {"priority_id": "p-2", "title": "For all", "agents": [],
                              "active": True})
    intake.read_tape(v)
    assert v.state("cy").wake.is_set()


def test_an_agents_configuration_is_read_from_the_village_lens_when_first_needed(core):
    v = make_village(core)
    record.configured(core, ME, {"role": "writer",
                                 "beat": {"provider_id": "p1", "model": "m1"}})
    assert v.cfg(ME)["beat"] == {"provider_id": "p1", "model": "m1"}
    assert v.cfg(ME)["role"] == "writer" and v.cfg(ME)["agent_id"] == ME


def test_one_event_that_cannot_be_taken_in_does_not_block_the_rest(core, monkeypatch):
    def broken(v, event):
        raise RuntimeError("no such card")

    v = make_village(core, **{"comms.benchlink": SimpleNamespace(apply_decision=broken)})
    tape = Tape(v, monkeypatch)
    tape.add("bench.decided", card_id="card-1", card_kind="village.work", action="approve",
             filed_by="agent:ada")
    tape.add("village.said", agent_id=ME, sender="owner", text="Hello.", urgent=False)
    assert intake.read_tape(v) == 2
    assert len(kinds(v, "chat")) == 1 and roster.cursor(core, intake.CURSOR) == 2


def add_command(v, kind, agent=None, **payload):
    """As the web process leaves one: through the roster, which owns the table."""
    return roster.submit(v.core, kind, agent, payload, by="owner")


def command(v, command_id):
    return roster.get(v.core, command_id)


def test_a_command_is_run_between_beats_and_its_result_recorded(core):
    ran = []

    def handle(v, cmd):
        ran.append((cmd["kind"], cmd["agent"], cmd["payload"]))
        if cmd["kind"] == "rollback":
            raise ValueError("there is no snapshot 9")
        return {"agent_id": "ben"}

    v = make_village(core, roster=SimpleNamespace(handle=handle))
    c1 = add_command(v, "birth", role="researcher", seed=7)
    c2 = add_command(v, "rollback", agent=ME, to=9)
    assert intake.run_commands(v) == 2
    assert ran == [("birth", None, {"role": "researcher", "seed": 7}),
                   ("rollback", ME, {"to": 9})]
    done = command(v, c1)
    assert done["done_ts"] is not None and done["result"] == {"agent_id": "ben"}
    assert command(v, c2)["result"] == {
        "ok": False, "error": "ValueError: there is no snapshot 9"}
    assert intake.run_commands(v) == 0  # each runs once


def test_a_command_for_an_agent_in_the_middle_of_a_beat_waits(core):
    v = make_village(core, roster=SimpleNamespace(handle=lambda v, cmd: {"ok": True}))
    c1 = add_command(v, "retire", agent=ME)
    v.state(ME).busy = True
    assert intake.run_commands(v) == 0 and command(v, c1)["done_ts"] is None
    v.state(ME).busy = False
    assert intake.run_commands(v) == 1 and command(v, c1)["done_ts"] is not None


def test_commands_keep_until_something_can_run_them(core):
    v = make_village(core)
    c1 = add_command(v, "birth")
    assert intake.run_commands(v) == 0 and command(v, c1)["done_ts"] is None
    intake.poll(v)  # and a whole pass without them is harmless
    assert command(v, c1)["done_ts"] is None


# ------------------------------------------------------------ the runtime
def test_the_village_names_the_owner_and_falls_back_to_plain_words(core):
    v = make_village(core)
    assert v.owner_name() == "the owner" and v.tz() == ""
    core.settings.set("owner.name", "  Robin ", actor="owner")
    assert v.owner_name() == "Robin"
    assert v.agent_dir(ME) == core.config.data / "village" / "agents" / ME
    assert v.shared_dir == core.config.data / "village" / "shared"
    assert v.name(ME) == "Ada" and v.name("night-owl") == "Night Owl"


def test_a_seam_that_is_not_installed_answers_with_the_default(core):
    v = Village(core, clock=SimClock(1_800_000_000.0, "UTC"), log=MemoryLog())
    assert v.seam("no_such_part") is None and v.seam("no_such.part") is None
    assert v.ask("no_such_part", "anything", ME, default=[]) == []
    v.fakes["comms.work"] = Box(queued=[1, 2])
    assert v.ask("comms.work", "queued", ME) == [1, 2]
    assert v.ask("comms.work", "missing_function", ME, default="none") == "none"


def test_what_does_not_keep_a_tired_agent_up_is_its_standing_priorities(core):
    v = make_village(core)
    v.fakes["comms.tasklist"] = Box(standing={"P1"})
    assert v.world.standing(ME) == {"P1"}  # the list says which; a one-off task is not one
    v.fakes["comms.tasklist"] = SimpleNamespace(standing=lambda v, agent_id: 1 / 0)
    assert v.world.standing(ME) == set()  # a broken list must not stop the clock


def test_the_bare_world_keeps_every_tuned_number_it_had(core):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from test_village_beat_fakes import temperament

    def bare(hour):
        clock = SimClock(datetime(2026, 11, 9, hour, 0, tzinfo=ZoneInfo("UTC")), "UTC")
        w = World(getter(), MemoryLog(), clock)
        return w, w.add_agent(ME, temperament()), clock

    w, a, clock = bare(17)
    assert w.rest(ME, 600) is None and a.rest_until == clock.now() + 180 * 60
    assert w.rest(ME, 1) is None and a.rest_until == clock.now() + 20 * 60
    w, a, clock = bare(10)
    a.needs["energy"] = 40.0
    assert "you have energy" in w.rest(ME, 60)
    a.needs["energy"] = 39.9
    assert w.rest(ME, 60) is None
    a.mode, a.rest_until, a.needs["energy"] = "rest", None, 39.9
    assert w.pick_mode(a) == "rest"
    a.needs["energy"] = 40.0
    assert w.pick_mode(a) == "work"
    a.mode, a.needs["frustration"] = "outlet", 42.1
    assert w.pick_mode(a) == "outlet"
    a.needs["frustration"] = 42.0
    assert w.pick_mode(a) == "work"
    a.mode, a.needs["curiosity"] = "explore", 50.1
    assert w.pick_mode(a) == "explore"
    a.needs["curiosity"] = 50.0
    assert w.pick_mode(a) == "work"
    for hour, naps in ((11, False), (12, True), (17, True), (18, False)):
        w, a, clock = bare(hour)
        a.needs["energy"] = 20.0
        clock.advance(1)
        w.advance(1)
        assert (a.sleep_kind == "nap") is naps, hour
    w, a, clock = bare(9)
    a.asleep, a.sleep_kind, a.mode, a.needs["energy"] = True, "forced", "sleep", 49.0
    clock.advance(1)
    w.advance(1)
    assert a.asleep
    a.needs["energy"] = 50.0
    clock.advance(1)
    w.advance(1)
    assert not a.asleep
