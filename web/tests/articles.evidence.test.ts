// The Articles page: which sentence of which source each claim rests on. The evidence is
// the program's own (apps/articles/tests/fixtures/evidence.json, pinned by
// test_articles_screen.py), so the page lights exactly what the checks found.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  byId, checkSummary, citedEverywhere, describeLight, lightFor, parseEvidence, raised, segments, sentenceChecks,
  sentenceStanding, standing, target,
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
} from "../../apps/articles/web/lib/evidence.js";

type Check = { id: number; name: string; level: string; detail: string; evidence: string[]; answer: string | null };
type Sentence = { i: number; text: string; cites: number[][]; marks: { start: number; end: number; kind: string; check: number | null }[]; checks: number[] };
const fixture = JSON.parse(readFileSync(new URL("../../apps/articles/tests/fixtures/evidence.json", import.meta.url), "utf8")) as {
  blocks: { tag: string; sentences: Sentence[] }[]; checks: Check[];
};
const checks = byId(fixture.checks);
const sentences = fixture.blocks.flatMap((b) => b.sentences);
const saying = (words: string) => sentences.find((s) => s.text.includes(words)) as Sentence;

test("evidence marks name a source and a sentence, or a source as a whole", () => {
  assert.deepEqual(parseEvidence("S2:5"), { source: 2, sentence: 5 });
  assert.deepEqual(parseEvidence("S3"), { source: 3, sentence: null });
  for (const wrong of ["notes", "2:5", "S:5", "S2:x", "", null]) assert.equal(parseEvidence(wrong), null);
});

test("what to light is gathered by source, across several sources, in order and once each", () => {
  const lit = lightFor(["S2:5", [1, 4], "S1:1", [1, 4], "S3", "S2:2", "junk", [1, 0]]);
  assert.deepEqual([...lit.entries()], [[1, [1, 4]], [2, [2, 5]], [3, []]]);
  assert.equal(describeLight(lit), "S1:1, 4; S2:2, 5; S3");
});

test("a number pressed in the article lights the source sentence its check found it in", () => {
  const runs = segments(saying("18% less"), checks);
  assert.equal(runs.map((r: { text: string }) => r.text).join(""), saying("18% less").text);
  const numbers = runs.filter((r: { kind: string }) => r.kind === "number");
  assert.deepEqual(numbers.map((r: { text: string }) => r.text), ["18%", "1,200"]);
  for (const run of numbers) {
    assert.equal(run.standing, "found");
    assert.deepEqual([...target(run, checks).light.entries()], [[1, [1]]]);
  }
});

test("marks in different paragraphs light different sources", () => {
  const compost = segments(saying("yields rise"), checks).filter((r: { kind: string }) => r.kind === "number");
  assert.deepEqual([...compost[0].light.keys()], [2]);
  const lit = citedEverywhere(fixture.blocks);
  assert.deepEqual([...lit.entries()], [[1, [1, 4, 8]], [2, [2]], [3, [3]]]);
});

test("a number the checks could not find points at its question instead of lighting anything", () => {
  const [run] = segments(saying("77%"), checks).filter((r: { kind: string }) => r.kind === "number");
  assert.equal(run.standing, "not_found");
  const found = target(run, checks);
  assert.equal(found.light.size, 0);
  assert.equal(found.check.level, "ask");
  assert.match(found.check.detail, /77%/);
  assert.equal(sentenceStanding(saying("77%"), checks), "ask");
});

test("a sentence that cites nothing but states a fact points at its question", () => {
  const s = saying("double happiness");
  assert.equal(sentenceStanding(s, checks), "ask");
  const found = target(s, checks);
  assert.equal(found.light.size, 0);
  assert.equal(found.check.name, "uncited");
  assert.equal(sentenceChecks(s, checks).length, 1);
});

test("a cited sentence with nothing raised lights what it cites", () => {
  const s = saying("five days a week");
  assert.equal(sentenceStanding(s, checks), "cited");
  assert.deepEqual([...target(s, checks).light.entries()], [[3, [3]]]);
});

test("an answered question no longer counts as open", () => {
  const answered = fixture.checks.map((c) => (c.name === "uncited" ? { ...c, answer: "yes" } : c));
  assert.equal(checkSummary(fixture.checks), "2 questions open.");
  assert.equal(checkSummary(answered), "1 question open.");
  assert.equal(sentenceStanding(saying("double happiness"), byId(answered)), "answered");
  assert.deepEqual(raised(answered).map((c: Check) => c.name), ["numbers", "uncited"]);
});

test("a check's standing is one word the page styles by", () => {
  assert.equal(standing(null), "unchecked");
  assert.equal(standing({ level: "ok" }), "found");
  assert.equal(standing({ level: "warn" }), "warn");
  assert.equal(standing({ level: "ask", answer: null }), "not_found");
  assert.equal(standing({ level: "ask", answer: "no" }), "answered");
  assert.equal(checkSummary([]), "Every check passed.");
});

test("marks that overlap or run past the sentence are left as text, so nothing is lost", () => {
  const s = { i: 1, text: "About 18% and 9%.", cites: [], checks: [],
    marks: [{ start: 6, end: 9, kind: "number", check: 1 }, { start: 7, end: 9, kind: "number", check: 2 }, { start: 14, end: 99, kind: "number", check: 3 }] };
  const runs = segments(s, checks);
  assert.equal(runs.map((r: { text: string }) => r.text).join(""), s.text);
  assert.equal(runs.filter((r: { kind: string }) => r.kind === "number").length, 1);
});
