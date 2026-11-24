import assert from "node:assert/strict";
import { test } from "node:test";
import type { DescribedRecord } from "../src/modules/apps/described/api.ts";
import { bucketByDay, dayOfMonth, dayOfTimestamp, gridRange, inMonth, monthGrid, monthOf, monthTitle, shiftMonth, weekStartIndex, weekdayNames } from "../src/modules/apps/described/lib/calendar.ts";

function rec(id: number, due: string | null): DescribedRecord {
  return { id, label: `R${id}`, created: 0, updated: 0, values: { due } };
}

test("the week starts on the day the owner's setting names, and Monday when it names none", () => {
  assert.equal(weekStartIndex("monday"), 1);
  assert.equal(weekStartIndex("sunday"), 0);
  assert.equal(weekStartIndex("saturday"), 6);
  assert.equal(weekStartIndex(""), 1);
  assert.equal(weekStartIndex(undefined), 1);
});

test("a month's grid is whole weeks starting on the week's first day", () => {
  // January 2030 begins on a Tuesday.
  const monday = monthGrid({ year: 2030, month: 1 }, 1);
  assert.equal(monday[0]?.[0], "2029-12-31");
  assert.equal(monday[0]?.[1], "2030-01-01");
  assert.equal(monday.at(-1)?.[6], "2030-02-03");
  assert.ok(monday.every((w) => w.length === 7));
  const sunday = monthGrid({ year: 2030, month: 1 }, 0);
  assert.equal(sunday[0]?.[0], "2029-12-30");
  assert.equal(sunday[0]?.[2], "2030-01-01");
  const saturday = monthGrid({ year: 2030, month: 1 }, 6);
  assert.equal(saturday[0]?.[0], "2029-12-29");
});

test("a month that starts on the week's first day has no leading days from the month before", () => {
  // July 2030 begins on a Monday.
  assert.equal(monthGrid({ year: 2030, month: 7 }, 1)[0]?.[0], "2030-07-01");
  assert.equal(monthGrid({ year: 2030, month: 7 }, 1).length, 5);
});

test("February in a leap year and in an ordinary one have the right days", () => {
  const flat = (y: number) => monthGrid({ year: y, month: 2 }, 1).flat().filter((d) => inMonth(d, { year: y, month: 2 }));
  assert.equal(flat(2028).length, 29);
  assert.equal(flat(2030).length, 28);
});

test("the calendar asks the server for exactly the days its grid shows", () => {
  assert.deepEqual(gridRange({ year: 2030, month: 1 }, 1), { from: "2029-12-31", to: "2030-02-03" });
  assert.deepEqual(gridRange({ year: 2030, month: 1 }, 0), { from: "2029-12-30", to: "2030-02-02" });
});

test("months step forwards and backwards over a year's edge", () => {
  assert.deepEqual(shiftMonth({ year: 2030, month: 12 }, 1), { year: 2031, month: 1 });
  assert.deepEqual(shiftMonth({ year: 2030, month: 1 }, -1), { year: 2029, month: 12 });
  assert.deepEqual(shiftMonth({ year: 2030, month: 3 }, -15), { year: 2028, month: 12 });
  assert.deepEqual(monthOf("2030-11-18"), { year: 2030, month: 11 });
});

test("records are bucketed on the day their date field holds, and those with none are set apart", () => {
  const { days, undated } = bucketByDay([rec(1, "2030-01-05"), rec(2, "2030-01-05"), rec(3, "2030-01-06"), rec(4, null), rec(5, "garbage")], "due");
  assert.deepEqual(days.get("2030-01-05")?.map((r) => r.id), [1, 2]);
  assert.deepEqual(days.get("2030-01-06")?.map((r) => r.id), [3]);
  assert.deepEqual(undated.map((r) => r.id), [4, 5]);
});

test("which day it is depends on the owner's time zone, set at the display edge", () => {
  const instant = Date.UTC(2030, 0, 1, 23, 30); // 23:30 UTC on New Year's Day
  assert.equal(dayOfTimestamp(instant, "UTC"), "2030-01-01");
  assert.equal(dayOfTimestamp(instant, "Asia/Tokyo"), "2030-01-02");
  assert.equal(dayOfTimestamp(instant, "America/Los_Angeles"), "2030-01-01");
  assert.equal(dayOfTimestamp(instant, "Pacific/Auckland"), "2030-01-02");
  assert.equal(dayOfTimestamp(instant, "Not/AZone"), "2030-01-01");
});

test("weekday headings start on the owner's first day, in the locale's words", () => {
  assert.deepEqual(weekdayNames(1, "en-US"), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
  assert.deepEqual(weekdayNames(0, "en-US"), ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]);
  assert.deepEqual(weekdayNames(6, "en-US")[0], "Sat");
  assert.equal(weekdayNames(1, "fr-FR")[0], "lun.");
});

test("a month is titled in the locale, and a day cell knows its number and whether it is in the month", () => {
  assert.equal(monthTitle({ year: 2030, month: 11 }, "en-US"), "November 2030");
  assert.equal(dayOfMonth("2030-11-08"), 8);
  assert.equal(inMonth("2030-11-30", { year: 2030, month: 11 }), true);
  assert.equal(inMonth("2030-12-01", { year: 2030, month: 11 }), false);
});
