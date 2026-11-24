import assert from "node:assert/strict";
import { test } from "node:test";
import { canInstall, createdLines, isBlunt, needsTypedName, planView, planWarning } from "../src/modules/apps/lib/plan.ts";
import { OWN, RUNS, fullPlan, line, plan } from "./apps.fixtures.ts";

const BLUNT = /runs as you/i;

test("an unreviewed full app gets the blunt warning that it runs as you and can do anything you can", () => {
  const warning = planWarning(fullPlan({ reviewed: false }));
  assert.equal(warning.weight, "blunt");
  assert.match(warning.heading, BLUNT);
  assert.match(warning.heading, /Nobody has reviewed this app/);
  assert.match(warning.text, /do anything you can do/);
  assert.match(warning.text, /Install it only if you trust whoever wrote it/);
});

test("the blunt warning says nobody reviewed the app once, not twice in other words", () => {
  const { heading, text } = planWarning(fullPlan());
  const said = `${heading} ${text}`;
  assert.equal(said.match(/review/gi)?.length, 1);
  assert.doesNotMatch(said, /Not reviewed/);
});

test("a reviewed full app still says it runs as you, but not that nobody has reviewed it", () => {
  const warning = planWarning(fullPlan({ reviewed: true }));
  assert.equal(warning.weight, "red");
  assert.match(warning.heading, BLUNT);
  assert.doesNotMatch(warning.text, /nobody has reviewed/i);
});

test("an unreviewed sandboxed app gets an amber line that nobody reviewed it and that it is held to its list", () => {
  const warning = planWarning(plan({ reviewed: false }));
  assert.equal(warning.weight, "warn");
  assert.equal(warning.heading, "Not reviewed");
  assert.match(warning.text, /^Nobody has reviewed this app\. It can still only do what is listed below/);
});

test("an unreviewed described app says it has no code instead of pointing at a list", () => {
  const warning = planWarning(plan({ described: true }));
  assert.match(warning.text, /has no code/);
});

test("a reviewed sandboxed app gets a calm line", () => {
  assert.equal(planWarning(plan({ reviewed: true })).weight, "calm");
});

test("only an unreviewed full app is blunt, with a red button, and has its name typed", () => {
  assert.equal(isBlunt(fullPlan()), true);
  assert.equal(isBlunt(fullPlan({ reviewed: true })), false);
  assert.equal(isBlunt(plan()), false);
  assert.equal(needsTypedName(fullPlan()), true);
  assert.equal(needsTypedName(plan()), false);
  assert.equal(planView(fullPlan()).installTone, "danger");
  assert.equal(planView(plan()).installTone, "primary");
});

// --- an app that comes with JimmyHQ ---------------------------------------------------

const ODD = /Not reviewed|nobody has reviewed|trust who(ever)? wrote|does not say who made it|cannot check/i;

function everything(view: ReturnType<typeof planView>): string {
  return [view.madeBy, view.sourceLine, view.warning.heading, view.warning.text, view.typedHint ?? "", view.notRunning ?? ""].join(" ");
}

test("a sandboxed app that comes with JimmyHQ says so, says the JimmyHQ project made it, and gives no unreviewed warning", () => {
  const view = planView(plan({ provenance: "bundled", source: { kind: "example", id: "water" }, app: { id: "water", name: "Water", version: "1.0.0", tier: "sandboxed" } }));
  assert.equal(view.bundled, true);
  assert.equal(view.madeBy, "The JimmyHQ project");
  assert.equal(view.warning.weight, "calm");
  assert.equal(view.warning.heading, "Comes with JimmyHQ");
  assert.match(view.warning.text, /The JimmyHQ project made it/);
  assert.match(view.sourceLine, /^JimmyHQ itself/);
  assert.doesNotMatch(everything(view), ODD);
});

test("a full app that comes with JimmyHQ still says it runs as you and still has its name typed, without calling it unreviewed", () => {
  const view = planView(fullPlan({ provenance: "bundled", source: { kind: "example", id: "drafts" } }));
  assert.equal(view.warning.weight, "red");
  assert.match(view.warning.heading, /Comes with JimmyHQ/);
  assert.match(view.warning.text, /runs as you|JimmyHQ cannot limit it/);
  assert.equal(view.nameToType, "Drafts");
  assert.equal(view.installTone, "primary");
  assert.match(view.typedHint ?? "", /Every full app runs as you/);
  assert.doesNotMatch(everything(view), ODD);
  assert.match(view.tier.sentence, /ships with JimmyHQ.*runs as a program on this computer/);
});

test("an example whose files the server did not vouch for gets the normal warning, whatever it is called", () => {
  const view = planView(plan({ provenance: "folder", source: { kind: "example", id: "water" }, app: { id: "water", name: "Water", version: "1.0.0", tier: "sandboxed", author: "The JimmyHQ project" } }));
  assert.equal(view.bundled, false);
  assert.equal(view.warning.heading, "Not reviewed");
  assert.match(view.madeBy, /^The JimmyHQ project, as the app says\. JimmyHQ cannot check this\.$/);
});

test("a full app's dialog says that installing starts its program and where to see how it is", () => {
  assert.match(planView(fullPlan()).notRunning ?? "", /starts its program/);
  assert.match(planView(fullPlan()).notRunning ?? "", /Apps screen/);
  assert.equal(planView(plan()).notRunning, null);
});

test("the dialog says who made the app as its manifest states and that this is unchecked", () => {
  assert.equal(planView(plan()).madeBy, "Example Author, as the app says. JimmyHQ cannot check this.");
});

test("an app that names no author says so instead of leaving a gap", () => {
  const view = planView(plan({ app: { id: "water", name: "Water", version: "1.0.0", tier: "sandboxed" } }));
  assert.equal(view.madeBy, "The app does not say who made it.");
});

test("an app from a folder says it comes from a folder, and an app from git names the address and the pinned commit", () => {
  assert.match(planView(plan()).sourceLine, /folder on this computer/);
  const hash = "3f2a9c1e5b7d4a6c8e0f1a2b3c4d5e6f70819203";
  const git = planView(plan({ source: { kind: "git", url: "https://git.example.com/apps/water.git", commit: hash } }));
  assert.match(git.sourceLine, /git\.example\.com\/apps\/water\.git at commit 3f2a9c1e5b/);
  assert.equal(git.commit, hash);
});

test("every permission line the server sent is in the dialog, in groups, with the server's words", () => {
  const view = planView(fullPlan());
  const sentences = view.groups.flatMap((group) => group.lines.map((item) => item.sentence));
  assert.deepEqual(sentences.sort(), [RUNS.sentence, line("model.ask").sentence, line("jobs").sentence, line("bench.file").sentence, OWN.sentence].sort());
  assert.equal(view.groups[0]?.key, "warning");
});

test("a plan that lists no permission lines cannot be installed from here", () => {
  const view = planView(plan({ permissions: [] }));
  assert.match(view.blocked ?? "", /did not get the list of what this app may do/);
  assert.equal(canInstall(view, ""), false);
});

test("installing an unreviewed full app waits until its name is typed", () => {
  const view = planView(fullPlan());
  assert.equal(view.nameToType, "Drafts");
  assert.equal(canInstall(view, ""), false);
  assert.equal(canInstall(view, "Draft"), false);
  assert.equal(canInstall(view, "drafts"), true);
});

test("installing a sandboxed app needs no name typed", () => {
  const view = planView(plan());
  assert.equal(view.nameToType, null);
  assert.equal(canInstall(view, ""), true);
});

test("an app already installed is replaced, and the dialog says its data stays", () => {
  const view = planView(plan({ installed_version: "0.9.0" }));
  assert.match(view.title, /^Replace Water/);
  assert.match(view.replaces ?? "", /Version 0\.9\.0 is installed/);
  assert.match(view.replaces ?? "", /data stays/);
  assert.equal(view.installLabel, "Replace it");
  assert.equal(planView(plan()).installLabel, "Install");
});

test("what will be created always starts with the app's own folder for data", () => {
  assert.deepEqual(createdLines(null), ["A private folder for its own data, on this computer."]);
});

test("what will be created names each screen, tile, setting, secret, card type, source, tool and job", () => {
  const lines = createdLines(
    {
      screens: [{ id: "board", label: "Posts" }],
      wall: ["Today"],
      settings: 2,
      secrets: [{ name: "booking_key", label: "Booking key" }],
      cards: [{ kind: "draft", label: "Post draft" }],
      memory_sources: [{ label: "Posts" }],
      tools: [{ name: "find_post" }],
      jobs: 1,
    },
  );
  assert.ok(lines.includes("A screen in the sidebar: “Posts”"));
  assert.ok(lines.includes("A tile on the Wall: “Today”"));
  assert.ok(lines.includes("2 settings, in their own section of Settings"));
  assert.ok(lines.includes("A secret you type in yourself, kept in the secret store: “Booking key”"));
  assert.ok(lines.includes("A kind of card on your Bench: “Post draft”"));
  assert.ok(lines.some((text) => text.startsWith("A source offered to Memory, switched off")));
  assert.ok(lines.some((text) => text.startsWith("A tool offered to your agents, off for every agent")));
  assert.ok(lines.includes("Work that runs in the background on a schedule"));
});

test("installing a full app does not promise a Python environment, since nothing is built for it yet", () => {
  assert.ok(!planView(fullPlan()).creates.some((text) => /Python environment/.test(text)));
});

test("the tier in the dialog is the same words the cards use", () => {
  assert.equal(planView(plan()).tier.key, "sandboxed");
  assert.equal(planView(plan({ described: true })).tier.key, "described");
  assert.equal(planView(fullPlan()).tier.key, "full");
});
