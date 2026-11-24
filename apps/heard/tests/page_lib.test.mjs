// The page's own logic, run by node (no browser): words, grouping and batching.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const lib = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "web", "lib");
const f = await import(pathToFileURL(path.join(lib, "format.js")).href);
const p = await import(pathToFileURL(path.join(lib, "poll.js")).href);

test("a day is called Today, Yesterday or by its long date", () => {
  assert.equal(f.dayHeading("2027-01-15", "2027-01-15"), "Today");
  assert.equal(f.dayHeading("2027-01-14", "2027-01-15"), "Yesterday");
  assert.match(f.dayHeading("2027-01-12", "2027-01-15"), /Tuesday.*12 January 2027/);
  assert.equal(f.dayHeading("nonsense", "2027-01-15"), "nonsense");
});

test("days add and subtract across a month end", () => {
  assert.equal(f.addDays("2027-03-01", -1), "2027-02-28");
  assert.equal(f.addDays("2027-12-31", 1), "2028-01-01");
});

test("the clock is read in the owners time zone", () => {
  const ms = Date.UTC(2027, 0, 15, 8, 5);
  assert.equal(f.clock(ms, "UTC"), "08:05");
  assert.equal(f.clock(ms, "America/New_York"), "03:05");
  assert.equal(f.clock(NaN, "UTC"), "");
});

test("a length is said in plain words", () => {
  assert.equal(f.lengthWords(8), "under a minute");
  assert.equal(f.lengthWords(60), "1 min");
  assert.equal(f.lengthWords(3000), "50 min");
  assert.equal(f.lengthWords(3900), "1 h 5 min");
  assert.equal(f.lengthWords(7200), "2 h");
});

test("who spoke is a short sentence", () => {
  assert.equal(f.whoWords(["You"], 1), "You");
  assert.equal(f.whoWords(["You", "Sam"], 2), "You and Sam");
  assert.equal(f.whoWords(["You", "Sam", "Alex", "Robin"], 6), "You, Sam, Alex, Robin and 2 more");
  assert.equal(f.whoWords([], 0), "");
});

test("conversations group by day in order, and a later page joins a shared day", () => {
  const a = [{ id: 1, day: "2027-01-15" }, { id: 2, day: "2027-01-15" }, { id: 3, day: "2027-01-14" }];
  const days = f.groupByDay(a);
  assert.deepEqual(days.map((d) => [d.day, d.items.length]), [["2027-01-15", 2], ["2027-01-14", 1]]);
  const more = f.addPage(days, [{ id: 4, day: "2027-01-14" }, { id: 5, day: "2027-01-13" }]);
  assert.deepEqual(more.map((d) => [d.day, d.items.map((i) => i.id)]), [["2027-01-15", [1, 2]], ["2027-01-14", [3, 4]], ["2027-01-13", [5]]]);
  assert.equal(days[1].items.length, 1, "the days already shown are not changed");
});

test("an import is divided into calls under the byte and count limits", () => {
  const item = (n) => ({ key: `k${n}`, text: "x".repeat(1000) });
  const out = f.batches(Array.from({ length: 10 }, (_, n) => item(n)), 3500, 100);
  assert.ok(out.length >= 3);
  for (const group of out) assert.ok(f.jsonBytes(group) <= 3500 + 2);
  assert.equal(out.flat().length, 10);
  assert.deepEqual(f.batches([], 100, 5), []);
  assert.equal(f.batches([item(1), item(2), item(3)], 1e9, 2).length, 2);
  assert.equal(f.batches([{ big: "y".repeat(9000) }], 100, 5).length, 1, "a piece too big alone still goes");
});

test("bytes are counted, not characters, for text outside plain ASCII", () => {
  assert.equal(f.jsonBytes("é"), 4);
});

test("an export file is checked before anything is sent", () => {
  assert.match(f.readExport("{oops").error, /not a Heard export/);
  assert.match(f.readExport(JSON.stringify({ app: "other", conversations: [] })).error, /not a Heard export/);
  assert.match(f.readExport(JSON.stringify({ app: "heard", format: 2, conversations: [] })).error, /different version/);
  assert.deepEqual(f.readExport(JSON.stringify({ app: "heard", format: 1, conversations: [{ key: "a" }] })).conversations, [{ key: "a" }]);
  assert.ok(f.readExport("null").error);
});

test("search snippets become runs, and only a real true marks a hit", () => {
  assert.deepEqual(f.runs([["a ", false], ["b", true], ["c", "yes"]]), [{ text: "a ", hit: false }, { text: "b", hit: true }, { text: "c", hit: false }]);
  assert.deepEqual(f.runs(undefined), []);
});

test("the wearer is one colour and each other speaker takes the next", () => {
  const list = [{ name: "You", is_user: 1 }, { name: "Sam", is_user: 0 }, { name: "Alex", is_user: 0 }];
  assert.equal(f.speakerClass("You", list), "who-you");
  assert.equal(f.speakerClass("Sam", list), "who-2");
  assert.equal(f.speakerClass("Alex", list), "who-3");
  assert.equal(f.speakerClass("Unknown", list), "who-1");
});

test("sizes and plurals read plainly", () => {
  assert.equal(f.plural(1, "line", "lines"), "1 line");
  assert.equal(f.plural(2, "line", "lines"), "2 lines");
  assert.equal(f.sizeWords(900), "900 bytes");
  assert.equal(f.sizeWords(2048), "2 KB");
});

test("asking again stops at the first answer that is not running, and when the owner leaves", async () => {
  const answers = [{ state: "running" }, { state: "running" }, { state: "done", said: "ok" }];
  let asked = 0;
  const got = await p.untilAnswered({ check: async () => answers[asked++], sleep: async () => {} });
  assert.deepEqual(got, { state: "done", said: "ok" });
  assert.equal(asked, 3);
  let calls = 0;
  const left = await p.untilAnswered({ check: async () => ({ state: "running" }), stillWanted: () => calls++ < 2, sleep: async () => {} });
  assert.equal(left, null);
});

test("an import says whether one item or several were not understood, in the right number", () => {
  assert.match(f.importWords(1, 3, 0), /^Imported 1 new conversation and 3 lines\. Anything already kept/);
  assert.match(f.importWords(0, 0, 1), /1 item was not understood and was left out\./);
  assert.match(f.importWords(0, 0, 2), /2 items were not understood and were left out\./);
});
