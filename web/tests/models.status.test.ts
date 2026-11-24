import assert from "node:assert/strict";
import { test } from "node:test";
import type { HealthEntry } from "../src/modules/models/api.ts";
import { staleAfter } from "../src/modules/models/lib/events.ts";
import { healthPollMs, pickIndicators } from "../src/modules/models/lib/indicators.ts";

function entry(id: string, local: boolean, ok: boolean | null): HealthEntry {
  return { id, label: id.toUpperCase(), type: "openai_compatible", local, ok, checked_ms: ok === null ? null : 1, error: ok === false ? "down" : null };
}

test("a single local provider is called the local model", () => {
  assert.deepEqual(pickIndicators([entry("den", true, true)]), [{ id: "model:den", label: "Local model online", ok: true }]);
  assert.deepEqual(pickIndicators([entry("den", true, false)]), [{ id: "model:den", label: "Local model offline", ok: false }]);
});

test("several providers are named by their labels, local ones first", () => {
  const picked = pickIndicators([entry("cloud", false, true), entry("den", true, false), entry("attic", true, true)]);
  assert.deepEqual(
    picked.map((item) => item.label),
    ["DEN offline", "ATTIC online", "CLOUD online"],
  );
});

test("at most three indicators are shown and hosted ones give way to local ones", () => {
  const picked = pickIndicators([entry("a", false, true), entry("b", false, true), entry("c", true, true), entry("d", true, true), entry("e", true, false)]);
  assert.deepEqual(
    picked.map((item) => item.id),
    ["model:c", "model:d", "model:e"],
  );
});

test("a provider that has never been checked is not shown", () => {
  assert.deepEqual(pickIndicators([entry("den", true, null)]), []);
  assert.deepEqual(pickIndicators([]), []);
});

test("health is polled at the server's interval, or every 30 seconds without one", () => {
  assert.equal(healthPollMs(undefined), 30_000);
  assert.equal(healthPollMs(45), 45_000);
  assert.equal(healthPollMs(0), 30_000);
  assert.equal(healthPollMs(1), 5_000);
});

test("a model call makes usage and health stale", () => {
  assert.deepEqual(staleAfter("model.called"), ["usage", "health", "providers"]);
  assert.deepEqual(staleAfter("model.failed"), ["usage", "health", "providers"]);
});

test("configuration events make only their own data stale", () => {
  assert.deepEqual(staleAfter("model.provider.saved"), ["providers", "health"]);
  assert.deepEqual(staleAfter("model.provider.removed"), ["providers", "health"]);
  assert.deepEqual(staleAfter("model.role.set"), ["roles"]);
  assert.deepEqual(staleAfter("model.prices.set"), ["prices", "usage"]);
});

test("only the settings that change usage figures refresh usage", () => {
  assert.deepEqual(staleAfter("setting.updated", { key: "models.savings_reference" }), ["usage"]);
  assert.deepEqual(staleAfter("setting.reset", { key: "models.currency" }), ["usage"]);
  assert.deepEqual(staleAfter("setting.updated", { key: "appearance.theme" }), []);
  assert.deepEqual(staleAfter("task.created"), []);
});
