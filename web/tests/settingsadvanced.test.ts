import assert from "node:assert/strict";
import { test } from "node:test";
import { ADVANCED_STORAGE_KEY, advancedToggleLabel, readAdvancedOpen, splitAdvanced, toggleAdvancedOpen, writeAdvancedOpen } from "../src/lib/settingsAdvanced.ts";

const rows = [
  { key: "a.plain", value: 1, default: 1 },
  { key: "a.tuned", value: 5, default: 5, advanced: true },
  { key: "a.altered", value: 9, default: 5, advanced: true },
  { key: "a.other", value: "x", default: "x", advanced: true },
];
const keys = (list: { key: string }[]) => list.map((row) => row.key);

test("advanced settings stay out of sight until asked for, except one that was changed", () => {
  const split = splitAdvanced(rows, false);
  assert.deepEqual(keys(split.shown), ["a.plain", "a.altered"]);
  assert.deepEqual([split.advanced, split.hidden], [3, 2]);
  assert.equal(advancedToggleLabel(split, false), "Show advanced settings (2)");
});

test("opening them shows every row in its usual order", () => {
  const split = splitAdvanced(rows, true);
  assert.deepEqual(keys(split.shown), ["a.plain", "a.tuned", "a.altered", "a.other"]);
  assert.equal(split.hidden, 0);
  assert.equal(advancedToggleLabel(split, true), "Hide advanced settings (3)");
});

test("a link to an advanced setting shows that one and leaves the rest closed", () => {
  const split = splitAdvanced(rows, false, "a.other");
  assert.deepEqual(keys(split.shown), ["a.plain", "a.altered", "a.other"]);
  assert.equal(advancedToggleLabel(split, false), "Show advanced settings (1)");
});

test("a block with nothing advanced, or with every advanced setting changed, offers no control", () => {
  assert.equal(advancedToggleLabel(splitAdvanced([rows[0]!], false), false), null);
  assert.equal(advancedToggleLabel(splitAdvanced([rows[0]!, rows[2]!], false), false), null);
  assert.deepEqual(splitAdvanced([], false), { shown: [], advanced: 0, hidden: 0 });
});

function memoryStore(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => void data.set(key, value), data };
}

test("which blocks are open is remembered in the browser", () => {
  const store = memoryStore();
  assert.deepEqual(readAdvancedOpen(store), []);
  const open = toggleAdvancedOpen(toggleAdvancedOpen([], "memory_search", true), "chat", true);
  writeAdvancedOpen(store, open);
  assert.equal(store.data.get(ADVANCED_STORAGE_KEY), '["chat","memory_search"]');
  assert.deepEqual(readAdvancedOpen(store), ["chat", "memory_search"]);
  assert.deepEqual(toggleAdvancedOpen(open, "chat", false), ["memory_search"]);
  assert.deepEqual(toggleAdvancedOpen(open, "chat", true), ["memory_search", "chat"]);
});

test("storage that is missing, blocked or holds nonsense never breaks the page", () => {
  const broken = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("full");
    },
  };
  assert.deepEqual(readAdvancedOpen(null), []);
  assert.deepEqual(readAdvancedOpen(broken), []);
  assert.doesNotThrow(() => writeAdvancedOpen(broken, ["chat"]));
  assert.doesNotThrow(() => writeAdvancedOpen(null, ["chat"]));
  assert.deepEqual(readAdvancedOpen(memoryStore({ [ADVANCED_STORAGE_KEY]: "{oops" })), []);
  assert.deepEqual(readAdvancedOpen(memoryStore({ [ADVANCED_STORAGE_KEY]: '{"a":1}' })), []);
  assert.deepEqual(readAdvancedOpen(memoryStore({ [ADVANCED_STORAGE_KEY]: '["chat",3,"chat"]' })), ["chat"]);
});
