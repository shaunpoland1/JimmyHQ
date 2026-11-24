"""Every tuned number the world reads, with its allowed range.

These came out of live rehearsal: change one only with the simulator's exit tests in
hand. This table is the single source: the settings registry declares each row as
``village.<key>`` and the simulator and tests read the defaults straight from here.

Rates are per minute unless the name says otherwise. Needs run from 0 to 100.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# (key, type, default, min, max)
TABLE: tuple[tuple[str, str, float, float, float], ...] = (
    ("village.headcount_cap", "int", 5, 1, 20),
    ("village.heartbeat_min_s", "int", 10, 5, 600),
    ("village.heartbeat_max_s", "int", 300, 30, 3600),

    ("energy.drain_per_min", "float", 0.075, 0.0, 1.0),
    ("energy.late_day_multiplier", "float", 1.6, 1.0, 3.0),
    ("energy.action_cost", "float", 0.15, 0.0, 5.0),
    ("energy.effort_full_after_min", "float", 2.0, 0.0, 30.0),
    ("energy.rest_gain_per_min", "float", 0.25, 0.0, 2.0),
    ("energy.sleep_gain_per_min", "float", 0.2, 0.0, 2.0),

    ("frustration.failed_action", "float", 5.0, 0.0, 50.0),
    ("frustration.same_no_new_result", "float", 9.0, 0.0, 50.0),
    ("frustration.guard_block", "float", 4.0, 0.0, 50.0),
    ("frustration.wait_per_hour", "float", 1.0, 0.0, 20.0),
    ("frustration.wait_cap_per_item", "float", 5.0, 0.0, 50.0),
    ("frustration.wait_cap_total", "float", 12.0, 0.0, 100.0),
    ("frustration.relief_floor", "float", 8.0, 0.0, 40.0),
    ("frustration.small_win", "float", 10.0, 0.0, 50.0),
    ("frustration.progress_relief", "float", 2.0, 0.0, 20.0),
    ("frustration.outlet_relief", "float", 3.5, 0.0, 20.0),
    ("frustration.vent_relief", "float", 6.0, 0.0, 30.0),
    ("frustration.fatigue_factor", "float", 0.5, 0.0, 2.0),
    ("frustration.sleep_baseline", "float", 12.0, 0.0, 50.0),
    ("frustration.sleep_settle_per_min", "float", 0.15, 0.0, 2.0),
    ("frustration.set_aside_at", "float", 50.0, 10.0, 100.0),
    ("frustration.resume_below", "float", 30.0, 0.0, 90.0),
    ("frustration.resume_by_choice_min", "float", 20.0, 0.0, 480.0),
    ("frustration.chosen_resume_margin", "float", 12.0, 0.0, 50.0),

    ("social.drain_per_min", "float", 0.06, 0.0, 1.0),
    ("social.message_fill", "float", 4.0, 0.0, 30.0),
    ("social.heard_share", "float", 0.6, 0.0, 1.5),
    ("social.topic_match_mult", "float", 1.5, 0.5, 3.0),
    ("social.forced_mult", "float", 0.4, 0.0, 1.5),
    ("social.empty_room_wait_min", "float", 15.0, 1.0, 120.0),
    ("social.empty_room_cooldown_min", "float", 60.0, 0.0, 480.0),
    ("social.frustrated_say_chars", "int", 160, 20, 2000),
    ("social.say_chars", "int", 700, 50, 4000),
    ("social.full_at", "float", 85.0, 50.0, 100.0),

    ("curiosity.routine_gain_per_min", "float", 0.05, 0.0, 1.0),
    ("curiosity.idle_gain_per_min", "float", 0.02, 0.0, 1.0),
    ("curiosity.explore_relief", "float", 5.0, 0.0, 30.0),

    ("purpose.drain_per_min", "float", 0.035, 0.0, 1.0),
    ("purpose.verified_completion", "float", 15.0, 0.0, 60.0),
    ("purpose.submission", "float", 5.0, 0.0, 30.0),
    ("purpose.acknowledgement", "float", 8.0, 0.0, 30.0),

    ("modes.rest_below_energy", "float", 20.0, 0.0, 60.0),
    ("modes.outlet_at_frustration", "float", 62.0, 10.0, 100.0),
    ("modes.social_low", "float", 25.0, 0.0, 80.0),
    ("modes.curiosity_high", "float", 75.0, 20.0, 100.0),

    ("sleep.start_hour", "float", 23.0, 18.0, 26.0),
    ("sleep.wake_hour", "float", 7.0, 4.0, 11.0),
    ("sleep.min_energy_to_wake", "float", 70.0, 20.0, 100.0),
    ("sleep.latest_wake_extra_h", "float", 2.0, 0.0, 6.0),
    ("sleep.forced_sleep_energy", "float", 5.0, 0.0, 30.0),
    ("sleep.nap_below_energy", "float", 25.0, 0.0, 60.0),
    ("sleep.nap_minutes", "float", 90.0, 10.0, 180.0),
    ("sleep.early_night_hour", "float", 20.0, 18.0, 23.0),
    ("sleep.chosen_rest_from_hour", "float", 16.0, 12.0, 23.0),
    ("sleep.early_night_energy", "float", 40.0, 0.0, 60.0),

    ("guards.repeat_window", "int", 6, 1, 50),
    ("guards.wall_after", "int", 4, 2, 30),

    ("divergence.window_minutes", "int", 120, 15, 720),
    ("divergence.feeling_repeat_share", "float", 0.6, 0.1, 1.0),
    ("divergence.action_diversity_min", "float", 0.4, 0.0, 1.0),
    ("divergence.progress_min", "int", 3, 1, 50),
)

DEFAULTS: dict[str, Any] = {key: default for key, _type, default, _min, _max in TABLE}

# What the world calls to read one setting, by the short key used in this table.
Getter = Callable[[str], Any]


def getter(overrides: Mapping[str, Any] | None = None) -> Getter:
    """A settings reader over the defaults, with optional overrides read live from the
    mapping given. An unknown key raises KeyError, so a typo cannot pass silently."""
    over = overrides if overrides is not None else {}

    def get(key: str) -> Any:
        if key in over:
            return over[key]
        return DEFAULTS[key]

    return get
