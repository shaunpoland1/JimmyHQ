import assert from "node:assert/strict";
import { test } from "node:test";
import { DAYS_PER_PAGE, dayHeading, entryCount, neighbours, newestFirst, pagesToShow, visibleDays } from "../src/modules/village/lib/diary.ts";

const run = (count: number) => Array.from({ length: count }, (_, i) => `2026-03-${String(i + 1).padStart(2, "0")}`);

test("days are listed newest first and anything that is not a day is dropped", () => {
  assert.deepEqual(newestFirst(["2026-03-01", "2026-03-03", "nonsense", "2026-03-02", "2026-03-03"]), ["2026-03-03", "2026-03-02", "2026-03-01"]);
  assert.deepEqual(newestFirst(undefined), []);
});

test("the day list shows one page at a time and says how many older days are hidden", () => {
  const days = newestFirst(run(20));
  const first = visibleDays(days, 1);
  assert.equal(first.shown.length, DAYS_PER_PAGE);
  assert.equal(first.hidden, 20 - DAYS_PER_PAGE);
  assert.equal(first.shown[0], "2026-03-20");
  const all = visibleDays(days, 2);
  assert.equal(all.shown.length, 20);
  assert.equal(all.hidden, 0);
});

test("a day picked beyond the shown pages brings its page into view", () => {
  const days = newestFirst(run(40));
  assert.equal(pagesToShow(days, days[0] as string, 1), 1);
  assert.equal(pagesToShow(days, days[DAYS_PER_PAGE] as string, 1), 2);
  assert.equal(pagesToShow(days, days[39] as string, 1), 3);
  assert.equal(pagesToShow(days, "2020-01-01", 2), 2);
});

test("the newer and older day are the neighbours in a newest-first list, and null at the ends", () => {
  const days = ["2026-03-03", "2026-03-02", "2026-03-01"];
  assert.deepEqual(neighbours(days, "2026-03-02"), { newer: "2026-03-03", older: "2026-03-01" });
  assert.deepEqual(neighbours(days, "2026-03-03"), { newer: null, older: "2026-03-02" });
  assert.deepEqual(neighbours(days, "2026-03-01"), { newer: "2026-03-02", older: null });
  assert.deepEqual(neighbours(days, "2025-01-01"), { newer: null, older: null });
});

test("today and yesterday are named as such and other days are written out", () => {
  const now = new Date(2026, 5, 15, 12, 0).getTime();
  assert.equal(dayHeading("2026-06-15", now), "Today");
  assert.equal(dayHeading("2026-06-14", now), "Yesterday");
  assert.match(dayHeading("2026-06-01", now), /June/);
  assert.doesNotMatch(dayHeading("2026-06-01", now), /2026/);
  assert.match(dayHeading("2025-06-01", now), /2025/);
  assert.equal(dayHeading("not a day", now), "not a day");
});

test("a day's entries are counted with its dreams called out", () => {
  assert.equal(entryCount([{}]), "1 entry");
  assert.equal(entryCount([{}, {}]), "2 entries");
  assert.equal(entryCount([{}, { dream: true }]), "2 entries, 1 a dream");
  assert.equal(entryCount([{ dream: true }, { dream: true }, {}]), "3 entries, 2 dreams");
});
