import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_ROUTE, formatHash, hashForPath, parseHash, sameRoute } from "../src/lib/route.ts";

test("parseHash reads the screen and optional sub-path", () => {
  assert.deepEqual(parseHash("#/bench"), { screen: "bench", sub: null });
  assert.deepEqual(parseHash("#/settings/appearance"), { screen: "settings", sub: "appearance" });
  assert.deepEqual(parseHash("#/Tasks/"), { screen: "tasks", sub: null });
});

test("parseHash falls back to the default screen", () => {
  assert.deepEqual(parseHash(""), DEFAULT_ROUTE);
  assert.deepEqual(parseHash("#"), DEFAULT_ROUTE);
  assert.deepEqual(parseHash("#/nope"), DEFAULT_ROUTE);
  assert.deepEqual(parseHash("#/%E0%A4%A"), DEFAULT_ROUTE);
});

test("formatHash round-trips through parseHash", () => {
  const route = { screen: "settings", sub: "my section" } as const;
  assert.equal(formatHash(route), "#/settings/my%20section");
  assert.deepEqual(parseHash(formatHash(route)), route);
  assert.equal(formatHash({ screen: "chat", sub: null }), "#/chat");
});

test("sameRoute compares both parts", () => {
  assert.ok(sameRoute({ screen: "chat", sub: null }, { screen: "chat", sub: null }));
  assert.ok(!sameRoute({ screen: "settings", sub: "a" }, { screen: "settings", sub: "b" }));
});

test("a sub-route with several parts reads as a path and comes back whole", () => {
  const hash = formatHash({ screen: "memory", sub: "shared/my ideas.md" });
  assert.equal(hash, "#/memory/shared/my%20ideas.md");
  assert.deepEqual(parseHash(hash), { screen: "memory", sub: "shared/my ideas.md" });
  assert.deepEqual(parseHash("#/memory/shared%2Fideas.md"), { screen: "memory", sub: "shared/ideas.md" });
});

test("a plain path for a screen stands for its hash route, and an address with a hash is left alone", () => {
  assert.equal(hashForPath("/settings", ""), "#/settings");
  assert.equal(hashForPath("/settings/appearance", "#"), "#/settings/appearance");
  assert.equal(hashForPath("/app/water/main", ""), "#/app/water/main");
  assert.equal(hashForPath("/Tasks/", ""), "#/tasks");
  assert.equal(hashForPath("/settings", "#/bench"), null);
  assert.equal(hashForPath("/", ""), null);
  assert.equal(hashForPath("/nope", ""), null);
  assert.equal(hashForPath("/api/status", ""), null);
});
