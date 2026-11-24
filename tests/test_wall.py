from __future__ import annotations

import pytest

from jimmyhq.settings import SettingError
from jimmyhq.wall.settings import TILES


def test_the_wall_shows_every_tile_in_the_agreed_order_until_told_otherwise(core):
    assert core.settings.get("wall.tiles") == ["clock", "usage", "bench", "today", "models", "feed"]
    assert core.settings.get("wall.tiles") == list(TILES)


def test_the_wall_starts_out_following_the_app_and_standing_still(core):
    assert core.settings.get("wall.clock_24h") is False
    assert core.settings.get("wall.show_seconds") is False
    assert core.settings.get("wall.force_dark") is False
    assert core.settings.get("wall.pixel_shift") is False
    assert core.settings.get("wall.refresh_s") == 60
    assert core.settings.get("wall.feed_lines") == 8
    assert core.settings.get("wall.task_lines") == 8


def test_the_wall_settings_are_declared_for_the_settings_screen(core):
    described = core.settings.describe()
    assert {"id": "wall", "label": "The Wall"} in described["sections"]
    keys = {s["key"] for s in described["settings"] if s["section"] == "wall"}
    assert keys == {
        "wall.tiles", "wall.clock_24h", "wall.show_seconds", "wall.force_dark",
        "wall.refresh_s", "wall.pixel_shift", "wall.pixel_shift_minutes",
        "wall.feed_lines", "wall.task_lines",
    }


def test_tiles_can_be_reordered_and_hidden(core):
    core.settings.set("wall.tiles", ["feed", "clock"], actor="owner")
    assert core.settings.get("wall.tiles") == ["feed", "clock"]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("wall.refresh_s", 5, "cannot be less than 10"),
        ("wall.refresh_s", 3601, "cannot be more than 3600"),
        ("wall.feed_lines", 0, "cannot be less than 1"),
        ("wall.task_lines", 51, "cannot be more than 50"),
        ("wall.pixel_shift_minutes", 0, "cannot be less than 1"),
        ("wall.force_dark", "yes", "on or off"),
        ("wall.tiles", "clock", "list of text"),
    ],
)
def test_a_wall_setting_out_of_range_is_refused(core, key, value, message):
    with pytest.raises(SettingError, match=message):
        core.settings.set(key, value, actor="owner")
