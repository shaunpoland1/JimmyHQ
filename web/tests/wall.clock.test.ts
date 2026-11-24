import assert from "node:assert/strict";
import { test } from "node:test";
import { clockFace, clockWidthEm, formatWait, shortTime, timeOfDay, untilNextTick } from "../src/modules/wall/lib/clock.ts";

// 2030-05-08 14:05:09 UTC, a Wednesday.
const NOW = Date.UTC(2030, 4, 8, 14, 5, 9);

test("a 12-hour clock keeps AM and PM apart from the digits", () => {
  assert.deepEqual(clockFace(NOW, { hour24: false, seconds: false }, "en-US", "UTC"), {
    time: "2:05",
    period: "PM",
    date: "Wednesday, May 8",
  });
  assert.equal(clockFace(NOW, { hour24: false, seconds: true }, "en-US", "UTC").time, "2:05:09");
});

test("a 24-hour clock has no AM or PM and counts midnight as 00", () => {
  assert.deepEqual(clockFace(NOW, { hour24: true, seconds: false }, "en-US", "UTC"), {
    time: "14:05",
    period: "",
    date: "Wednesday, May 8",
  });
  const midnight = Date.UTC(2030, 4, 8, 0, 3, 0);
  assert.equal(clockFace(midnight, { hour24: true, seconds: true }, "en-US", "UTC").time, "00:03:00");
  assert.equal(clockFace(midnight, { hour24: false, seconds: false }, "en-US", "UTC").time, "12:03");
});

test("the date is written the way the locale writes it", () => {
  assert.equal(clockFace(NOW, { hour24: true, seconds: false }, "en-GB", "UTC").date, "Wednesday 8 May");
  assert.equal(clockFace(NOW, { hour24: true, seconds: false }, "de-DE", "UTC").date, "Mittwoch, 8. Mai");
});

test("the clock follows the time zone it is given", () => {
  assert.equal(clockFace(NOW, { hour24: true, seconds: false }, "en-US", "Asia/Tokyo").time, "23:05");
});

test("a feed line's time is short", () => {
  assert.equal(shortTime(NOW, true, "en-US", "UTC"), "14:05");
  assert.match(shortTime(NOW, false, "en-US", "UTC"), /^2:05\sPM$/);
});

test("a task's time follows the Wall's 12 or 24-hour setting, not the locale's habit", () => {
  assert.equal(timeOfDay("14:05", true, "en-US"), "14:05");
  assert.match(timeOfDay("14:05", false, "en-US"), /^2:05\sPM$/);
  assert.match(timeOfDay("14:05", false, "de-DE"), /^2:05\s/);
  assert.equal(timeOfDay("00:30", true, "en-US"), "00:30");
  assert.match(timeOfDay("00:30", false, "en-US"), /^12:30\sAM$/);
});

test("a task time that is not a time is shown as it came", () => {
  assert.equal(timeOfDay("soon", true, "en-US"), "soon");
  assert.equal(timeOfDay("25:00", false, "en-US"), "25:00");
  assert.equal(timeOfDay("", true, "en-US"), "");
});

test("the next tick lands on the next whole second or minute", () => {
  assert.equal(untilNextTick(NOW + 250, true), 750);
  assert.equal(untilNextTick(NOW, true), 1000);
  assert.equal(untilNextTick(NOW, false), 51_000);
  assert.equal((NOW + untilNextTick(NOW, false)) % 60_000, 0);
});

test("the clock's width grows with what it shows", () => {
  const short = clockWidthEm({ time: "2:05", period: "" });
  const withPeriod = clockWidthEm({ time: "2:05", period: "PM" });
  const withSeconds = clockWidthEm({ time: "12:05:09", period: "PM" });
  assert.equal(short, 2.4);
  assert.ok(withPeriod > short);
  assert.ok(withSeconds > withPeriod);
});

test("a wait is said in the largest unit that fits", () => {
  const MIN = 60_000;
  const HOUR = 60 * MIN;
  assert.equal(formatWait(-5), "under a minute");
  assert.equal(formatWait(59_000), "under a minute");
  assert.equal(formatWait(14 * MIN), "14 min");
  assert.equal(formatWait(6 * HOUR + 59 * MIN), "6 h");
  assert.equal(formatWait(47 * HOUR), "47 h");
  assert.equal(formatWait(72 * HOUR), "3 days");
});
