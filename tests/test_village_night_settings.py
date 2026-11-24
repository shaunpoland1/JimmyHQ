"""The night's settings: each tuned number declared with its range and plain words."""

from __future__ import annotations

from jimmyhq import modules
from jimmyhq.settings import declared
from jimmyhq.village.settings_night import NIGHT, night_setting

# (short key, default, lowest, highest)
TUNED = [("tries", 6, 1, 12), ("retry_gap_min", 45, 5, 240), ("smart_only_tries", 3, 0, 12),
         ("carry_days", 3, 0, 14), ("name_lock_nights", 3, 1, 10),
         ("snapshots_kept", 30, 5, 365), ("section_cap", 24, 8, 60),
         ("input_chars", 90000, 20000, 400000)]


def test_every_night_setting_is_declared_with_its_tuned_default_and_range():
    modules.load()
    specs = {s.key: s for s in declared() if s.key.startswith("village.night.")}
    assert sorted(specs) == sorted(f"village.night.{k}" for k, *_ in TUNED) and len(NIGHT) == 8
    for key, default, low, high in TUNED:
        s = specs[f"village.night.{key}"]
        assert (s.type, s.default, s.min, s.max) == ("int", default, low, high), key
        assert s.section == "village_night" and 5 <= len(s.label) <= 70, key
        assert s.help.endswith("."), key


def test_a_night_setting_is_read_live_by_its_short_key(core):
    assert night_setting(core, "tries") == 6
    core.settings.set("village.night.tries", 2, actor="owner")
    assert night_setting(core, "tries") == 2
