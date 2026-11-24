import assert from "node:assert/strict";
import { test } from "node:test";
import { buildPrices, makePriceId, newDraft, parsePrice, priceErrors, priceLabel, priceText, pricesChanged, toDrafts } from "../src/modules/models/lib/prices.ts";

const saved = [
  { id: "big", provider: "anthropic", model: "big-*", input_per_mtok: 4, output_per_mtok: 20 },
  { id: "small", provider: "*", model: "small-*", input_per_mtok: 0.1, output_per_mtok: 0.5 },
];

test("a price is a plain number from 0 to a million", () => {
  assert.equal(parsePrice("4"), 4);
  assert.equal(parsePrice(" 0.25 "), 0.25);
  assert.equal(parsePrice(".5"), 0.5);
  assert.equal(parsePrice("1000000"), 1_000_000);
  assert.equal(parsePrice("1000001"), null);
  assert.equal(parsePrice("-1"), null);
  assert.equal(parsePrice("1e3"), null);
  assert.equal(parsePrice("4,5"), null);
  assert.equal(parsePrice(""), null);
  assert.equal(parsePrice("abc"), null);
});

test("a price line needs a provider, a model pattern and two numbers", () => {
  assert.deepEqual(Object.keys(priceErrors(newDraft(1))).sort(), ["input", "model", "output"]);
  assert.ok(priceErrors({ ...newDraft(1), provider: " ", model: "m", input: "1", output: "1" }).provider);
  assert.deepEqual(priceErrors({ ...newDraft(1), model: "m-*", input: "1", output: "2.5" }), {});
});

test("saved lines round-trip unchanged and keep their ids", () => {
  const drafts = toDrafts(saved);
  assert.equal(pricesChanged(drafts, saved), false);
  assert.deepEqual(buildPrices(drafts), { ok: true, prices: saved });
});

test("editing, reordering, adding or removing a line counts as a change", () => {
  const drafts = toDrafts(saved);
  assert.equal(pricesChanged([{ ...drafts[0]!, input: "5" }, drafts[1]!], saved), true);
  assert.equal(pricesChanged([drafts[1]!, drafts[0]!], saved), true);
  assert.equal(pricesChanged([...drafts, newDraft(1)], saved), true);
  assert.equal(pricesChanged([drafts[0]!], saved), true);
  assert.equal(pricesChanged([{ ...drafts[0]!, input: "4.0" }, drafts[1]!], saved), false);
});

test("a new line gets a readable id that no other line uses", () => {
  const drafts = [...toDrafts(saved), { ...newDraft(1), provider: "anthropic", model: "big-*", input: "1", output: "2" }, { ...newDraft(2), provider: "*", model: "*", input: "0", output: "0" }];
  const built = buildPrices(drafts);
  assert.equal(built.ok, true);
  if (built.ok) {
    const ids = built.prices.map((entry) => entry.id);
    assert.equal(new Set(ids).size, ids.length);
    assert.deepEqual(ids, ["big", "small", "anthropic-big-any", "any-any"]);
    for (const id of ids) assert.match(id, /^[a-z][a-z0-9_-]{0,39}$/);
  }
  assert.equal(makePriceId("anthropic", "big", new Set(["anthropic-big"])), "anthropic-big-2");
  assert.match(makePriceId("9", "9", new Set()), /^p-/);
});

test("a table with a bad line is not built and says how many lines need fixing", () => {
  const built = buildPrices([...toDrafts(saved), newDraft(1)]);
  assert.equal(built.ok, false);
  if (!built.ok) {
    assert.match(built.summary, /^One line/);
    assert.deepEqual(built.errors[0], {});
    assert.ok(built.errors[2]?.model);
  }
});

test("a price line is named by its provider and pattern", () => {
  assert.equal(priceLabel(saved[0]!), "anthropic · big-*");
  assert.equal(priceLabel(saved[1]!), "any provider · small-*");
});

test("a very small saved price loads as a plain decimal that can be saved again unchanged", () => {
  const tiny = [{ id: "tiny", provider: "*", model: "*", input_per_mtok: 1e-7, output_per_mtok: 2 }];
  assert.equal(priceText(1e-7), "0.0000001");
  assert.equal(priceText(2), "2");
  assert.equal(priceText(0.25), "0.25");
  const drafts = toDrafts(tiny);
  assert.deepEqual(priceErrors(drafts[0]!), {});
  assert.equal(pricesChanged(drafts, tiny), false);
  assert.deepEqual(buildPrices(drafts), { ok: true, prices: tiny });
});
