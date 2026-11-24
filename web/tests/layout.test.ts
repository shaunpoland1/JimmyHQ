import assert from "node:assert/strict";
import { test } from "node:test";
import { pickOrdered, settingFlag } from "../src/lib/layout.ts";

const items = [{ id: "bench" }, { id: "tasks" }, { id: "chat" }];
const ids = (list: { id: string }[]) => list.map((item) => item.id);

test("items follow the order in the setting", () => {
  assert.deepEqual(ids(pickOrdered(items, ["chat", "bench", "tasks"])), ["chat", "bench", "tasks"]);
});

test("an item left out of the setting is hidden", () => {
  assert.deepEqual(ids(pickOrdered(items, ["tasks"])), ["tasks"]);
});

test("unknown ids, repeats, stray case and spaces are tolerated", () => {
  assert.deepEqual(ids(pickOrdered(items, ["Chat ", "nope", "chat", 7, "bench"])), ["chat", "bench"]);
});

test("everything shows in its built-in order while settings are loading", () => {
  assert.deepEqual(ids(pickOrdered(items, undefined)), ["bench", "tasks", "chat"]);
});

test("a flag uses its fallback until a real boolean arrives", () => {
  assert.equal(settingFlag(undefined, true), true);
  assert.equal(settingFlag(false, true), false);
  assert.equal(settingFlag("false", true), true);
});
