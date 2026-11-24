import assert from "node:assert/strict";
import { test } from "node:test";
import {
  adoptsExistingSetup,
  browserTimeZone,
  browserWeekStart,
  chatModelReady,
  filterTimeZones,
  isKnownTimeZone,
  parseProgress,
  previousStep,
  progressAfter,
  stepLabel,
  timeZoneLabel,
  timeZoneList,
  validateAbout,
  weekStartFromDay,
  welcomeStep,
} from "../src/lib/welcome.ts";

const provider = { id: "p", enabled: true, needs_key: false, has_key: false };
const chatRole = { role: "default", primary: { provider_id: "p" } };

test("a fresh installation with no model starts at About you", () => {
  assert.equal(welcomeStep("new", false), "about");
});

test("an installation that can already chat is never sent through the welcome steps", () => {
  assert.equal(welcomeStep("new", true), null);
  assert.equal(adoptsExistingSetup("new", true), true);
  assert.equal(adoptsExistingSetup("about_you", true), false);
});

test("while it is not known whether chat works, the app is shown rather than blocked", () => {
  assert.equal(welcomeStep("new", null), null);
  assert.equal(adoptsExistingSetup("new", null), false);
});

test("a half-finished welcome resumes at the step after the last one saved", () => {
  assert.equal(welcomeStep("about_you", true), "model");
  assert.equal(welcomeStep("model", false), "finish");
  assert.equal(welcomeStep("done", false), null);
});

test("each step saves the progress that leads to the next one", () => {
  assert.equal(welcomeStep(progressAfter("about"), false), "model");
  assert.equal(welcomeStep(progressAfter("model"), false), "finish");
  assert.equal(welcomeStep(progressAfter("finish"), false), null);
});

test("an unknown stored progress counts as not started", () => {
  assert.equal(parseProgress("halfway"), "new");
  assert.equal(parseProgress(undefined), "new");
  assert.equal(parseProgress("model"), "model");
});

test("steps are numbered after creating the owner, and Back stops at the first", () => {
  assert.equal(stepLabel("about"), "Step 2 of 4");
  assert.equal(stepLabel("finish"), "Step 4 of 4");
  assert.equal(previousStep("about"), null);
  assert.equal(previousStep("finish"), "model");
});

test("chat is ready only when the default role points at a usable provider", () => {
  assert.equal(chatModelReady([chatRole], [provider]), true);
  assert.equal(chatModelReady([{ role: "default", primary: null }], [provider]), false);
  assert.equal(chatModelReady([{ role: "smart", primary: { provider_id: "p" } }], [provider]), false);
  assert.equal(chatModelReady([chatRole], []), false);
  assert.equal(chatModelReady([chatRole], [{ ...provider, enabled: false }]), false);
  assert.equal(chatModelReady([chatRole], [{ ...provider, needs_key: true }]), false);
  assert.equal(chatModelReady([chatRole], [{ ...provider, needs_key: true, has_key: true }]), true);
});

test("only real time zone names are accepted", () => {
  assert.equal(isKnownTimeZone("Europe/Paris"), true);
  assert.equal(isKnownTimeZone("UTC"), true);
  assert.equal(isKnownTimeZone(""), false);
  assert.equal(isKnownTimeZone(" Europe/Paris"), false);
  assert.equal(isKnownTimeZone("Mars/Olympus_Mons"), false);
});

test("the browser's time zone is used only when it is a real name", () => {
  assert.equal(browserTimeZone(() => "Asia/Tokyo"), "Asia/Tokyo");
  assert.equal(browserTimeZone(() => "Nowhere/Land"), null);
  assert.equal(browserTimeZone(() => undefined), null);
  assert.equal(browserTimeZone(() => { throw new Error("no Intl"); }), null);
});

test("the list always holds the saved zone, once, in order", () => {
  assert.deepEqual(timeZoneList(["Europe/Paris", "Asia/Tokyo"], ["UTC", "Asia/Tokyo", null, "bogus zone"]), ["Asia/Tokyo", "Europe/Paris", "UTC"]);
});

test("typing filters time zones by every word, ignoring case, slashes and underscores", () => {
  const names = ["America/New_York", "America/Los_Angeles", "Europe/London", "Asia/Tokyo"];
  assert.deepEqual(filterTimeZones(names, "new york"), ["America/New_York"]);
  assert.deepEqual(filterTimeZones(names, "AMERICA"), ["America/New_York", "America/Los_Angeles"]);
  assert.deepEqual(filterTimeZones(names, "europe/lon"), ["Europe/London"]);
  assert.deepEqual(filterTimeZones(names, "  "), names);
  assert.deepEqual(filterTimeZones(names, "atlantis"), []);
  assert.equal(timeZoneLabel("America/New_York"), "America / New York");
});

test("week start comes from the locale only when the setting offers that day", () => {
  assert.equal(weekStartFromDay(1), "monday");
  assert.equal(weekStartFromDay(7), "sunday");
  assert.equal(weekStartFromDay(6), "saturday");
  assert.equal(weekStartFromDay(5), null);
  assert.equal(browserWeekStart("x", () => ({ getWeekInfo: () => ({ firstDay: 7 }) })), "sunday");
  assert.equal(browserWeekStart("x", () => ({ weekInfo: { firstDay: 1 } })), "monday");
  assert.equal(browserWeekStart("x", () => ({})), null);
  assert.equal(browserWeekStart(undefined), null);
  assert.equal(browserWeekStart("x", () => { throw new RangeError("bad tag"); }), null);
});

test("About you needs a name, a real time zone and an offered week start", () => {
  const weeks = ["monday", "sunday", "saturday"];
  assert.deepEqual(validateAbout({ name: " Sam ", timeZone: "Europe/Paris", weekStart: "monday" }, weeks), {});
  const errors = validateAbout({ name: "  ", timeZone: "Paris", weekStart: "friday" }, weeks);
  assert.ok(errors.name && errors.timeZone && errors.weekStart);
  assert.ok(validateAbout({ name: "x".repeat(121), timeZone: "UTC", weekStart: "sunday" }, weeks).name);
});

test("the last welcome step points at the Apps screen as well as at chat, tasks and the Village", async () => {
  const { FINISH_SCREENS } = await import("../src/lib/welcome.ts");
  assert.ok(FINISH_SCREENS.includes("apps"));
  assert.ok(["chat", "tasks", "village"].every((screen) => (FINISH_SCREENS as readonly string[]).includes(screen)));
});
