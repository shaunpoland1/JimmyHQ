// The Reader page: topic health in words.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { ago, forWhom, healthLine, outcomesLine, quietOffer, verdict } from "../../apps/reader/web/lib/health.js";

const DAY = 86_400_000;
const NOW = 1_800_000_000_000;
const health = (more: Record<string, unknown> = {}) => ({ found: 12, kept: 5, skipped: 3, later: 1, waiting: 2, decided: 9, last_found_ms: NOW - 3 * DAY, quiet_days: 3, quiet: false, ...more });

test("a topic's counts read as one plain line", () => {
  assert.equal(healthLine(health(), NOW), "Found 12 · kept 5 · skipped 3 · 2 waiting · last found 3 days ago");
  assert.equal(healthLine(health({ found: 0, kept: 0, skipped: 0, waiting: 0, last_found_ms: null }), NOW), "Found 0 · kept 0 · skipped 0 · nothing found yet");
});

test("how long ago reads in days, weeks and months", () => {
  assert.equal(ago(NOW, NOW), "today");
  assert.equal(ago(NOW - DAY, NOW), "yesterday");
  assert.equal(ago(NOW - 13 * DAY, NOW), "13 days ago");
  assert.equal(ago(NOW - 21 * DAY, NOW), "3 weeks ago");
  assert.equal(ago(NOW - 90 * DAY, NOW), "3 months ago");
  assert.equal(ago(null, NOW), "never");
});

test("a quiet topic is offered a pause, and a paused one is not", () => {
  assert.equal(quietOffer({ paused: 0, health: health({ quiet: true, quiet_days: 16 }) }), "Quiet for 16 days. Pause it, or try other phrasings?");
  assert.equal(quietOffer({ paused: 0, health: health({ quiet: true, quiet_days: 1 }) }), "Quiet for 1 day. Pause it, or try other phrasings?");
  assert.equal(quietOffer({ paused: 1, health: health({ quiet: true }) }), null);
  assert.equal(quietOffer({ paused: 0, health: health() }), null);
});

test("a verdict is given only once the owner has chosen enough", () => {
  assert.equal(verdict(health({ decided: 4 })), null);
  assert.equal(verdict(health({ kept: 8, skipped: 2, decided: 10 })), "Mostly kept");
  assert.equal(verdict(health({ kept: 1, skipped: 9, decided: 10 })), "Mostly skipped");
  assert.equal(verdict(health({ kept: 5, skipped: 5, decided: 10 })), "Mixed");
});

test("who a topic is for, and what happens to its articles, read as words", () => {
  assert.equal(forWhom({ audience: "" }), "For you");
  assert.equal(forWhom({ audience: "People who follow my page" }), "For: People who follow my page");
  assert.equal(outcomesLine(["bench", "memory"]), "On your Bench to read, Mined into Memory");
  assert.equal(outcomesLine(["publish"]), "Kept in the list");
});
