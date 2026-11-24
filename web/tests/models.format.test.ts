import assert from "node:assert/strict";
import { test } from "node:test";
import { formatCompact, formatCost, formatCostCell, formatDuration, formatModelUsage } from "../src/modules/models/lib/format.ts";

const totals = { calls: 46_512, input_tokens: 150_000_000, output_tokens: 32_800_000, estimated_cost: 243.488, saved_by_local: 201.12 };

test("compact counts use K for thousands and M for millions with one decimal", () => {
  assert.equal(formatCompact(0, "en-US"), "0");
  assert.equal(formatCompact(950, "en-US"), "950");
  assert.equal(formatCompact(1000, "en-US"), "1.0K");
  assert.equal(formatCompact(46_512, "en-US"), "46.5K");
  assert.equal(formatCompact(950_000, "en-US"), "950.0K");
  assert.equal(formatCompact(182_800_000, "en-US"), "182.8M");
  assert.equal(formatCompact(2_500_000_000, "en-US"), "2.5B");
});

test("a count that would round to 1000 of a unit moves up to the next unit", () => {
  assert.equal(formatCompact(999_960, "en-US"), "1.0M");
  assert.equal(formatCompact(999_940, "en-US"), "999.9K");
});

test("cost always shows two decimals and falls back to a prefix for an unknown currency label", () => {
  assert.equal(formatCost(243.488, "USD", "en-US"), "$243.49");
  assert.equal(formatCost(5, "usd", "en-US"), "$5.00");
  assert.equal(formatCost(12.5, "credits", "en-US"), "credits 12.50");
  assert.equal(formatCost(12.5, "", "en-US"), "12.50");
});

test("a tiny cost in a table is not rounded down to nothing", () => {
  assert.equal(formatCostCell(0.000216, "USD", "en-US"), "< $0.01");
  assert.equal(formatCostCell(0, "USD", "en-US"), "$0.00");
});

test("the usage line has calls, tokens, cost and local savings", () => {
  assert.equal(formatModelUsage(totals, "USD", "en-US"), "LLM 46.5K calls · 182.8M tok · est $243.49 · saved $201.12 local");
});

test("the usage line is null until a call has been made", () => {
  assert.equal(formatModelUsage({ ...totals, calls: 0 }, "USD", "en-US"), null);
});

test("the usage line leaves out parts that are null or zero", () => {
  assert.equal(formatModelUsage({ ...totals, estimated_cost: 0, saved_by_local: null }, "USD", "en-US"), "LLM 46.5K calls · 182.8M tok");
  assert.equal(formatModelUsage({ ...totals, saved_by_local: 0 }, "USD", "en-US"), "LLM 46.5K calls · 182.8M tok · est $243.49");
  assert.equal(formatModelUsage({ calls: 1, input_tokens: 0, output_tokens: 0, estimated_cost: 0, saved_by_local: 0.0002 }, "USD", "en-US"), "LLM 1 call");
});

test("durations read as milliseconds below a second and seconds above", () => {
  assert.equal(formatDuration(340, "en-US"), "340 ms");
  assert.equal(formatDuration(1234, "en-US"), "1.2 s");
});
