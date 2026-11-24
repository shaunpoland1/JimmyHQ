"""The village's settings: every tuned number, declared with its range and plain words."""

from __future__ import annotations

import re

import pytest

from jimmyhq import modules
from jimmyhq.settings import SettingError, declared
from jimmyhq.village.settings import TEXT, full_key
from jimmyhq.village.world.defaults import DEFAULTS, TABLE

# (key, type, default, lowest, highest). These came out of live rehearsal. Change one
# here only together with the table in the world, and only with the simulator's exit
# tests still passing.
TUNED = [
    ("village.headcount_cap", "int", 5, 1, 20),
    ("village.heartbeat_min_s", "int", 10, 5, 600),
    ("village.heartbeat_max_s", "int", 300, 30, 3600),
    ("village.energy.drain_per_min", "float", 0.075, 0.0, 1.0),
    ("village.energy.late_day_multiplier", "float", 1.6, 1.0, 3.0),
    ("village.energy.action_cost", "float", 0.15, 0.0, 5.0),
    ("village.energy.effort_full_after_min", "float", 2.0, 0.0, 30.0),
    ("village.energy.rest_gain_per_min", "float", 0.25, 0.0, 2.0),
    ("village.energy.sleep_gain_per_min", "float", 0.2, 0.0, 2.0),
    ("village.frustration.failed_action", "float", 5.0, 0.0, 50.0),
    ("village.frustration.same_no_new_result", "float", 9.0, 0.0, 50.0),
    ("village.frustration.guard_block", "float", 4.0, 0.0, 50.0),
    ("village.frustration.wait_per_hour", "float", 1.0, 0.0, 20.0),
    ("village.frustration.wait_cap_per_item", "float", 5.0, 0.0, 50.0),
    ("village.frustration.wait_cap_total", "float", 12.0, 0.0, 100.0),
    ("village.frustration.relief_floor", "float", 8.0, 0.0, 40.0),
    ("village.frustration.small_win", "float", 10.0, 0.0, 50.0),
    ("village.frustration.progress_relief", "float", 2.0, 0.0, 20.0),
    ("village.frustration.outlet_relief", "float", 3.5, 0.0, 20.0),
    ("village.frustration.vent_relief", "float", 6.0, 0.0, 30.0),
    ("village.frustration.fatigue_factor", "float", 0.5, 0.0, 2.0),
    ("village.frustration.sleep_baseline", "float", 12.0, 0.0, 50.0),
    ("village.frustration.sleep_settle_per_min", "float", 0.15, 0.0, 2.0),
    ("village.frustration.set_aside_at", "float", 50.0, 10.0, 100.0),
    ("village.frustration.resume_below", "float", 30.0, 0.0, 90.0),
    ("village.frustration.resume_by_choice_min", "float", 20.0, 0.0, 480.0),
    ("village.frustration.chosen_resume_margin", "float", 12.0, 0.0, 50.0),
    ("village.social.drain_per_min", "float", 0.06, 0.0, 1.0),
    ("village.social.message_fill", "float", 4.0, 0.0, 30.0),
    ("village.social.heard_share", "float", 0.6, 0.0, 1.5),
    ("village.social.topic_match_mult", "float", 1.5, 0.5, 3.0),
    ("village.social.forced_mult", "float", 0.4, 0.0, 1.5),
    ("village.social.empty_room_wait_min", "float", 15.0, 1.0, 120.0),
    ("village.social.empty_room_cooldown_min", "float", 60.0, 0.0, 480.0),
    ("village.social.frustrated_say_chars", "int", 160, 20, 2000),
    ("village.social.say_chars", "int", 700, 50, 4000),
    ("village.social.full_at", "float", 85.0, 50.0, 100.0),
    ("village.curiosity.routine_gain_per_min", "float", 0.05, 0.0, 1.0),
    ("village.curiosity.idle_gain_per_min", "float", 0.02, 0.0, 1.0),
    ("village.curiosity.explore_relief", "float", 5.0, 0.0, 30.0),
    ("village.purpose.drain_per_min", "float", 0.035, 0.0, 1.0),
    ("village.purpose.verified_completion", "float", 15.0, 0.0, 60.0),
    ("village.purpose.submission", "float", 5.0, 0.0, 30.0),
    ("village.purpose.acknowledgement", "float", 8.0, 0.0, 30.0),
    ("village.modes.rest_below_energy", "float", 20.0, 0.0, 60.0),
    ("village.modes.outlet_at_frustration", "float", 62.0, 10.0, 100.0),
    ("village.modes.social_low", "float", 25.0, 0.0, 80.0),
    ("village.modes.curiosity_high", "float", 75.0, 20.0, 100.0),
    ("village.sleep.start_hour", "float", 23.0, 18.0, 26.0),
    ("village.sleep.wake_hour", "float", 7.0, 4.0, 11.0),
    ("village.sleep.min_energy_to_wake", "float", 70.0, 20.0, 100.0),
    ("village.sleep.latest_wake_extra_h", "float", 2.0, 0.0, 6.0),
    ("village.sleep.forced_sleep_energy", "float", 5.0, 0.0, 30.0),
    ("village.sleep.nap_below_energy", "float", 25.0, 0.0, 60.0),
    ("village.sleep.nap_minutes", "float", 90.0, 10.0, 180.0),
    ("village.sleep.early_night_hour", "float", 20.0, 18.0, 23.0),
    ("village.sleep.chosen_rest_from_hour", "float", 16.0, 12.0, 23.0),
    ("village.sleep.early_night_energy", "float", 40.0, 0.0, 60.0),
    ("village.guards.repeat_window", "int", 6, 1, 50),
    ("village.guards.wall_after", "int", 4, 2, 30),
    ("village.divergence.window_minutes", "int", 120, 15, 720),
    ("village.divergence.feeling_repeat_share", "float", 0.6, 0.1, 1.0),
    ("village.divergence.action_diversity_min", "float", 0.4, 0.0, 1.0),
    ("village.divergence.progress_min", "int", 3, 1, 50),
]


def village_settings():
    """The settings of the world table (the body's tuned numbers). The beat, the tools and
    the night declare their own ``village.*`` settings, pinned in their own tests."""
    modules.load()
    world = {full_key(key) for key in DEFAULTS}
    return {s.key: s for s in declared() if s.key in world}


def test_every_village_setting_is_declared_with_its_tuned_default_and_range():
    specs = village_settings()
    assert sorted(specs) == sorted(row[0] for row in TUNED) and len(TUNED) == 64
    for key, kind, default, low, high in TUNED:
        s = specs[key]
        assert (s.type, s.default, s.min, s.max) == (kind, default, low, high), key
        assert type(s.default) is (int if kind == "int" else float), key


def test_the_pure_world_reads_the_same_defaults_the_settings_screen_shows():
    specs = village_settings()
    assert len(TABLE) == len(DEFAULTS) == len(specs)
    for key, value in DEFAULTS.items():
        assert specs[full_key(key)].default == value


def test_every_village_setting_has_a_label_and_help_in_plain_words():
    specs = village_settings()
    assert set(TEXT) == set(DEFAULTS)
    labels = [s.label for s in specs.values()]
    assert len(set(labels)) == len(labels)
    for s in specs.values():
        assert 5 <= len(s.label) <= 70 and s.help.endswith("."), s.key
        assert s.section in ("village", "village_turn", "village_body", "village_sleep",
                             "village_guards")
        words = f"{s.label} {s.help}".lower()
        for jargon in ("validator", "pts", "outlet", "divergence", "circadian", "mult"):
            assert not re.search(rf"\b{jargon}\b", words), (s.key, jargon)


def test_a_village_setting_refuses_a_value_outside_its_range(core):
    with pytest.raises(SettingError):
        core.settings.set("village.guards.repeat_window", 10_000, actor="owner")
    with pytest.raises(SettingError):
        core.settings.set("village.guards.repeat_window", 2.5, actor="owner")
    with pytest.raises(SettingError):
        core.settings.set("village.sleep.start_hour", 12, actor="owner")
    assert core.settings.set("village.guards.repeat_window", 12, actor="owner") == 12
    assert core.settings.get("village.guards.repeat_window") == 12
    assert core.settings.get("village.sleep.start_hour") == 23.0
