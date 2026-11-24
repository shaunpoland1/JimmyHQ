// An app that asks to keep more than the usual storage: the install and update dialogs say
// how much, in the server's one sentence.

import assert from "node:assert/strict";
import { test } from "node:test";
import { planView } from "../src/modules/apps/lib/plan.ts";
import { readUpdatePlan } from "../src/modules/apps/lib/update.ts";
import { readPlan } from "../src/modules/apps/lib/wire.ts";
import { plan } from "./apps.fixtures.ts";

const SENTENCE = "Keep up to 500 MB of its own data, such as pictures you give it, on this computer.";

test("the install dialog says how much storage an app asks for, and nothing when it asks for none", () => {
  assert.equal(planView(plan({ storage: SENTENCE })).storage, SENTENCE);
  assert.equal(planView(plan()).storage, null);
});

test("the plan's storage ask is read from what the server sends", () => {
  const raw = { plan: "01JD0000000000000000000000", fingerprint: "f".repeat(64), source: { kind: "folder" }, app: { id: "posts", name: "Posts", tier: "sandboxed" }, permissions: [], storage: { asked_mb: 500, usual_mb: 50, sentence: SENTENCE } };
  assert.equal(readPlan(raw, { kind: "folder", path: "x" }).storage, SENTENCE);
  assert.equal(readPlan({ ...raw, storage: null }, { kind: "folder", path: "x" }).storage, null);
});

test("an update that asks for more storage says so too", () => {
  const raw = { plan: "p1", fingerprint: "f".repeat(64), app: { id: "posts", name: "Posts", version: "1.1.0" }, update: { from_version: "1.0.0", ask: [], removed: [], tier: null, needs_consent: false, storage: { sentence: SENTENCE } } };
  assert.equal(readUpdatePlan(raw).storage, SENTENCE);
});
