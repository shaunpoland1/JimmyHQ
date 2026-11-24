import assert from "node:assert/strict";
import { test } from "node:test";
import { KEY_MAX_LENGTH, affectsLookupStatus, checkKey, keyState, leavesLine, providerAbout, readiness, searchWords, tryLine, tryRows } from "../src/modules/lookup/lib/status.ts";

test("a key is trimmed and accepted when it is one run of printable characters", () => {
  assert.deepEqual(checkKey("  sk-abc_123  "), { ok: true, key: "sk-abc_123" });
});

test("an empty key, a key with a space and a key that is too long are each refused with a sentence", () => {
  for (const bad of ["", "   ", "two words", "line\nbreak", "tab\tkey", "k".repeat(KEY_MAX_LENGTH + 1), "café"]) {
    const check = checkKey(bad);
    assert.equal(check.ok, false, JSON.stringify(bad));
    if (!check.ok) assert.match(check.error, /\.$/);
  }
});

test("a key of exactly the longest length is accepted", () => {
  assert.equal(checkKey("k".repeat(KEY_MAX_LENGTH)).ok, true);
});

test("a change to a Web lookup setting or a lookup receipt makes the status worth asking for again", () => {
  assert.equal(affectsLookupStatus({ kind: "setting.updated", payload: { key: "lookup.enabled" } }), true);
  assert.equal(affectsLookupStatus({ kind: "setting.reset", payload: { key: "lookup.search_provider" } }), true);
  assert.equal(affectsLookupStatus({ kind: "lookup.searched", payload: {} }), true);
  assert.equal(affectsLookupStatus({ kind: "setting.updated", payload: { key: "chat.local_only" } }), false);
  assert.equal(affectsLookupStatus({ kind: "chat.message.sent" }), false);
});

test("a provider that needs a key is saved or missing, and one that needs none says so", () => {
  assert.equal(keyState({ needs_key: true, key_set: true }), "saved");
  assert.equal(keyState({ needs_key: true, key_set: false }), "missing");
  assert.equal(keyState({ needs_key: false, key_set: false }), "none");
});

test("every provider the server lists has a line of explanation, and SearXNG says it needs no key", () => {
  for (const id of ["exa", "tavily", "brave", "searxng", "jina", "none"]) assert.notEqual(providerAbout(id), "", id);
  assert.match(providerAbout("searxng"), /no key/);
  assert.match(providerAbout("searxng"), /yourself/);
  assert.equal(providerAbout("unheard-of"), "");
});

test("the header says off, ready, or on with search not ready", () => {
  assert.deepEqual(readiness({ enabled: false, search_ready: true }), { tone: "off", label: "Off" });
  assert.equal(readiness({ enabled: true, search_ready: true }).tone, "ok");
  assert.equal(readiness({ enabled: true, search_ready: false }).tone, "warn");
});

test("the line about what leaves the machine names where searches go", () => {
  assert.match(leavesLine({ provider: "exa", provider_label: "Exa" }), /go to Exa/);
  assert.match(leavesLine({ provider: "searxng", provider_label: "SearXNG (your own server)" }), /your own SearXNG server/);
  assert.match(leavesLine({ provider: "none", provider_label: "None" }), /nothing is searched/);
  assert.match(leavesLine({ provider: "exa", provider_label: "Exa" }), /read from this computer/);
});

test("a tried search reports its result count, provider and time", () => {
  const one = { provider_label: "Exa", duration_ms: 420, results: [{ title: "a", url: "https://example.com", snippet: "", text: null }] };
  assert.equal(tryLine(one, "en-US"), "1 result from Exa in 0.4 s");
  assert.equal(tryLine({ ...one, results: [] }, "en-US"), "No results from Exa in 0.4 s");
});

test("tried results become rows with a host, and a result with no title is shown by its host", () => {
  const rows = tryRows({
    results: [
      { title: "  Growing broad beans ", url: "https://www.example.com/beans", snippet: " Sow in autumn. ", text: null },
      { title: "", url: "https://example.org/x", snippet: "", text: null },
      { title: "Odd", url: "javascript:alert(1)", snippet: "", text: null },
    ],
  });
  assert.deepEqual(rows[0], { title: "Growing broad beans", url: "https://www.example.com/beans", host: "example.com", snippet: "Sow in autumn." });
  assert.equal(rows[1]?.title, "example.org");
  assert.equal(rows[2]?.host, "");
});

test("search words are tidied and an empty box sends nothing", () => {
  assert.equal(searchWords("  broad   beans \n sowing "), "broad beans sowing");
  assert.equal(searchWords("   "), null);
  assert.equal(searchWords("x".repeat(500))?.length, 400);
});
