import assert from "node:assert/strict";
import { test } from "node:test";
import { COUNT_AFTER_S, SLOW_AFTER_S, SLOW_HINT, formatWait, waitNote, waitedSeconds } from "../src/modules/chat/lib/wait.ts";

test("a short wait is just Thinking and says nothing more", () => {
  assert.deepEqual(waitNote(0), { label: "Thinking…", hint: null });
  assert.deepEqual(waitNote(COUNT_AFTER_S - 1), { label: "Thinking…", hint: null });
});

test("a wait of a few seconds is counted aloud", () => {
  assert.equal(waitNote(42).label, "Thinking… 42 s");
  assert.equal(waitNote(COUNT_AFTER_S).label, `Thinking… ${COUNT_AFTER_S} s`);
});

test("the calm second line appears at thirty seconds and not before", () => {
  assert.equal(waitNote(SLOW_AFTER_S - 1).hint, null);
  assert.equal(waitNote(SLOW_AFTER_S).hint, SLOW_HINT);
  assert.match(SLOW_HINT, /still working/);
  assert.match(SLOW_HINT, /reason/);
  assert.match(SLOW_HINT, /stop it/);
  assert.match(SLOW_HINT, /lower .effort. for this conversation/);
});

test("a reply that has been looking things up is not told to be patient", () => {
  assert.equal(waitNote(120, { hasSteps: true }).hint, null);
  assert.equal(waitNote(120, { hasSteps: true }).label, "Thinking… 2 m 00 s");
});

test("a reply being stopped says so, and nothing else", () => {
  assert.deepEqual(waitNote(120, { stopping: true }), { label: "Stopping…", hint: null });
});

test("minutes are written with padded seconds", () => {
  assert.equal(formatWait(59), "59 s");
  assert.equal(formatWait(60), "1 m 00 s");
  assert.equal(formatWait(245), "4 m 05 s");
  assert.equal(formatWait(-5), "0 s");
});

test("seconds waited are whole, and never negative if the clock moved back", () => {
  assert.equal(waitedSeconds(1000, 43_999), 42);
  assert.equal(waitedSeconds(5000, 1000), 0);
  assert.equal(waitedSeconds(Number.NaN, 1000), 0);
});
