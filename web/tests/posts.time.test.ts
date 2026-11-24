// The Posts page: slots shown and typed in the page's own time zone.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { calendar, dayKey, formatSlot, fromInputValue, toInputValue, zoneFor } from "../../apps/posts/web/lib/time.js";

const JAN_15_0800_UTC = Date.UTC(2027, 0, 15, 8, 0);

test("a slot is shown in the page's time zone, not the browser's", () => {
  assert.equal(toInputValue(JAN_15_0800_UTC, "UTC"), "2027-01-15T08:00");
  assert.equal(toInputValue(JAN_15_0800_UTC, "America/New_York"), "2027-01-15T03:00");
  assert.equal(toInputValue(JAN_15_0800_UTC, "Asia/Kolkata"), "2027-01-15T13:30");
  assert.equal(formatSlot(JAN_15_0800_UTC, "Asia/Kolkata", JAN_15_0800_UTC), "Fri 15 Jan, 13:30");
  assert.equal(formatSlot(JAN_15_0800_UTC, "UTC", Date.UTC(2026, 5, 1)), "Fri 15 Jan 2027, 08:00");
});

test("a time typed in the page's zone is booked at that moment", () => {
  for (const zone of ["UTC", "America/New_York", "Asia/Kolkata", "Europe/Paris", "Australia/Lord_Howe"]) {
    const value = toInputValue(JAN_15_0800_UTC, zone);
    assert.equal(fromInputValue(value, zone).ms, JAN_15_0800_UTC, zone);
  }
  // Summer time, on either side of the change.
  const july = Date.UTC(2027, 6, 1, 12, 0);
  assert.equal(fromInputValue(toInputValue(july, "Europe/Paris"), "Europe/Paris").ms, july);
});

test("a time the clocks skip is refused with a sentence, not booked an hour out", () => {
  const got = fromInputValue("2027-03-14T02:30", "America/New_York");
  assert.equal(got.ms, undefined);
  assert.match(got.error, /does not happen on that day in America\/New_York/);
  assert.match(fromInputValue("tomorrow", "UTC").error, /Choose a day and a time/);
});

test("the page's own zone is used when it has one, else the owner's", () => {
  assert.equal(zoneFor({ timezone: "Asia/Kolkata" }, "Europe/Paris"), "Asia/Kolkata");
  assert.equal(zoneFor({ timezone: "" }, "Europe/Paris"), "Europe/Paris");
  assert.equal(zoneFor({ timezone: "Not/AZone" }, "Europe/Paris"), "Europe/Paris");
});

test("the calendar strip puts each post on its day, with later and untimed ones counted", () => {
  const now = Date.UTC(2027, 0, 15, 6, 0);
  const posts = [
    { id: 1, slot_ms: Date.UTC(2027, 0, 15, 8, 0) },
    { id: 2, slot_ms: Date.UTC(2027, 0, 16, 23, 30) }, // the 17th in Paris
    { id: 3, slot_ms: Date.UTC(2027, 1, 20, 8, 0) },
    { id: 4, slot_ms: null },
  ];
  const strip = calendar(posts, { zone: "Europe/Paris", nowMs: now, days: 7 });
  assert.equal(strip.days.length, 7);
  assert.equal(strip.days[0].today, true);
  assert.deepEqual(strip.days[0].posts.map((p: { id: number }) => p.id), [1]);
  assert.deepEqual(strip.days[2].posts.map((p: { id: number }) => p.id), [2]);
  assert.equal(strip.later, 1);
  assert.equal(strip.unset, 1);
});

test("the strip never skips or repeats a day when the clocks change", () => {
  const late = Date.UTC(2027, 2, 13, 23, 50) + 5 * 3_600_000; // 23:50 in New York, before the change
  const strip = calendar([], { zone: "America/New_York", nowMs: late, days: 4 });
  assert.deepEqual(strip.days.map((d: { key: string }) => d.key), ["2027-03-13", "2027-03-14", "2027-03-15", "2027-03-16"]);
  assert.equal(dayKey(late, "America/New_York"), "2027-03-13");
});
