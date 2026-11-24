import assert from "node:assert/strict";
import { test } from "node:test";
import { BUILD_KINDS, DESCRIBED_EXAMPLE, GUIDE_LINK_NOTE, guideUrl } from "../src/modules/apps/lib/build.ts";
import { EMPTY_ABOUT, EMPTY_TRY, EXAMPLES, TIER_SENTENCES } from "../src/modules/apps/lib/examples.ts";

test("the build guide covers the three kinds of app, simplest first", () => {
  assert.deepEqual(BUILD_KINDS.map((kind) => kind.key), ["described", "sandboxed", "full"]);
  for (const kind of BUILD_KINDS) assert.ok(kind.steps.length > 0, kind.key);
});

test("the build guide says plainly that a full app runs as you, and only the full app has a caveat", () => {
  const full = BUILD_KINDS.find((kind) => kind.key === "full");
  assert.match(full?.caveat ?? "", /runs as you/);
  assert.ok(full?.steps.some((step) => step.includes("jimmyhq apps dev")));
  assert.ok(BUILD_KINDS.filter((kind) => kind.key !== "full").every((kind) => kind.caveat === null));
});

test("the example description to copy is a whole described app in valid JSON", () => {
  const app = JSON.parse(DESCRIBED_EXAMPLE) as { tier: string; screens: { entry: string }[]; permissions: string[] };
  assert.equal(app.tier, "sandboxed");
  assert.ok(app.screens.every((screen) => screen.entry === "described"));
  assert.deepEqual(app.permissions, []);
});

test("the link to the full guide points at the apps page of the docs and says it leaves JimmyHQ", () => {
  assert.equal(guideUrl("https://git.example.com/project/"), "https://git.example.com/project/blob/main/docs/apps.md");
  assert.match(GUIDE_LINK_NOTE, /new tab, outside JimmyHQ/);
});

test("the empty screen has one panel to read and one to act on, each with its own heading", () => {
  assert.notEqual(EMPTY_ABOUT.heading, EMPTY_TRY.heading);
  assert.equal(TIER_SENTENCES.length, 3);
  assert.ok(EXAMPLES.length >= 3);
  assert.match(EMPTY_TRY.text, /Nothing is installed until you say so/);
});

test("the small full example is offered first among the full ones and says what it does", () => {
  const full = EXAMPLES.filter((example) => example.tier === "full");
  assert.equal(full[0]?.id, "watering");
  assert.match(full[0]?.blurb ?? "", /Bench/);
});
