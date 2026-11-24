import assert from "node:assert/strict";
import { test } from "node:test";
import { exampleSource } from "../src/modules/apps/lib/examples.ts";
import { APPS_PATHS, cancelBody, confirmBody, planBody, removeBody } from "../src/modules/apps/lib/requests.ts";
import { noteLines, readApp, readApps, readPlan, readRefusal } from "../src/modules/apps/lib/wire.ts";
import { planView } from "../src/modules/apps/lib/plan.ts";

const ID = /^[a-z][a-z0-9_]{1,30}$/;
const COMMIT = "3f2a9c1e5b7d4a6c8e0f1a2b3c4d5e6f70819203";
const REPO = "https://git.example.com/apps/water.git";

// What the server really sends (server/jimmyhq/apps/install.py), copied from a test run.
const SERVER_PLAN = {
  plan: "01JD3V7Q2M8K4N6P0R5T9W1XYZ",
  created_at: 1795000000000,
  expires_at: 1795001800000,
  app: { id: "water", name: "Water", version: "1.0.0", author: "", summary: "Count glasses of water.", licence: "MIT", tier: "sandboxed" },
  source: { kind: "folder", repo: null, commit: null, ref: null, tree: "5d".repeat(32), files: 3, bytes: 1098, left_out: 0 },
  reviewed: false,
  provenance: "folder",
  review: "Not reviewed. Install only if you trust who wrote it.",
  warning: null,
  needs_typed_name: false,
  permissions: [
    { id: "tasks.write", permission: "tasks.write", host: null, sentence: "Add tasks, and mark the ones it added as done.", strong: false, always: false },
    { id: "always.data", permission: "always.data", host: null, sentence: "Keep its own data on this computer.", strong: false, always: true },
  ],
  adds: { screens: ["Water"], dock: [], wall: [], cards: [], memory_sources: [], tools: [], schedule: false, settings: ["Glasses a day"], secrets: [] },
  described: false,
  fingerprint: "9f".repeat(32),
};

const SERVER_DETAIL = {
  id: "water", name: "Water", version: "1.0.0", tier: "sandboxed", source: "git", repo: REPO, commit: COMMIT, tree: "5d".repeat(32),
  reviewed: false, enabled: true, disabled_reason: null, installed_at: 1795000000000, updated_at: null, consented_at: 1795000000000,
  fingerprint: "9f".repeat(32), permissions: ["tasks.write"], provenance: "git", review: "Not reviewed. Install only if you trust who wrote it.",
  summary: "Count glasses of water.", author: "", licence: "MIT", described: false,
  screens: [{ id: "main", label: "Water", described: false }], dock: [], wall: [], broken: null,
  lines: SERVER_PLAN.permissions, warning: null,
  consents: [{ at: 1795000000000, by: "owner", version: "1.0.0", tier: "sandboxed", permissions: ["tasks.write"], added: ["tasks.write"], removed: [], fingerprint: "9f".repeat(32) }],
  storage_bytes: 4096, adds: SERVER_PLAN.adds,
};

test("no word the routes use for all apps could ever be an app's id", () => {
  for (const path of [APPS_PATHS.installed, APPS_PATHS.examples, APPS_PATHS.plan, APPS_PATHS.confirm, APPS_PATHS.cancel]) {
    const word = path.replace("/api/apps/", "").split("/")[0] ?? "";
    assert.ok(word.startsWith("_") && !ID.test(word), path);
  }
  assert.equal(APPS_PATHS.shell, "/api/apps");
});

test("each address about one app is under that app's id", () => {
  assert.equal(APPS_PATHS.detail("water"), "/api/apps/water");
  assert.equal(APPS_PATHS.enable("water"), "/api/apps/water/enable");
  assert.equal(APPS_PATHS.disable("water"), "/api/apps/water/disable");
  assert.equal(APPS_PATHS.remove("water"), "/api/apps/water/remove");
  assert.equal(APPS_PATHS.described("books_read"), "/api/apps/books_read/described");
});

test("something that is not an id is never put in an address", () => {
  for (const odd of ["../settings", "..", ".", "%2e%2e", "water/remove", "a?b=c", "_install", "_installed", "a#b", "Water", ""]) {
    assert.equal(APPS_PATHS.remove(odd), "/api/apps/_none/remove", odd);
    assert.equal(APPS_PATHS.detail(odd), "/api/apps/_none", odd);
  }
  assert.ok(!ID.test("_none"));
});

test("a plan request names exactly one source, in the server's words", () => {
  assert.deepEqual(planBody({ kind: "folder", path: "my-apps/water" }), { folder: "my-apps/water" });
  assert.deepEqual(planBody({ kind: "git", url: REPO, commit: COMMIT }), { url: REPO, commit: COMMIT });
  assert.deepEqual(planBody({ kind: "example", id: "water" }), { example: "water" });
  assert.deepEqual(planBody(exampleSource({ id: "books_read" })), { example: "books_read" });
});

test("confirming sends back the plan's own id and fingerprint, and the typed name only when it was asked for", () => {
  const plan = readPlan(SERVER_PLAN, { kind: "folder", path: "my-apps/water" });
  assert.deepEqual(confirmBody(plan, "ignored"), { plan: SERVER_PLAN.plan, fingerprint: SERVER_PLAN.fingerprint });
  const full = readPlan({ ...SERVER_PLAN, needs_typed_name: true, app: { ...SERVER_PLAN.app, tier: "full", name: "Drafts" } });
  assert.deepEqual(confirmBody(full, " Drafts "), { plan: SERVER_PLAN.plan, fingerprint: SERVER_PLAN.fingerprint, typed_name: "Drafts" });
  assert.deepEqual(cancelBody(plan), { plan: SERVER_PLAN.plan });
});

test("removing says only whether the data is kept", () => {
  assert.deepEqual(removeBody({ keepData: true }), { keep_data: true });
  assert.deepEqual(removeBody({ keepData: false }), { keep_data: false });
});

test("the server's plan is read: its id, what it asks for, what it adds and where it is from", () => {
  const plan = readPlan(SERVER_PLAN, { kind: "folder", path: "my-apps/water" });
  assert.equal(plan.plan, SERVER_PLAN.plan);
  assert.deepEqual(plan.source, { kind: "folder", path: "my-apps/water" });
  assert.deepEqual(plan.permissions.map((line) => line.sentence), ["Add tasks, and mark the ones it added as done.", "Keep its own data on this computer."]);
  assert.equal(plan.needs_typed_name, false);
  const view = planView(plan);
  assert.equal(view.blocked, null);
  assert.ok(view.creates.some((line) => line.includes("“Water”")));
  assert.ok(view.creates.some((line) => line.includes("“Glasses a day”")));
  assert.ok(!view.creates.some((line) => line.includes("schedule")));
});

test("a git plan keeps the commit the server pinned, whatever was typed", () => {
  const typed = { kind: "git", url: REPO, commit: "0".repeat(40) } as const;
  const plan = readPlan({ ...SERVER_PLAN, source: { ...SERVER_PLAN.source, kind: "git", repo: REPO, commit: COMMIT, ref: "main" } }, typed);
  assert.deepEqual(plan.source, { kind: "git", url: REPO, commit: COMMIT });
  assert.equal(planView(plan).commit, COMMIT);
});

test("an example's plan says it is an example, and a plan without an id cannot be confirmed", () => {
  const plan = readPlan({ ...SERVER_PLAN, described: true, source: { ...SERVER_PLAN.source, example: "books_read" } });
  assert.deepEqual(plan.source, { kind: "example", id: "books_read" });
  assert.equal(plan.described, true);
  assert.match(planView(plan).sourceLine, /folder on this computer/);
  const bundled = readPlan({ ...SERVER_PLAN, provenance: "bundled", source: { ...SERVER_PLAN.source, example: "books_read" } });
  assert.equal(bundled.provenance, "bundled");
  assert.match(planView(bundled).sourceLine, /^JimmyHQ itself/);
  const { plan: _gone, ...noId } = SERVER_PLAN;
  assert.throws(() => readPlan(noId), /no id/);
});

test("a full app that needs its name typed is read as one, and schedule becomes background work", () => {
  const plan = readPlan({ ...SERVER_PLAN, needs_typed_name: true, app: { ...SERVER_PLAN.app, tier: "full", name: "Drafts" }, adds: { ...SERVER_PLAN.adds, schedule: true } });
  const view = planView(plan);
  assert.equal(view.nameToType, "Drafts");
  assert.ok(view.creates.includes("Work that runs in the background on a schedule"));
});

test("the server's detail is read: the consent's sentences, the source, the newest consent and the space used", () => {
  const app = readApp(SERVER_DETAIL);
  assert.deepEqual(app.permissions?.map((line) => line.id), ["tasks.write", "always.data"]);
  assert.deepEqual(app.source, { kind: "git", repo: REPO, commit: COMMIT, path: null });
  assert.equal(app.installed_ms, 1795000000000);
  assert.deepEqual(app.consent, { granted_ms: 1795000000000, version: "1.0.0", permissions: ["tasks.write"] });
  assert.equal(app.data_bytes, 4096);
  assert.deepEqual(app.screens, [{ id: "main", label: "Water", entry: undefined }]);
  assert.equal(app.attention, null);
  assert.equal(app.described, false);
});

test("the list's rows are read without the detail's parts, and a broken app says why", () => {
  const { lines: _lines, consents: _consents, adds: _adds, storage_bytes: _bytes, ...row } = SERVER_DETAIL;
  const books = { ...row, id: "books_read", name: "Books read", described: true, permissions: [], screens: [{ id: "main", label: "Books read", described: true }], broken: "Its files are missing." };
  const [water, read] = readApps({ apps: [row, books] });
  assert.deepEqual(water?.permissions?.map((line) => line.id), ["tasks.write"]);
  assert.equal(read?.described, true);
  assert.equal(read?.screens?.[0]?.entry, "described");
  assert.equal(read?.attention, "Its files are missing.");
});

test("the server's refusals are one sentence, and the one about unreviewed full apps points at settings", () => {
  const setting = readRefusal(403, { detail: "You cannot install this app from a URL: it is a full app that has not been reviewed, and Settings, Apps has that switched off. Nothing was installed." });
  assert.equal(setting.kind, "setting");
  const manifest = readRefusal(422, { detail: "You cannot use this app as it is, and nothing was installed or changed. Fix its jimmyhq.app.json: \"id\" is missing." });
  assert.equal(manifest.kind, "other");
  assert.match(manifest.message, /Fix its jimmyhq\.app\.json/);
});

test("only the server's own word makes an app JimmyHQ's: an unknown or missing provenance reads as its source", () => {
  assert.equal(readPlan({ ...SERVER_PLAN, provenance: "Bundled" }).provenance, "folder");
  assert.equal(readPlan({ ...SERVER_PLAN, provenance: undefined }).provenance, "folder");
  assert.equal(readPlan({ ...SERVER_PLAN, provenance: "bundled", source: { ...SERVER_PLAN.source, kind: "git", repo: REPO, commit: COMMIT } }).provenance, "bundled");
  assert.equal(readApp(SERVER_DETAIL).provenance, "git");
  assert.equal(readApp({ ...SERVER_DETAIL, provenance: "bundled", source: "folder" }).provenance, "bundled");
  assert.equal(readApp({ ...SERVER_DETAIL, provenance: null, source: "folder" }).provenance, "folder");
});

test("the app's page shows the server's sentences about what the app expects of a host, and nothing else", () => {
  assert.deepEqual(readApp({ ...SERVER_DETAIL, net_notes: ["It expects more.", 5, "", { html: "<b>" }, "x".repeat(900)] }).net_notes, ["It expects more.", "x".repeat(500)]);
  assert.deepEqual(readApp(SERVER_DETAIL).net_notes, []);
  assert.deepEqual(noteLines("one sentence"), []);
  assert.equal(noteLines(new Array(100).fill("a")).length, 32);
});
