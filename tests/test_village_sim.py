"""The simulator's exit tests: the bar any change to the body's rules must clear."""

from __future__ import annotations

import pytest

from jimmyhq.village.sim.report import exit_tests, render
from jimmyhq.village.sim.runner import run_all, simulate
from jimmyhq.village.sim.scenarios import SCENARIOS
from jimmyhq.village.world import getter

SEEDS = 20


@pytest.fixture(scope="module")
def results():
    return run_all(getter(), SEEDS)


@pytest.fixture(scope="module")
def tests(results):
    return {t["name"]: t for t in exit_tests(results, getter())}


def check(tests, name):
    assert tests[name]["pass"], tests[name]["detail"]


def test_no_need_stays_pinned_at_either_end_for_two_hours_while_awake(tests):
    check(tests, "No need pinned at 0 or 100 for 2h+ while awake")


def test_every_simulated_need_change_has_a_cause_and_evidence(tests):
    check(tests, "Every need change has a cause and evidence")


def test_set_asides_fire_on_tasks_that_loop(tests):
    check(tests, "Set-asides fire on loops")


def test_healthy_repetition_is_not_punished(tests):
    check(tests, "Healthy repetition is not punished")


def test_the_body_never_contradicts_its_own_log(tests):
    check(tests, "The body never contradicts its own log")


def test_waiting_on_the_owner_never_forces_a_break_by_itself(tests):
    check(tests, "Waiting on the owner never forces an outlet by itself")


def test_there_are_six_exit_tests_and_the_report_lists_each(results, tests):
    assert len(tests) == 6
    md = render(results, list(tests.values()), SEEDS, 1.0, {"energy.action_cost": 0.2})
    assert "Exit tests passed: **6/6**" in md
    assert all(name in md for name in tests) and all(name in md for name in SCENARIOS)
    assert "energy.action_cost=0.2" in md


def test_the_same_seed_gives_the_same_run():
    one = simulate(SCENARIOS["loop_trap"], 3, getter())
    two = simulate(SCENARIOS["loop_trap"], 3, getter())
    assert one == two and one["events"] > 1000


def test_agents_in_every_scenario_sleep_each_night_and_get_work_done(results):
    for name, runs in results.items():
        for r in runs:
            assert r["sleeps"]["night"] >= r["days"] - 1, name
            assert all(8 <= h / r["days"] <= 20 for h in r["awake_hours"].values()), name
    assert all(r["completions"] > 0 for r in results["onboarding_week"])


def test_a_setting_pushed_far_from_its_tuned_value_fails_an_exit_test():
    # The exit tests must be able to fail, or they prove nothing.
    get = getter({"frustration.set_aside_at": 100.0, "frustration.failed_action": 0.0,
                  "frustration.guard_block": 0.0})
    results = {"loop_trap": [simulate(SCENARIOS["loop_trap"], s, get) for s in range(3)]}
    failed = [t["name"] for t in exit_tests(results, get) if not t["pass"]]
    assert "Set-asides fire on loops" in failed
