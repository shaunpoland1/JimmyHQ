import assert from "node:assert/strict";
import { test } from "node:test";
import type { PlanEntry, Setting } from "../src/api.ts";
import { changeLine, changedMessage, parseSettingsText, settingsChangedSince } from "../src/lib/settingsFile.ts";

const theme = {
  key: "appearance.theme",
  section: "appearance",
  type: "choice",
  label: "Theme",
  help: "",
  default: "system",
  value: "system",
  choices: [
    { value: "system", label: "Match my system" },
    { value: "dark", label: "Dark" },
  ],
} satisfies Setting;

function entry(overrides: Partial<PlanEntry>): PlanEntry {
  return {
    key: "appearance.theme",
    status: "change",
    label: "Theme",
    old: "system",
    new: "dark",
    reset: false,
    portable: true,
    reason: null,
    ...overrides,
  };
}

test("a pasted settings file is read, and one that is not says what is wrong", () => {
  const good = parseSettingsText('{"jimmyhq_settings": 1, "settings": {"appearance.theme": "dark"}}');
  assert.equal(good.ok, true);
  assert.deepEqual(parseSettingsText("  ").ok, false);
  const broken = parseSettingsText('{"jimmyhq_settings": 1,');
  assert.equal(broken.ok === false && broken.error.includes("not valid JSON"), true);
  const other = parseSettingsText('{"name": "x"}');
  assert.equal(other.ok === false && other.error.includes("not a JimmyHQ settings file"), true);
  assert.equal(parseSettingsText("[1, 2]").ok, false);
});

test("a file with a byte order mark is still read", () => {
  assert.equal(parseSettingsText('﻿{"jimmyhq_settings": 1, "settings": {}}').ok, true);
});

test("a change is worded with the setting's own labels, not its stored values", () => {
  assert.deepEqual(changeLine(entry({}), [theme]), {
    key: "appearance.theme",
    label: "Theme",
    from: "Match my system",
    to: "Dark",
  });
});

test("a change back to the default says so", () => {
  const line = changeLine(entry({ old: "dark", new: "system", reset: true }), [theme]);
  assert.equal(line.to, "Match my system (default)");
});

test("the count of changes is worded for one, many and none", () => {
  assert.equal(changedMessage(0), "No settings changed.");
  assert.equal(changedMessage(1), "1 setting changed.");
  assert.equal(changedMessage(12), "12 settings changed.");
});

test("only a settings event newer than the last one seen asks for a refresh", () => {
  const events = [
    { seq: 12, kind: "task.created" },
    { seq: 11, kind: "setting.updated" },
    { seq: 9, kind: "setting.reset" },
  ];
  assert.equal(settingsChangedSince(events, 10), true);
  assert.equal(settingsChangedSince(events, 11), false);
  assert.equal(settingsChangedSince([], 0), false);
});
