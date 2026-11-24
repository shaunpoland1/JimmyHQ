"""Settings for the Wall. Importing this module declares them."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

TILES = ("clock", "usage", "bench", "today", "models", "feed")
_TILE_LABELS = {"clock": "Clock", "usage": "Usage", "bench": "Bench", "today": "Today",
                "models": "Models", "feed": "Live feed"}

section("wall", "The Wall", group="work", order=50,
        description="The always-on display: its tiles, its clock and how it protects the screen.")

declare(Setting(
    key="wall.tiles", section="wall", type="list", default=list(TILES),
    label="Tiles",
    help="Which tiles the Wall shows, in order. Remove one to hide it.",
    items=tuple((tile, _TILE_LABELS[tile]) for tile in TILES), unique=True,
))
declare(Setting(
    key="wall.clock_24h", section="wall", type="bool", default=False,
    label="24-hour clock",
    help="Show times on the Wall as 14:05 rather than 2:05 PM.",
))
declare(Setting(
    key="wall.show_seconds", section="wall", type="bool", default=False,
    label="Show seconds",
    help="Have the Wall's clock count seconds.",
))
declare(Setting(
    key="wall.force_dark", section="wall", type="bool", default=False,
    label="Always dark",
    help="Keep the Wall dark whatever the app's theme is. Off follows the app's theme.",
))
declare(Setting(
    key="wall.refresh_s", section="wall", type="int", default=60, min=10, max=3600,
    advanced=True,
    label="Refresh at least every (seconds)",
    help="The Wall updates as things happen. It also reads everything again this often, "
         "in case something was missed.",
))
declare(Setting(
    key="wall.pixel_shift", section="wall", type="bool", default=False,
    label="Shift the picture to prevent burn-in",
    help="Move the whole Wall by a few pixels now and then, for screens that are left on "
         "all day.",
))
declare(Setting(
    key="wall.pixel_shift_minutes", section="wall", type="int", default=5, min=1, max=120,
    label="Shift every (minutes)",
    help="How often the Wall moves when the shift is on.",
))
declare(Setting(
    key="wall.feed_lines", section="wall", type="int", default=8, min=1, max=50,
    label="Feed lines to show at most",
    help="The live feed tile shows this many events, or fewer if fewer fit.",
))
declare(Setting(
    key="wall.task_lines", section="wall", type="int", default=8, min=1, max=50,
    label="Tasks to show at most",
    help="The Today tile shows this many tasks, or fewer if fewer fit, then how many more "
         "there are.",
))
