"""A full app that keeps crashing: held back, recorded on the Tape, the owner told (or
not, as they chose), and started again only by the owner. Never switched off or rolled
back for them."""

from __future__ import annotations

import pytest

from apps_full_helpers import install_full
from jimmyhq import bench
from jimmyhq.apps import runtime, store
from jimmyhq.apps.plugs import crashes
from jimmyhq.ids import now_ms


@pytest.fixture
def shaky(core, tmp_path):
    install_full(core, tmp_path, "shaky")
    runtime.publish(core)
    return "shaky"


def held(core, app_id: str, *, generation: int = 0, at: int = 1000, code: int = 3) -> None:
    """The supervisor's word that it held the program back."""
    runtime.write_json(runtime.status_file(core.config.data), {"written": now_ms(), "apps": {
        app_id: {"state": "crashing", "version": "1.0.0-00000000", "generation": generation,
                 "pid": None, "started_at": at - 500, "failures": 5, "restarts": [],
                 "last_exit": {"code": code, "at": at, "ran_s": 0.4}}}})


def events(core, kind: str):
    return [e for e in core.tape.after(0, limit=500) if e.kind == kind]


def held_cards(core):
    lanes = bench.board(core)["lanes"]
    return [c for cards in lanes.values() for c in cards if c["kind"] == crashes.CARD_KIND]


def test_a_program_held_back_is_recorded_on_the_tape_once(core, shaky):
    held(core, shaky)
    assert crashes.notice(core) == ["shaky"]
    assert crashes.notice(core) == []
    [event] = events(core, "apps.held_back")
    assert event.payload == {"app_id": "shaky", "version": "1.0.0", "exits": 5,
                             "last_code": 3, "told": True, "then": "held"}
    assert event.actor == "system" and event.subject == "app:shaky"


def test_by_default_the_owner_is_told_with_a_card_that_says_how_to_start_it_again(core,
                                                                                 shaky):
    held(core, shaky)
    crashes.notice(core)
    [card] = held_cards(core)
    assert card["lane"] == "done" and "Shaky" in card["title"]
    assert "Restart" in bench.get_card(core, card["id"])["body"]


def test_with_hold_only_it_is_recorded_but_no_card_is_filed(core, shaky):
    core.settings.set("apps.crash_loop", "hold", actor="owner")
    held(core, shaky)
    crashes.notice(core)
    assert events(core, "apps.held_back")[0].payload["told"] is False
    assert held_cards(core) == []


def test_held_back_again_after_a_restart_is_told_again(core, shaky):
    held(core, shaky, generation=0, at=1000)
    crashes.notice(core)
    held(core, shaky, generation=1, at=9000)
    assert crashes.notice(core) == ["shaky"]
    assert len(events(core, "apps.held_back")) == 2


def test_holding_back_never_switches_the_app_off_or_rolls_it_back(core, shaky):
    held(core, shaky)
    crashes.notice(core)
    assert store.get(core, shaky)["enabled"]
    assert events(core, "apps.disabled") == [] and events(core, "apps.rolled_back") == []


def test_the_owners_restart_is_recorded_with_how_the_app_was(core, shaky):
    held(core, shaky)
    runtime.restart(core, shaky)
    [event] = events(core, "apps.restarted")
    assert event.payload == {"app_id": "shaky", "was": "crashing"}
    assert event.actor == "owner"
    generation = runtime.read_json(runtime.run_file(core.config.data))["apps"][0]["generation"]
    assert generation == 1


def test_the_screen_says_held_back_after_crashing(core, shaky):
    held(core, shaky)
    found = runtime.health(core, shaky)
    assert found["state"] == "crashing" and found["label"] == "Held back after crashing"


def test_quick_exits_hold_a_program_back_unless_the_owner_chose_to_keep_trying():
    assert not runtime.quick_exits_end_retries(4, 5)
    assert runtime.quick_exits_end_retries(5, 5, "hold_and_tell")
    assert runtime.quick_exits_end_retries(5, 5, "hold")
    assert not runtime.quick_exits_end_retries(50, 5, "keep_trying")


def test_the_choice_reaches_the_supervisor_in_what_should_run(core, shaky, tmp_path):
    core.settings.set("apps.crash_loop", "keep_trying", actor="owner")
    assert runtime.publish(core)["crash_loop"] == "keep_trying"
    kids = runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth)
    kids.read_run()
    assert kids.crash_loop == "keep_trying"


def test_a_supervisor_reading_an_unknown_choice_holds_back_and_tells(core, shaky, tmp_path):
    path = runtime.run_file(core.config.data)
    runtime.write_json(path, runtime.read_json(path) | {"crash_loop": "rm -rf"})
    kids = runtime.AppChildren(core.config.data, tmp_path / "logs", "http://127.0.0.1:9",
                               auth=core.auth)
    kids.read_run()
    assert kids.crash_loop == "hold_and_tell"


def test_the_list_of_apps_says_how_each_full_apps_program_is(core, shaky):
    from jimmyhq.apps import install

    held(core, shaky)
    [row] = install.listing(core)
    assert row["health"] == "crashing" and row["running"] is False
