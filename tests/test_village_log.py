"""The village event log: its own database, apart from the Tape."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jimmyhq import modules, village
from jimmyhq.village.log import SqliteLog
from jimmyhq.village.world import MemoryLog, SimClock, World, getter
from jimmyhq.village.world.temperament import roll

T0 = 1_800_000_000.0


@pytest.fixture
def log(tmp_path):
    log = SqliteLog(tmp_path / "village.db")
    yield log
    log.close()


def fill(log):
    log.append("born", "a", T0, needs={"energy": 80.0})
    log.append("action", "a", T0 + 60, tool="click", ok=True)
    log.append("action", "b", T0 + 120, tool="scroll", ok=False)
    log.append("feeling", "a", T0 + 180, phrase="settled", evidence=["evt-2"])
    log.append("action", "a", T0 + 240, tool="click", ok=False)
    log.append("setting_note", None, T0 + 300)


def test_an_appended_event_reads_back_with_its_id_time_agent_kind_and_data(log):
    e = log.append("action", "a", T0 + 0.25, tool="click", args={"el": 3}, ok=True)
    assert e.id == "evt-1" and e.ts == T0 + 0.25 and e.agent == "a" and e.kind == "action"
    assert log.get("evt-1") == e and e.data == {"tool": "click", "args": {"el": 3}, "ok": True}
    assert log.get("evt-99") is None and log.get("nonsense") is None and log.get("evt-x") is None


def test_times_are_stored_as_unix_milliseconds(log, tmp_path):
    log.append("tick", "a", T0 + 1.5)
    conn = sqlite3.connect(tmp_path / "village.db")
    try:
        assert conn.execute("SELECT ts_ms, agent, kind, data FROM events").fetchall() == [
            (1_800_000_001_500, "a", "tick", "{}")]
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(events)")}
        assert {"events_agent_ts", "events_kind_ts"} <= indexes
    finally:
        conn.close()


def test_events_can_be_read_by_agent_by_kind_and_by_time_oldest_first(log):
    fill(log)
    assert [e.id for e in log.query()] == [f"evt-{i}" for i in range(1, 7)]
    assert [e.id for e in log.query(agent="a")] == ["evt-1", "evt-2", "evt-4", "evt-5"]
    assert [e.id for e in log.query(kinds=["action"])] == ["evt-2", "evt-3", "evt-5"]
    assert [e.id for e in log.query(agent="a", kinds=["action", "feeling"],
                                    since=T0 + 180)] == ["evt-4", "evt-5"]
    # both ends of a time range are included
    assert [e.id for e in log.query(since=T0 + 60, until=T0 + 180)] == ["evt-2", "evt-3", "evt-4"]
    assert [e.id for e in log.query(agent="a", limit=2)] == ["evt-4", "evt-5"]
    assert log.query(agent="nobody") == []


def test_the_newest_event_and_counts_can_be_asked_for_directly(log):
    fill(log)
    assert log.last().id == "evt-6"
    assert log.last(agent="a", kinds=["action"]).id == "evt-5"
    assert log.last(agent="b", kinds=["feeling"]) is None
    assert log.count() == 6 and log.count(agent="a") == 4
    assert log.count(agent="a", kinds=["action"], since=T0 + 100) == 1
    assert log.counts_by_kind(agent="a") == {"born": 1, "action": 2, "feeling": 1}
    assert log.counts_by_kind(since=T0 + 250) == {"setting_note": 1}


def test_the_log_survives_being_closed_and_opened_and_keeps_counting_ids(tmp_path):
    first = SqliteLog(tmp_path / "village.db")
    fill(first)
    first.close()
    second = SqliteLog(tmp_path / "village.db")
    try:
        assert second.count() == 6
        assert second.append("tick", "a", T0 + 400).id == "evt-7"
    finally:
        second.close()


def test_two_handles_on_one_file_see_each_others_events(tmp_path):
    one, two = SqliteLog(tmp_path / "village.db"), SqliteLog(tmp_path / "village.db")
    try:
        one.append("tick", "a", T0)
        two.append("tick", "b", T0 + 1)
        assert [e.id for e in one.query()] == ["evt-1", "evt-2"] == [e.id for e in two.query()]
    finally:
        one.close()
        two.close()


def test_the_log_offers_no_way_to_change_or_remove_an_event():
    assert not [name for name in dir(SqliteLog)
                if name.startswith(("update", "delete", "remove", "set", "clear"))]


def test_the_world_behaves_the_same_on_the_stored_log_as_on_the_one_in_memory(log):
    def run(event_log):
        clock = SimClock(datetime(2026, 11, 9, 18, 0, tzinfo=ZoneInfo("UTC")), "UTC")
        w = World(getter(), event_log, clock)
        t = roll(random.Random(4))
        w.add_agent("a", t)
        rng = random.Random(5)
        for i in range(200):
            clock.advance(5)
            w.advance(5)
            if not w.agents["a"].asleep:
                w.pick_mode(w.agents["a"])
                w.act("a", "click", {"el": rng.randint(0, 3)}, before=f"s{i % 7}",
                      result=f"r{rng.randint(0, 5)}", ok=rng.random() > 0.3, task="t")
                if i % 12 == 0:
                    w.feelings("a")
        return w, [(e.id, e.ts, e.kind, e.agent, e.data) for e in event_log.query()]

    w_mem, in_memory = run(MemoryLog())
    w_db, stored = run(log)
    assert stored == in_memory and len(stored) > 500
    assert w_db.agents["a"].needs == w_mem.agents["a"].needs
    assert any(kind == "sleep" for _, _, kind, _, _ in stored)


def test_the_module_is_registered_and_keeps_its_log_in_the_data_folder_not_on_the_tape(core):
    assert "village" in modules.MODULES
    before = core.tape.last_seq()
    world = village.new_world(core)
    world.add_agent("a", roll(random.Random(1)))
    world.advance(1)
    assert (core.config.data / "village.db").is_file()
    assert village.log(core) is village.log(core)
    assert village.log(core).count(agent="a", kinds=["born"]) == 1
    assert core.tape.last_seq() == before  # nothing reached the Tape
    assert world.S("energy.drain_per_min") == 0.075


def test_the_world_reads_a_setting_the_owner_changed_without_a_restart(core):
    world = village.new_world(core)
    assert world.S("guards.wall_after") == 4
    core.settings.set("village.guards.wall_after", 9, actor="owner")
    assert world.S("guards.wall_after") == 9
    assert world.S("village.headcount_cap") == 5


def test_the_village_clock_uses_the_owners_time_zone(core):
    core.settings.set("owner.timezone", "Asia/Tokyo", actor="owner")
    assert village.clock(core).local_time().utcoffset().total_seconds() == 9 * 3600
