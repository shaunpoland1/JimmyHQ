import assert from "node:assert/strict";
import { test } from "node:test";
import type { Passage, Why } from "../src/modules/memory/api.ts";
import { splitLine, splitPassages } from "../src/modules/memory/lib/search.ts";

function passage(n: number, note: string, why: Why): Passage {
  return { id: `c:${n}`, text: `Passage ${n}`, note, heading: "", fact_id: null, score: 1 / n, why, confirmed: null, chars: 9 };
}

test("passages found by meaning alone are kept apart from the ones that match the words", () => {
  const split = splitPassages([
    passage(1, "a.md", "both"),
    passage(2, "b.md", "meaning"),
    passage(3, "a.md", "keyword"),
    passage(4, "a.md", "meaning"),
  ]);
  assert.deepEqual(
    split.matches.map((g) => [g.note, g.passages.map((p) => p.rank)]),
    [["a.md", [1, 2]]],
  );
  assert.deepEqual(
    split.related.map((g) => [g.note, g.passages.map((p) => p.rank)]),
    [
      ["b.md", [3]],
      ["a.md", [4]],
    ],
  );
  assert.equal(split.matchCount, 2);
  assert.equal(split.relatedCount, 2);
});

test("related passages are never counted as matches in the summary", () => {
  const one = splitPassages([passage(1, "a.md", "both")]);
  assert.equal(splitLine(one), "1 match in 1 note.");
  const mixed = splitPassages([passage(1, "a.md", "keyword"), passage(2, "b.md", "meaning"), passage(3, "c.md", "meaning")]);
  assert.equal(splitLine(mixed), "1 match in 1 note. 2 more seem related in meaning.");
  const onlyRelated = splitPassages([passage(1, "a.md", "meaning")]);
  assert.equal(splitLine(onlyRelated), "No passage contains these words. 1 more seems related in meaning.");
  assert.equal(splitLine(splitPassages([])), "Nothing matched.");
});
