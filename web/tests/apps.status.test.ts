import assert from "node:assert/strict";
import { test } from "node:test";
import {
  aboutLines,
  BUNDLED_BADGE,
  NOT_REVIEWED_BADGE,
  appState,
  badgeTone,
  badges,
  keptLine,
  DOOR_ERASED,
  madeByLine,
  removeIntro,
  sourceText,
  switchLabel,
  switchedToast,
  tierKey,
  tierWords,
  whereLines,
} from "../src/modules/apps/lib/status.ts";

test("a sandboxed app with code is called a page in a box that can only do what is listed", () => {
  const words = tierWords({ tier: "sandboxed", screens: [{ id: "main", label: "Water", entry: "index.html" }] });
  assert.equal(words.key, "sandboxed");
  assert.match(words.sentence, /page in a box/);
  assert.match(words.sentence, /only do what is listed/);
});

test("an app whose every screen is the core's own renderer is called records and views with no code", () => {
  const screens = [{ id: "main", label: "Books", entry: "described" }];
  assert.equal(tierKey({ tier: "sandboxed", screens }), "described");
  assert.match(tierWords({ tier: "sandboxed", screens }).sentence, /records and views/i);
  assert.match(tierWords({ tier: "sandboxed", screens }).sentence, /no code/i);
});

test("an app with one described screen and one with code is not called described", () => {
  const screens = [
    { id: "a", label: "A", entry: "described" },
    { id: "b", label: "B", entry: "b.html" },
  ];
  assert.equal(tierKey({ tier: "sandboxed", screens }), "sandboxed");
});

test("the server saying an app is described is believed even before its screens are listed", () => {
  assert.equal(tierKey({ tier: "sandboxed", described: true }), "described");
});

test("a full app is called a program that runs as you", () => {
  const words = tierWords({ tier: "full" });
  assert.equal(words.key, "full");
  assert.match(words.sentence, /program that runs as you/);
});

test("a full app is never called described, whatever its screens say", () => {
  assert.equal(tierKey({ tier: "full", described: true, screens: [{ id: "a", label: "A", entry: "described" }] }), "full");
});

test("an app that is on and well reads as on", () => {
  assert.deepEqual(appState({ enabled: true }), { kind: "on", label: "On", tone: "ok", reason: null });
});

test("an app that is switched off reads as off even when something is wrong with it", () => {
  assert.equal(appState({ enabled: false }).kind, "off");
  assert.equal(appState({ enabled: false, attention: "A file is missing." }).kind, "off");
  assert.equal(appState({ enabled: false, health: "crashing" }).kind, "off");
});

test("an app that needs attention carries the server's reason word for word", () => {
  const state = appState({ enabled: true, attention: "Its screen file index.html is missing." });
  assert.equal(state.kind, "attention");
  assert.equal(state.label, "Needs attention");
  assert.equal(state.reason, "Its screen file index.html is missing.");
});

test("an app that keeps crashing or stops answering says what to do next", () => {
  const crashing = appState({ enabled: true, health: "crashing" });
  assert.equal(crashing.kind, "attention");
  assert.match(crashing.reason ?? "", /press Restart/i);
  const silent = appState({ enabled: true, health: "not_responding" });
  assert.equal(silent.kind, "attention");
  assert.equal(silent.tone, "warn");
});

test("a full app held back after crashing says it is held back and that Restart starts it", () => {
  const held = appState({ enabled: true, health: "crashing" });
  assert.match(held.reason ?? "", /holding it back/);
  assert.match(held.reason ?? "", /Restart/);
  assert.equal(appState({ enabled: true, health: "running" }).kind, "on");
});

test("badges name development builds, waiting updates and apps nobody has reviewed", () => {
  assert.deepEqual(badges({ reviewed: true }), []);
  assert.deepEqual(badges({}), ["Not reviewed"]);
  assert.deepEqual(badges({ development: true, update_available: "1.2.0", reviewed: false }), ["Development", "Update available: 1.2.0", "Not reviewed"]);
});

test("the switch's name says what the app is now and what pressing does", () => {
  assert.equal(switchLabel({ name: "Water", enabled: true }), "Water is on. Press to switch off.");
  assert.equal(switchLabel({ name: "Water", enabled: false }), "Water is off. Press to switch on.");
});

test("switching an app off says its data is kept", () => {
  assert.equal(switchedToast({ name: "Water" }, true), "Switched on Water.");
  assert.match(switchedToast({ name: "Water" }, false), /data is kept/);
});

test("where an app's screens are is said in words, one place per line", () => {
  const lines = whereLines({
    screens: [{ id: "main", label: "Water" }],
    dock: [{ id: "t", label: "Glasses" }, { id: "u", label: "Goal" }],
    wall: [{ id: "w", label: "Today" }],
  });
  assert.deepEqual(lines, [
    "A screen in the sidebar: “Water”",
    "Tabs in the side panel: “Glasses”, “Goal”",
    "A tile on the Wall: “Today”",
  ]);
  assert.deepEqual(whereLines({}), []);
});

test("removing an app says what goes and what stays, and where the data is", () => {
  assert.match(removeIntro({ name: "Water", tier: "sandboxed" }), /screens go away/);
  assert.doesNotMatch(removeIntro({ name: "Water", tier: "sandboxed" }), /\?/);
  assert.match(removeIntro({ name: "Drafts", tier: "full" }), /program are deleted/);
  assert.match(keptLine({ id: "water" }), /apps\/water\/data/);
  assert.match(keptLine({ id: "water", data_dir: "the data folder" }), /in the data folder/);
  assert.doesNotMatch(keptLine({ id: "water", tier: "sandboxed" }), /keys/);
});

test("removing a full app with its data kept says its keys are kept and its door will be off", () => {
  const line = keptLine({ id: "house", tier: "full" });
  assert.match(line, /picks up where it left off/);
  assert.match(line, /its keys are kept for this same app; its door will be off until you switch it on again/);
  assert.match(DOOR_ERASED, /keys.*deleted/);
});

test("an app that comes with JimmyHQ is badged as such and never as not reviewed", () => {
  assert.deepEqual(badges({ provenance: "bundled" }), ["Comes with JimmyHQ"]);
  assert.deepEqual(badges({ provenance: "folder" }), ["Not reviewed"]);
});

test("the detail's about lines name the JimmyHQ project, its review and its source for an app that comes with JimmyHQ", () => {
  const about = aboutLines({ provenance: "bundled", author: "Someone Else", source: { kind: "folder" } });
  assert.deepEqual(about, { madeBy: "The JimmyHQ project", review: "It comes with JimmyHQ", source: "JimmyHQ itself, copied into your apps" });
});

test("the detail's about lines for anyone else's app say what the app claims, unchecked, and that nobody reviewed it", () => {
  const about = aboutLines({ provenance: "git", author: "Example Author", source: { kind: "git", repo: "https://git.example.com/a.git", commit: "abc" } });
  assert.equal(about.madeBy, "Example Author, as the app says. JimmyHQ cannot check this.");
  assert.equal(about.review, "Nobody has reviewed it");
  assert.equal(about.source, "https://git.example.com/a.git at commit abc");
  assert.equal(aboutLines({ reviewed: true, source: null }).review, "Reviewed and listed in the store");
});

test("who made an app is never left blank", () => {
  assert.equal(madeByLine(undefined, false), "The app does not say who made it.");
  assert.equal(madeByLine(undefined, true), "The JimmyHQ project");
  assert.equal(sourceText({ source: null }), "Not recorded");
});

test("a full app that ships with JimmyHQ says so, and still says it runs as a program on this computer", () => {
  const words = tierWords({ tier: "full", provenance: "bundled" });
  assert.equal(words.key, "full");
  assert.match(words.sentence, /ships with JimmyHQ/);
  assert.match(words.sentence, /runs as a program on this computer/);
  assert.match(words.sentence, /could do anything you can/);
  assert.doesNotMatch(tierWords({ tier: "full", provenance: "git" }).sentence, /ships with/);
});

test("an app that comes with JimmyHQ is drawn apart from one nobody reviewed and from the plain badges", () => {
  assert.equal(badgeTone(BUNDLED_BADGE), "accent");
  assert.equal(badgeTone(NOT_REVIEWED_BADGE), "warn");
  assert.equal(badgeTone("Development"), "plain");
});
