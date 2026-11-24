from __future__ import annotations

from jimmyhq.lens import Lens, LensRunner


class Counter(Lens):
    name = "counter"
    version = 1
    kinds = ("setting.updated",)

    def initial(self):
        return {"n": 0}

    def fold(self, state, event):
        state["n"] += 1
        return state


def _update(core, value):
    core.tape.print("setting.updated", {"key": "k", "value": value}, actor="owner")


def test_a_lens_catches_up_with_new_events_and_only_folds_its_kinds(core):
    lens = Counter()
    _update(core, 1)
    core.tape.print("setting.reset", {"key": "k"}, actor="owner")
    assert core.lenses.read(lens) == {"n": 1}
    _update(core, 2)
    assert core.lenses.read(lens) == {"n": 2}


def test_lens_state_survives_a_restart_without_replaying(cfg, core):
    lens = Counter()
    _update(core, 1)
    core.lenses.read(lens)

    class Exploding(Counter):
        def fold(self, state, event):
            raise AssertionError("replayed an event that was already folded")

    fresh = LensRunner(core.tape, cfg.views_db)
    assert fresh.read(Exploding()) == {"n": 1}
    fresh.close()


def test_bumping_a_lens_version_replays_from_the_first_event(cfg, core):
    _update(core, 1)
    _update(core, 2)
    core.lenses.read(Counter())

    class Doubled(Counter):
        version = 2

        def fold(self, state, event):
            state["n"] += 2
            return state

    fresh = LensRunner(core.tape, cfg.views_db)
    assert fresh.read(Doubled()) == {"n": 4}
    fresh.close()


def test_a_fold_that_raises_partway_leaves_nothing_half_counted(core):
    class Fragile(Counter):
        name = "fragile"
        explode_on = None

        def fold(self, state, event):
            if event.payload["value"] == self.explode_on:
                raise RuntimeError("boom")
            return super().fold(state, event)

    lens = Fragile()
    _update(core, 1)
    assert core.lenses.read(lens) == {"n": 1}
    for value in (2, 3, 4):
        _update(core, value)
    lens.explode_on = 4
    try:
        core.lenses.read(lens)
    except RuntimeError:
        pass
    lens.explode_on = None
    assert core.lenses.read(lens) == {"n": 4}


def test_changing_the_state_a_read_returns_does_not_change_what_others_read(core):
    lens = Counter()
    _update(core, 1)
    core.lenses.read(lens)["n"] = 999
    assert core.lenses.read(lens) == {"n": 1}


def test_an_event_of_a_kind_this_process_does_not_know_stops_nothing(core):
    core.tape._conn.execute(
        "INSERT INTO tape (id, ts, kind, v, actor, payload) VALUES (?,?,?,?,?,?)",
        ("01UNKNOWNKIND0000000000000", 1, "app.gone.thing", 1, "app:gone", '{"x":1}'),
    )
    core.tape._conn.execute(
        "INSERT INTO tape (id, ts, kind, v, actor, payload) VALUES (?,?,?,?,?,?)",
        ("01NEWERVERSION000000000000", 2, "setting.reset", 9, "owner", '{"key":"k","new":true}'),
    )
    _update(core, 1)
    kinds = [e.kind for e in core.tape.after(0)]
    assert "app.gone.thing" in kinds
    raw = core.tape.get("01UNKNOWNKIND0000000000000")
    assert (raw.v, raw.payload, raw.readable) == (1, {"x": 1}, False)
    assert core.lenses.read(Counter()) == {"n": 1}
    assert core.settings.get("appearance.theme") == "system"
    assert len(core.tape.latest(limit=10)) >= 3


def test_a_newer_shape_of_a_kind_a_lens_folds_is_skipped_not_folded(core):
    """A newer release, or a newer worker beside an older web process, wrote it."""
    core.tape._conn.execute(
        "INSERT INTO tape (id, ts, kind, v, actor, payload) VALUES (?,?,?,?,?,?)",
        ("01NEWSHAPE0000000000000000", 1, "setting.updated", 2, "owner",
         '{"name":"appearance.theme","to":"dark"}'),
    )
    core.settings.set("appearance.theme", "light", actor="owner")
    assert core.settings.get("appearance.theme") == "light"
    assert core.settings.describe()["settings"]
    assert core.lenses.read(Counter()) == {"n": 1}


def test_a_stored_payload_that_no_longer_validates_is_handed_back_marked_unreadable(core):
    core.tape._conn.execute(
        "INSERT INTO tape (id, ts, kind, v, actor, payload) VALUES (?,?,?,?,?,?)",
        ("01TIGHTENED000000000000000", 1, "system.opened", 1, "system",
         '{"version":"1","extra":true}'),
    )
    event = core.tape.get("01TIGHTENED000000000000000")
    assert event.readable is False and event.payload["extra"] is True
    assert core.tape.latest(limit=5)


def test_a_known_kind_with_no_upgrade_path_is_a_loud_error_not_a_quiet_skip(core):
    import pytest

    from jimmyhq.tape.registry import MissingUpcasterError, Payload, kind

    @kind("testing.gap", v=1, example={"a": 1})
    class V1(Payload):
        a: int

    stored = core.tape.print("testing.gap", {"a": 1}, actor="owner")

    @kind("testing.gap", v=2, example={"b": 1})
    class V2(Payload):
        b: int

    with pytest.raises(MissingUpcasterError):
        core.tape.get(stored.id)


def test_reading_far_past_the_end_of_the_tape_is_empty_not_a_crash(core):
    assert core.tape.after(2**70) == []
    assert core.tape.after(0, upto=2**70, limit=-1)
    assert core.tape.after(0, kinds=()) == []


def test_a_view_that_has_read_past_the_end_of_a_restored_tape_is_replayed(cfg, core):
    lens = Counter()
    _update(core, 1)
    _update(core, 2)
    assert core.lenses.read(lens) == {"n": 2}
    # The stored views were copied after a Tape that had ten fewer events.
    import sqlite3

    conn = sqlite3.connect(cfg.views_db)
    conn.execute("UPDATE lens_state SET last_seq = last_seq + 10, state = '{\"n\": 12}'")
    conn.commit()
    conn.close()
    fresh = LensRunner(core.tape, cfg.views_db)
    try:
        assert fresh.read(lens) == {"n": 2}  # what this Tape holds, nothing more
        _update(core, 3)  # its number is one the old view had counted as read
        assert fresh.read(lens) == {"n": 3}
    finally:
        fresh.close()
