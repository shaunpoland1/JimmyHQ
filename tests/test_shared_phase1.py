"""Shared-code behaviour added while reviewing phase 1."""

from __future__ import annotations

import threading

import pytest

from jimmyhq.settings import Setting, SettingError
from jimmyhq.tape import Tape, TapeConflict


def _spec(**extra):
    return Setting(key="t.list", section="profile", type="list", default=["a"],
                   label="Tiles", help="x", **extra)


def test_a_list_setting_limited_to_known_items_refuses_others_and_says_which_are_allowed():
    spec = _spec(items=(("a", "A"), ("b", "B")), unique=True)
    assert spec.check(["b", "a"]) == ["b", "a"]
    with pytest.raises(SettingError, match="can only contain: a, b"):
        spec.check(["a", "nonsense"])
    with pytest.raises(SettingError, match="same item twice"):
        spec.check(["a", "a"])


def test_a_settings_own_check_can_refuse_or_tidy_a_value():
    def no_spaces(value):
        if " " in value:
            raise SettingError("Name cannot contain spaces.")
        return value.lower()

    spec = Setting(key="t.name", section="profile", type="str", default="", label="Name",
                   help="x", validate=no_spaces)
    assert spec.check("ABC") == "abc"
    with pytest.raises(SettingError, match="cannot contain spaces"):
        spec.check("a b")


def test_a_time_zone_this_computer_does_not_know_is_refused_when_saved(core):
    core.settings.set("owner.timezone", " Europe/Paris ", actor="owner")
    assert core.settings.get("owner.timezone") == "Europe/Paris"
    with pytest.raises(SettingError, match="IANA name such as"):
        core.settings.set("owner.timezone", "Mars/Olympus", actor="owner")
    assert core.settings.set("owner.timezone", "", actor="owner") == ""


def test_the_description_tells_the_screen_about_pick_lists(core):
    described = {s["key"]: s for s in core.settings.describe()["settings"]}
    order = described["appearance.sidebar_order"]
    assert {"value": "bench", "label": "Bench"} in order["items"] and order["unique"] is True
    assert "items" not in described["owner.name"]
    assert described["owner.week_starts"]["type"] == "choice"


def test_a_guarded_write_lands_only_if_nothing_else_touched_the_subject(core):
    first = core.tape.print("setting.reset", {"key": "k"}, actor="owner", subject="thing:1",
                            expect_subject_seq=0)
    assert core.tape.subject_last_seq("thing:1") == first.seq
    with pytest.raises(TapeConflict):
        core.tape.print("setting.reset", {"key": "k"}, actor="owner", subject="thing:1",
                        expect_subject_seq=0)
    assert len(core.tape.by_subject("thing:1")) == 1
    core.tape.print("setting.reset", {"key": "k"}, actor="owner", subject="thing:1",
                    expect_subject_seq=first.seq)


def test_of_two_processes_deciding_the_same_thing_at_once_exactly_one_wins(cfg, core):
    other = Tape(cfg.tape_db)
    core.tape.print("setting.reset", {"key": "k"}, actor="owner", subject="thing:2")
    seen = core.tape.subject_last_seq("thing:2")
    barrier = threading.Barrier(2)
    outcomes = []

    def decide(tape):
        barrier.wait()
        try:
            tape.print("setting.reset", {"key": "k"}, actor="owner", subject="thing:2",
                       expect_subject_seq=seen)
            outcomes.append("won")
        except TapeConflict:
            outcomes.append("lost")

    threads = [threading.Thread(target=decide, args=(t,)) for t in (core.tape, other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    other.close()
    assert sorted(outcomes) == ["lost", "won"]
    assert len(core.tape.by_subject("thing:2")) == 2


def test_a_subjects_events_can_be_read_a_page_at_a_time_from_either_end(core):
    seqs = [core.tape.print("setting.reset", {"key": str(i)}, actor="owner",
                            subject="thing:3").seq for i in range(5)]
    newest = core.tape.by_subject("thing:3", limit=2, newest_first=True)
    assert [e.seq for e in newest] == seqs[:2:-1]
    older = core.tape.by_subject("thing:3", limit=2, newest_first=True, before=newest[-1].seq)
    assert [e.seq for e in older] == [seqs[2], seqs[1]]
    assert [e.seq for e in core.tape.by_subject("thing:3", after=seqs[3])] == [seqs[4]]


def test_a_session_in_use_renews_itself_once_half_its_life_is_gone(core):
    token = core.auth.create_session(days=30)
    assert core.auth.renew_session(token, 30) is False  # plenty of life left
    core.auth._conn.execute("UPDATE sessions SET expires = expires - ?", (20 * 86_400_000,))
    assert core.auth.renew_session(token, 30) is True
    assert core.auth.renew_session(token, 30) is False
    core.auth._conn.execute("UPDATE sessions SET expires = 1")
    assert core.auth.renew_session(token, 30) is False  # an ended session stays ended
    assert not core.auth.session_valid(token)
