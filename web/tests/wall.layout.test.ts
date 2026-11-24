import assert from "node:assert/strict";
import { test } from "node:test";
import { SHIFT_REACH, arrange, fitLines, pixelShift } from "../src/modules/wall/lib/layout.ts";

test("a wide, short panel is one row of tiles", () => {
  assert.deepEqual(arrange(6, 1280, 400), { rows: [6], cols: 6 });
  assert.deepEqual(arrange(6, 3840, 1080), { rows: [6], cols: 6 });
  assert.deepEqual(arrange(6, 1920, 480), { rows: [6], cols: 6 });
});

test("a taller screen is two rows", () => {
  assert.deepEqual(arrange(6, 1024, 600), { rows: [3, 3], cols: 3 });
  assert.deepEqual(arrange(6, 800, 400), { rows: [3, 3], cols: 3 });
  assert.deepEqual(arrange(6, 1920, 1080), { rows: [3, 3], cols: 3 });
});

test("an upright screen stacks the tiles", () => {
  assert.deepEqual(arrange(6, 380, 800), { rows: [2, 2, 2], cols: 2 });
  assert.deepEqual(arrange(3, 400, 1200), { rows: [1, 1, 1], cols: 1 });
});

test("an uneven number of tiles leaves the short row last", () => {
  assert.deepEqual(arrange(5, 1024, 600), { rows: [3, 2], cols: 3 });
  assert.deepEqual(arrange(1, 1280, 400), { rows: [1], cols: 1 });
});

test("every tile is placed exactly once, whatever the screen", () => {
  for (let count = 0; count <= 6; count += 1) {
    for (const [width, height] of [[800, 400], [1280, 400], [1024, 600], [3840, 1080], [380, 700], [0, 0]] as const) {
      const layout = arrange(count, width, height);
      assert.equal(layout.rows.reduce((sum, row) => sum + row, 0), count);
      assert.ok(layout.rows.every((row) => row >= 1 && row <= layout.cols));
    }
  }
  assert.deepEqual(arrange(0, 1280, 400), { rows: [], cols: 0 });
});

test("a list that fits is shown whole", () => {
  assert.deepEqual(fitLines(4, 8, 6), { shown: 4, more: 0 });
  assert.deepEqual(fitLines(6, 8, 6), { shown: 6, more: 0 });
  assert.deepEqual(fitLines(0, 8, 6), { shown: 0, more: 0 });
});

test("a list too long for the room keeps one line to say how many more", () => {
  assert.deepEqual(fitLines(10, 20, 6), { shown: 5, more: 5 });
  assert.deepEqual(fitLines(7, 8, 6), { shown: 5, more: 2 });
});

test("the cap from settings wins over the room", () => {
  assert.deepEqual(fitLines(12, 8, 30), { shown: 8, more: 4 });
  assert.deepEqual(fitLines(12, 8, 9), { shown: 8, more: 4 });
  assert.deepEqual(fitLines(12, 8, 8), { shown: 7, more: 5 });
});

test("with no room at all nothing is drawn but the count is still right", () => {
  assert.deepEqual(fitLines(5, 8, 0), { shown: 0, more: 5 });
  assert.deepEqual(fitLines(5, 8, 1), { shown: 0, more: 5 });
});

test("before the tile is measured the cap alone decides", () => {
  assert.deepEqual(fitLines(12, 8, null), { shown: 8, more: 4 });
  assert.deepEqual(fitLines(3, 8, null), { shown: 3, more: 0 });
});

test("a list with no end shows what fits and counts nothing", () => {
  assert.deepEqual(fitLines(50, 8, 5, false), { shown: 5, more: 0 });
  assert.deepEqual(fitLines(50, 8, 20, false), { shown: 8, more: 0 });
});

test("the picture stays home unless the shift is on", () => {
  assert.deepEqual(pixelShift(1_000_000_000, 300_000, false), { x: 0, y: 0 });
  assert.deepEqual(pixelShift(1_000_000_000, 0, true), { x: 0, y: 0 });
});

test("the shift moves a little each period, never far, and comes back home", () => {
  const every = 300_000;
  const seen = new Set<string>();
  let last = pixelShift(0, every, true);
  assert.deepEqual(last, { x: 0, y: 0 });
  for (let step = 1; step <= 20; step += 1) {
    const at = pixelShift(step * every, every, true);
    assert.ok(Math.abs(at.x) <= SHIFT_REACH && Math.abs(at.y) <= SHIFT_REACH);
    assert.ok(Math.abs(at.x - last.x) <= 3 && Math.abs(at.y - last.y) <= 3);
    assert.deepEqual(pixelShift(step * every + every - 1, every, true), at);
    seen.add(`${at.x},${at.y}`);
    last = at;
  }
  assert.ok(seen.size >= 8);
  assert.ok(seen.has("0,0"));
});
