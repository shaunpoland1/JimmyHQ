// The Posts page (apps/posts/web): which source sentence each number and quotation
// in a draft rests on. Keys are pinned to the Python checks by a shared fixture.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { checkSummary, citedSentences, marks, raised, segments } from "../../apps/posts/web/lib/evidence.js";

type Mark = { text: string; kind: string; key: string; sentences: number[] };
const fixture = JSON.parse(readFileSync(new URL("../../apps/posts/tests/fixtures/evidence.json", import.meta.url), "utf8")) as {
  body: string; sentences: string[]; marks: Mark[];
};

/** The check results the Python checks keep for the fixture (see test_posts_screen.py). */
function resultsFor(list: Mark[]) {
  return list.map((m, i) => ({
    id: i + 1, name: m.kind === "number" ? "numbers" : "quotes", key: m.key,
    level: m.sentences.length ? "ok" : "ask", evidence: m.sentences, answer: null, detail: `About ${m.text}`,
  }));
}

test("the page finds the same numbers and quotations, under the same keys, as the checks", () => {
  const found = marks(fixture.body).map((m: { start: number; end: number; kind: string; key: string }) => ({
    text: fixture.body.slice(m.start, m.end), kind: m.kind, key: m.key,
  }));
  assert.deepEqual(found, fixture.marks.map(({ text, kind, key }) => ({ text, kind, key })));
});

test("a number pressed in the draft names the source sentence it was found in", () => {
  const segs = segments(fixture.body, resultsFor(fixture.marks));
  const homes = segs.find((s: { text: string }) => s.text === "1,240");
  assert.deepEqual(homes.sentences, [1]);
  assert.equal(homes.standing, "found");
  const quote = segs.find((s: { kind: string }) => s.kind === "quote");
  assert.deepEqual(quote.sentences, [3]);
});

test("a number the checks could not find is marked, with its question to answer", () => {
  const segs = segments(fixture.body, resultsFor(fixture.marks));
  const made = segs.find((s: { text: string }) => s.text === "2.5 million");
  assert.equal(made.standing, "not_found");
  assert.deepEqual(made.sentences, []);
  assert.equal(made.check.level, "ask");
  const answered = segments(fixture.body, resultsFor(fixture.marks).map((r) => (r.key === "2.5 million" ? { ...r, answer: "yes" } : r)));
  assert.equal(answered.find((s: { text: string }) => s.text === "2.5 million").standing, "answered");
});

test("a number with no result on this version says it was not checked rather than passing", () => {
  const segs = segments("We planted 30 trees.", []);
  assert.equal(segs.find((s: { kind: string }) => s.kind === "number").standing, "unchecked");
});

test("the runs put back together are the draft exactly, hostile text included", () => {
  const hostile = '<img src=x onerror="alert(1)"> 38% of <script>x()</script> & "a b c" end';
  const segs = segments(hostile, []);
  assert.equal(segs.map((s: { text: string }) => s.text).join(""), hostile);
  assert.equal(segments(fixture.body, resultsFor(fixture.marks)).map((s: { text: string }) => s.text).join(""), fixture.body);
});

test("a number inside a quotation belongs to the quotation, not to itself", () => {
  const found = marks("She said “we sold 40 boxes today” at noon.");
  assert.deepEqual(found.map((m: { kind: string }) => m.kind), ["quote"]);
});

test("a word or two in quotation marks is not taken for a quotation", () => {
  assert.deepEqual(marks('a so-called "smart" roof'), []);
});

test("the summary and the order of the raised checks match the card's", () => {
  const checks = [
    { id: 3, level: "warn", answer: null }, { id: 1, level: "ok", answer: null },
    { id: 2, level: "ask", answer: null }, { id: 4, level: "ask", answer: "yes" },
  ];
  assert.equal(checkSummary(checks), "1 question open, 1 warning.");
  assert.equal(checkSummary([]), "Every check passed.");
  assert.deepEqual(raised(checks).map((c: { id: number }) => c.id), [2, 4, 3]);
});

test("the sentences a draft rests on are lit faintly before anything is pressed", () => {
  const cited = citedSentences([{ text: "x", sentences: [2, 9] }], [{ level: "ok", evidence: [1, "notes"] }, { level: "ask", evidence: [] }]);
  assert.deepEqual([...cited].sort(), [1, 2, 9]);
});
