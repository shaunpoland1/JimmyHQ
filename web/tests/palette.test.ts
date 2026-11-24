import assert from "node:assert/strict";
import { test } from "node:test";
import { filterCommands, stepIndex } from "../src/lib/palette.ts";

const commands = [
  { id: "go:bench", title: "Go to Bench", group: "Navigate", keywords: ["bench"] },
  { id: "go:tasks", title: "Go to Tasks", group: "Navigate", keywords: ["tasks"] },
  { id: "go:settings", title: "Go to Settings", group: "Navigate", keywords: ["settings"] },
  { id: "theme:dark", title: "Theme: Dark", group: "Theme", keywords: ["appearance", "mode"] },
  { id: "theme:light", title: "Theme: Light", group: "Theme", keywords: ["appearance", "mode"] },
];
const ids = (query: string) => filterCommands(commands, query).map((command) => command.id);

test("an empty query returns everything in order", () => {
  assert.deepEqual(ids(""), commands.map((command) => command.id));
  assert.deepEqual(ids("   "), commands.map((command) => command.id));
});

test("matches are case-insensitive and ranked by title first", () => {
  assert.deepEqual(ids("BENCH"), ["go:bench"]);
  assert.deepEqual(ids("theme"), ["theme:dark", "theme:light"]);
  assert.equal(ids("dark")[0], "theme:dark");
});

test("keywords and groups match too", () => {
  assert.deepEqual(ids("appearance"), ["theme:dark", "theme:light"]);
  assert.deepEqual(ids("navigate"), ["go:bench", "go:tasks", "go:settings"]);
});

test("every token must match", () => {
  assert.deepEqual(ids("go set"), ["go:settings"]);
  assert.deepEqual(ids("go zzz"), []);
});

test("stepIndex wraps in both directions", () => {
  assert.equal(stepIndex(0, -1, 3), 2);
  assert.equal(stepIndex(2, 1, 3), 0);
  assert.equal(stepIndex(1, 1, 3), 2);
  assert.equal(stepIndex(0, 1, 0), 0);
});
