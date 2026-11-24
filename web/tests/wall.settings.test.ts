import assert from "node:assert/strict";
import { test } from "node:test";
import { TILE_IDS, WALL_DEFAULTS, readWallSettings, tileOrder } from "../src/modules/wall/lib/settings.ts";

test("with no settings loaded the Wall uses its defaults", () => {
  assert.deepEqual(readWallSettings(undefined), WALL_DEFAULTS);
  assert.deepEqual(readWallSettings([]), WALL_DEFAULTS);
  assert.deepEqual(WALL_DEFAULTS.tiles, ["clock", "usage", "bench", "today", "models", "feed"]);
});

test("the server's values are read, with seconds and minutes turned into milliseconds", () => {
  const settings = readWallSettings([
    { key: "wall.tiles", value: ["feed", "clock"] },
    { key: "wall.clock_24h", value: true },
    { key: "wall.show_seconds", value: true },
    { key: "wall.force_dark", value: true },
    { key: "wall.refresh_s", value: 120 },
    { key: "wall.pixel_shift", value: true },
    { key: "wall.pixel_shift_minutes", value: 10 },
    { key: "wall.feed_lines", value: 12 },
    { key: "wall.task_lines", value: 5 },
    { key: "appearance.theme", value: "dark" },
  ]);
  assert.deepEqual(settings, {
    tiles: ["feed", "clock"],
    clock24h: true,
    showSeconds: true,
    forceDark: true,
    refreshMs: 120_000,
    pixelShift: true,
    pixelShiftMs: 600_000,
    feedLines: 12,
    taskLines: 5,
  });
});

test("the tile list keeps its order and drops what the Wall does not know, and repeats", () => {
  assert.deepEqual(tileOrder(["today", "weather", " Clock ", "today", 4, "FEED"]), ["today", "clock", "feed"]);
  assert.deepEqual(tileOrder([]), []);
  assert.deepEqual(tileOrder("clock"), [...TILE_IDS]);
});

test("an empty tile list means no tiles, not the default ones", () => {
  assert.deepEqual(readWallSettings([{ key: "wall.tiles", value: [] }]).tiles, []);
});

test("a value of the wrong type or out of range cannot break the Wall", () => {
  const settings = readWallSettings([
    { key: "wall.refresh_s", value: 1 },
    { key: "wall.feed_lines", value: 9999 },
    { key: "wall.task_lines", value: "many" },
    { key: "wall.clock_24h", value: "yes" },
    { key: "wall.pixel_shift_minutes", value: Number.NaN },
  ]);
  assert.equal(settings.refreshMs, 10_000);
  assert.equal(settings.feedLines, 50);
  assert.equal(settings.taskLines, WALL_DEFAULTS.taskLines);
  assert.equal(settings.clock24h, false);
  assert.equal(settings.pixelShiftMs, WALL_DEFAULTS.pixelShiftMs);
});
