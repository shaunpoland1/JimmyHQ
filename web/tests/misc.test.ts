import assert from "node:assert/strict";
import { test } from "node:test";
import { MIN_PASSWORD_LENGTH, hasErrors, validateSetup } from "../src/lib/auth.ts";
import { DOCK_DEFAULT_WIDTH, DOCK_MAX_WIDTH, DOCK_MIN_WIDTH, clampDockWidth, parseDockTab } from "../src/lib/dock.ts";

test("validateSetup enforces name, length and confirmation", () => {
  const good = "x".repeat(MIN_PASSWORD_LENGTH);
  assert.ok(!hasErrors(validateSetup({ name: "Sample Owner", password: good, confirm: good })));
  assert.ok(validateSetup({ name: "  ", password: good, confirm: good }).name);
  assert.ok(validateSetup({ name: "Sample Owner", password: "short", confirm: "short" }).password);
  assert.ok(validateSetup({ name: "Sample Owner", password: good, confirm: `${good}!` }).confirm);
});

test("clampDockWidth keeps the dock within limits", () => {
  assert.equal(clampDockWidth(10), DOCK_MIN_WIDTH);
  assert.equal(clampDockWidth(5000), DOCK_MAX_WIDTH);
  assert.equal(clampDockWidth(400.4), 400);
  assert.equal(clampDockWidth(Number.NaN), DOCK_DEFAULT_WIDTH);
  assert.equal(clampDockWidth(600, 1000), 500);
  assert.equal(clampDockWidth(600, 300), DOCK_MIN_WIDTH);
});

test("parseDockTab accepts only known tabs", () => {
  assert.equal(parseDockTab("tape"), "tape");
  assert.equal(parseDockTab("nope"), undefined);
});
