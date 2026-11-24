import assert from "node:assert/strict";
import { test } from "node:test";
import { formatBytes, formatCount, formatEventTime, formatTapeUsage, joinUsage } from "../src/lib/format.ts";

test("formatCount groups thousands", () => {
  assert.equal(formatCount(1234, "en-US"), "1,234");
  assert.equal(formatCount(0, "en-US"), "0");
});

test("formatBytes picks a sensible unit", () => {
  assert.equal(formatBytes(0, "en-US"), "0 B");
  assert.equal(formatBytes(-5, "en-US"), "0 B");
  assert.equal(formatBytes(512, "en-US"), "512 B");
  assert.equal(formatBytes(2048, "en-US"), "2 KB");
  assert.equal(formatBytes(0.4 * 1024 * 1024, "en-US"), "0.4 MB");
  assert.equal(formatBytes(12.34 * 1024 * 1024, "en-US"), "12.3 MB");
  assert.equal(formatBytes(3 * 1024 ** 3, "en-US"), "3.0 GB");
});

test("formatTapeUsage builds the top strip line", () => {
  assert.equal(formatTapeUsage({ events: 1234, bytes: 419430 }, "en-US"), "TAPE 1,234 events · 0.4 MB");
  assert.equal(formatTapeUsage({ events: 1, bytes: 0 }, "en-US"), "TAPE 1 event · 0 B");
});

test("joinUsage drops segments without data", () => {
  assert.equal(joinUsage(["TAPE 1 event · 0 B", null, undefined, ""]), "TAPE 1 event · 0 B");
  assert.equal(joinUsage([null, null]), "");
  assert.equal(joinUsage(["a", "b"]), "a  |  b");
});

test("formatEventTime shows the time today and the date for older events", () => {
  const now = Date.UTC(2030, 0, 15, 12, 0, 0);
  const today = formatEventTime(Date.UTC(2030, 0, 15, 9, 5, 7), now, "en-GB", "UTC");
  assert.equal(today, "09:05:07");
  const older = formatEventTime(Date.UTC(2030, 0, 10, 9, 5, 7), now, "en-GB", "UTC");
  assert.match(older, /10 Jan/);
  assert.match(older, /09:05/);
});
