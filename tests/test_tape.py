from __future__ import annotations

import threading

import pytest
from pydantic import ValidationError

from jimmyhq.tape import Tape, registry
from jimmyhq.tape.registry import Payload, kind, upcaster


def test_a_printed_event_comes_back_with_a_seq_and_its_payload(core):
    event = core.tape.print("setting.updated", {"key": "k", "value": 1}, actor="owner")
    assert event.seq == core.tape.last_seq()
    assert core.tape.get(event.id).payload == {"key": "k", "value": 1}


def test_an_unknown_kind_is_refused(core):
    with pytest.raises(registry.UnknownKindError):
        core.tape.print("nope.nothing", {}, actor="owner")


def test_a_payload_with_an_extra_field_is_refused(core):
    with pytest.raises(ValidationError):
        core.tape.print("setting.reset", {"key": "k", "surprise": 1}, actor="owner")


def test_latest_is_newest_first_and_after_is_oldest_first(core):
    for i in range(5):
        core.tape.print("setting.updated", {"key": "k", "value": i}, actor="owner")
    newest = core.tape.latest(limit=2, kind="setting.updated")
    assert [e.payload["value"] for e in newest] == [4, 3]
    older = core.tape.latest(limit=2, kind="setting.updated", before=newest[-1].seq)
    assert [e.payload["value"] for e in older] == [2, 1]
    oldest_first = core.tape.after(0, kind="setting.updated")
    assert [e.payload["value"] for e in oldest_first] == [0, 1, 2, 3, 4]


def test_lineage_walks_cause_links_nearest_first(core):
    a = core.tape.print("setting.updated", {"key": "k", "value": 1}, actor="owner")
    b = core.tape.print("setting.updated", {"key": "k", "value": 2}, actor="owner", cause=a.id)
    c = core.tape.print("setting.updated", {"key": "k", "value": 3}, actor="owner", cause=b.id)
    assert [e.id for e in core.tape.lineage(c.id)] == [b.id, a.id]
    assert [e.id for e in core.tape.caused_by(a.id)] == [b.id]


def test_two_connections_to_one_tape_never_lose_or_duplicate_a_seq(cfg, core):
    other = Tape(cfg.tape_db)
    start = core.tape.last_seq()

    def write(tape):
        for i in range(100):
            tape.print("setting.updated", {"key": "k", "value": i}, actor="owner")

    threads = [threading.Thread(target=write, args=(t,)) for t in (core.tape, other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    seqs = [e.seq for e in core.tape.after(start, limit=1000)]
    other.close()
    assert seqs == list(range(start + 1, start + 201))


def test_an_old_payload_is_upgraded_on_read_and_the_stored_bytes_are_untouched(core):
    @kind("testing.renamed", v=1, example={"old": "x"})
    class V1(Payload):
        old: str

    stored = core.tape.print("testing.renamed", {"old": "hello"}, actor="owner")

    @kind("testing.renamed", v=2, example={"new": "x"})
    class V2(Payload):
        new: str

    @upcaster("testing.renamed", 1)
    def _up(payload):
        return {"new": payload["old"]}

    read = core.tape.get(stored.id)
    assert (read.v, read.payload) == (2, {"new": "hello"})
    raw = core.tape._conn.execute(
        "SELECT v, payload FROM tape WHERE id = ?", (stored.id,)
    ).fetchone()
    assert (raw["v"], raw["payload"]) == (1, '{"old":"hello"}')


def test_a_kind_cannot_be_registered_without_a_valid_example():
    with pytest.raises(ValidationError):
        @kind("testing.bad_example", example={"wrong": 1})
        class Bad(Payload):
            right: str


def test_an_app_kind_must_carry_its_app_prefix_and_core_cannot_use_it():
    with pytest.raises(ValueError, match="must start with"):
        @kind("notes.saved", example={}, owner="notes")
        class A(Payload):
            pass

    with pytest.raises(ValueError, match="reserved for apps"):
        @kind("app.notes.saved", example={})
        class B(Payload):
            pass


def test_every_registered_kind_has_an_example_that_validates():
    for name in registry.registered_kinds():
        v = registry.latest_version(name)
        registry.validate_payload(name, v, registry.example_of(name))


def test_stats_count_events_by_kind(core):
    core.tape.print("setting.reset", {"key": "k"}, actor="owner")
    stats = core.tape.stats()
    assert stats["by_kind"]["setting.reset"] == 1
    assert stats["events"] == sum(stats["by_kind"].values())
    assert stats["bytes"] > 0
