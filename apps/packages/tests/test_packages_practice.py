"""Practice moves a package along by itself, as a tracker's reading would, so a delivered
package and its card on the Bench can be seen with nothing looked up."""

from __future__ import annotations

import pytest
from packages_rig import DAY, MINUTE, RAKE


@pytest.fixture
def practice(make_rig):
    return make_rig(tracker="practice", secrets={}, settings={"practice_minutes": 15})


def run_for(rig, minutes: int) -> None:
    """Let ``minutes`` pass, the job looking every five."""
    for _ in range(minutes // 5):
        rig.clock.advance(5 * MINUTE)
        rig.sync()


def status(rig, package_id: int = 1) -> str:
    return rig.package(package_id)["status"]


def test_a_practice_package_goes_through_its_stages_by_itself_and_ends_delivered_with_its_card(
        practice):
    practice.add()
    assert status(practice) == "waiting" and practice.cards() == []
    seen = []
    for _ in range(3):
        run_for(practice, 5)
        seen.append(status(practice))
    assert seen == ["on_its_way", "out_for_delivery", "delivered"]
    assert [c["title"] for c in practice.cards()] == ["Garden hose was delivered."]
    assert practice.package(1)["delivered_ms"] == practice.clock.now


def test_the_time_a_practice_package_takes_is_the_setting(make_rig):
    rig = make_rig(tracker="practice", secrets={}, settings={"practice_minutes": 30})
    rig.add()
    run_for(rig, 25)
    assert status(rig) == "out_for_delivery"
    run_for(rig, 5)
    assert status(rig) == "delivered"
    quick = make_rig(tracker="practice", secrets={}, settings={"practice_minutes": 3})
    quick.add()
    for _ in range(3):
        quick.clock.advance(MINUTE)
        quick.sync()
    assert status(quick) == "delivered"


def test_every_stage_is_written_on_the_package_as_practice_news_and_says_nothing_was_looked_up(
        practice):
    practice.add()
    run_for(practice, 5)
    row = practice.package(1)
    assert row["event"].startswith("Practice: pretending it is on its way")
    assert "Nothing was looked up" in row["event"] and row["event_ms"] == practice.clock.now
    assert practice.service.calls == [] and practice.core.fetched == []


def test_a_package_the_owner_set_by_hand_is_left_alone(practice):
    practice.add()
    practice.core.call("package.mark", {"id": 1, "status": "problem"})
    run_for(practice, 20)
    assert status(practice) == "problem"
    other = practice.add(label="Rake", number=RAKE)
    practice.core.call("package.mark", {"id": other["id"], "status": "on_its_way"})
    run_for(practice, 20)
    assert status(practice, other["id"]) == "on_its_way"


def test_taking_back_a_mark_starts_the_practice_journey_again_from_waiting(practice):
    practice.add()
    practice.core.call("package.mark", {"id": 1, "status": "on_its_way"})
    practice.core.call("package.unmark", {"id": 1})
    assert status(practice) == "waiting"
    run_for(practice, 5)
    assert status(practice) == "on_its_way"


def test_packages_added_later_start_their_own_journey(practice):
    practice.add()
    run_for(practice, 15)
    practice.add(label="Rake", number=RAKE)
    assert status(practice, 2) == "waiting"
    run_for(practice, 5)
    assert status(practice, 1) == "delivered" and status(practice, 2) == "on_its_way"


def test_a_delivered_practice_package_is_told_once_however_long_the_job_keeps_looking(practice):
    practice.add()
    run_for(practice, 15)
    run_for(practice, 60)
    assert len(practice.core.cards) == 1 and status(practice) == "delivered"


def test_with_any_change_each_practice_stage_is_told_and_the_card_comes_with_the_name_only(
        make_rig):
    rig = make_rig(tracker="practice", secrets={},
                   settings={"practice_minutes": 15, "tell_when": "any_change"})
    rig.add()
    run_for(rig, 15)
    assert [c["title"] for c in rig.cards()] == ["Garden hose was delivered."]
    assert len([a for a in rig.core.filed]) == 3  # on its way, out for delivery, delivered


def test_refresh_now_under_practice_moves_what_is_due_and_says_so(practice):
    practice.add()
    nothing = practice.core.call("refresh")
    assert nothing["ok"] and "looks nothing up" in nothing["said"]
    assert "No package was due" in nothing["said"]
    practice.clock.advance(5 * MINUTE)
    done = practice.core.call("refresh")
    assert "moved 1 package a step along" in done["said"] and status(practice) == "on_its_way"


def test_the_screen_is_told_it_is_practice_and_for_how_long(practice):
    got = practice.core.call("overview")
    assert got["tracker"]["practice"] is True
    assert got["schedule"]["practice"] and got["schedule"]["practice_minutes"] == 15


def test_a_real_tracker_does_not_move_packages_by_itself(rig):
    rig.store.insert("package", {"label": "Rake", "number": RAKE, "created": 0, "updated": 0})
    rig.clock.advance(DAY)
    assert rig.core.call("overview")["tracker"]["practice"] is False
    from packages import pretend
    assert pretend.progress(rig.ctx) == 0


def test_practice_does_not_tell_the_owner_to_set_each_status_when_packages_move_by_themselves():
    from packages.trackers.practice import Practice

    help_text = Practice.carrier_help.lower()
    assert "by itself" in help_text and "yourself" in help_text
    assert "set each status" not in help_text
