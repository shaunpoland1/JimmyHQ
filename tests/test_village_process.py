"""The village process: it starts, idles with nobody in it, keeps every agent's loop
going whatever one of them does, and stops when asked."""

from __future__ import annotations

import asyncio
import threading

import pytest

from jimmyhq import cli, supervisor
from jimmyhq.metrics import Metrics
from jimmyhq.village import process
from jimmyhq.village.runtime import Village
from jimmyhq.village.world import MemoryLog, SimClock
from jimmyhq.web import app as web_app
from test_village_beat_fakes import ME, call, fails, kinds, make_village


def test_the_village_is_a_supervised_process_with_its_own_command():
    assert "village" in supervisor.CHILDREN and "village" in web_app.PROCESSES
    assert process.NAME == "village"
    with pytest.raises(SystemExit) as stopped:
        cli.main(["village", "--help"])
    assert stopped.value.code == 0


def test_the_process_starts_idles_with_nobody_in_it_and_stops_when_asked(cfg):
    from jimmyhq.core import open_core

    stop = threading.Event()
    thread = threading.Thread(target=process.run, args=(stop, open_core(cfg)))
    thread.start()
    metrics = Metrics(cfg.metrics_db)
    try:
        for _ in range(100):
            if metrics.processes(("village",))[0]["ok"]:
                break
            threading.Event().wait(0.05)
        assert metrics.processes(("village",))[0]["ok"]
        assert thread.is_alive()  # nobody lives here, and it is still running
    finally:
        stop.set()
        thread.join(5)
        metrics.close()
    assert not thread.is_alive()


def test_an_agents_loop_outlives_a_beat_that_raises_and_a_model_that_fails(core):
    v = make_village(core, call("post_to_board", kind="need_help", text="x"),  # the tool raises
                     fails("unreachable"), call("write_diary", text="Still here."))
    waits = []

    async def pause(st, seconds):
        waits.append(seconds)
        if len(waits) == 3:
            del v.world.agents[ME]  # the agent leaves: its loop ends

    asyncio.run(process.agent_loop(v, ME, pause))
    assert waits == [120, 120, 10]
    assert len(kinds(v, "brain_error")) == 1 and len(kinds(v, "platform_fault")) == 1
    assert kinds(v, "action")[-1].data["tool"] == "write_diary"


def test_an_agent_is_given_its_documents_when_its_loop_starts_and_lives_without_them(core):
    from types import SimpleNamespace

    given = []

    def settle_in(v, agent_id):
        given.append(agent_id)
        raise OSError("the documents are missing")

    v = make_village(core, call("write_diary", text="x"),
                     docs=SimpleNamespace(settle_in=settle_in))

    async def pause(st, seconds):
        del v.world.agents[ME]

    asyncio.run(process.agent_loop(v, ME, pause))
    assert given == [ME] and len(kinds(v, "action")) == 1


def test_the_process_beats_every_agent_and_saves_their_bodies_on_the_way_out(core):
    stop = threading.Event()

    def last_one(messages, options):
        stop.set()
        return call("write_diary", text="One beat.")

    v = make_village(core, last_one, agents=(ME, "ben"))
    v.fakes["brain"].script.append(call("write_diary", text="Ben's beat."))
    guard = threading.Timer(20, stop.set)  # a broken beat must fail this test, not hang it
    guard.start()
    try:
        asyncio.run(process.main(stop, core, v=v, poll_s=0.01))
    finally:
        guard.cancel()
    assert len(kinds(v, "action")) + len(kinds(v, "action", agent="ben")) == 2
    assert core.metrics.processes(("village",))[0]["ok"]
    for agent_id in (ME, "ben"):
        assert (v.agent_dir(agent_id) / "state.json").is_file()
    assert (core.config.data / "village" / "world.json").is_file()


def test_a_loop_that_ended_is_started_again_and_one_for_a_departed_agent_is_stopped(core):
    v = make_village(core, agents=(ME, "ben"))

    async def scene():
        async def dies():
            raise RuntimeError("the loop itself broke")

        async def forever():
            await asyncio.sleep(60)

        loops = {ME: asyncio.create_task(dies()), "gone": asyncio.create_task(forever())}
        await asyncio.sleep(0)
        dead, departed = loops[ME], loops["gone"]
        process.reconcile(v, loops)  # clears the dead one, stops the departed one
        process.reconcile(v, loops)
        started = set(loops)
        fresh = loops[ME]
        await asyncio.sleep(0)
        for task in loops.values():
            task.cancel()
        await asyncio.gather(*loops.values(), departed, return_exceptions=True)
        return started, fresh is not dead, departed.cancelled()

    v.fakes["brain"].script = [fails("unreachable")] * 4
    assert asyncio.run(scene()) == ({ME, "ben"}, True, True)


def test_one_failing_part_of_a_pass_does_not_stop_the_process(core, monkeypatch):
    stop = threading.Event()
    v = make_village(core, agents=())
    passes = []

    def broken_poll(village):
        passes.append(1)
        if len(passes) == 3:
            stop.set()
        raise RuntimeError("the database is busy")

    monkeypatch.setattr(process.intake, "poll", broken_poll)
    monkeypatch.setattr(v, "tick", lambda: 1 / 0)
    asyncio.run(process.main(stop, core, v=v, poll_s=0.01))
    assert len(passes) == 3


# ------------------------------------------------------- load, save, time
def test_saved_bodies_are_put_back_when_the_village_starts(core):
    v = make_village(core, agents=(ME, "ben"))
    v.world.agents[ME].needs["energy"] = 33.0
    v.world.agents["ben"].current_task = "T1"
    v.save()
    (v.data / "agents" / "broken").mkdir()
    (v.data / "agents" / "broken" / "state.json").write_bytes(b"{not json")
    again = Village(core, clock=v.clock, log=v.log, fakes=v.fakes, only_fakes=True)
    assert again.load() == [ME, "ben"]  # the unreadable one is logged and left out
    assert again.world.agents[ME].needs["energy"] == 33.0
    assert again.world.agents["ben"].current_task == "T1"
    assert len(v.log.query(kinds=["born"])) == 2  # nobody was born twice


def test_a_village_with_no_data_yet_loads_as_empty(core):
    v = Village(core, clock=SimClock(1_800_000_000.0, "UTC"), log=MemoryLog(), only_fakes=True)
    assert v.load() == []
    v.tick()
    v.save()


def test_time_that_passed_while_running_is_lived_in_steps_of_five_minutes(core):
    v = make_village(core)
    v.tick()
    v.clock.advance(12)
    v.tick()
    assert [e.data["minutes"] for e in kinds(v, "tick")] == [5.0, 5.0, pytest.approx(2.0)]
    assert kinds(v, "world_paused") == []


def test_a_long_gap_is_a_pause_the_bodies_did_not_live(core):
    v = make_village(core)
    v.tick()
    energy = v.world.agents[ME].needs["energy"]
    v.clock.advance(16)
    v.tick()
    paused = kinds(v, "world_paused")
    assert len(paused) == 1 and paused[0].data["minutes"] == 16.0
    assert kinds(v, "tick") == [] and v.world.agents[ME].needs["energy"] == energy


def test_when_the_world_last_moved_is_remembered_across_a_restart(core):
    v = make_village(core)
    v.tick()
    v.clock.advance(4)
    again = Village(core, clock=v.clock, log=v.log, fakes=v.fakes, only_fakes=True)
    again.load()
    again.tick()
    assert [e.data["minutes"] for e in kinds(v, "tick")] == [pytest.approx(4.0)]
