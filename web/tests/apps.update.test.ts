import assert from "node:assert/strict";
import { test } from "node:test";
import { APPS_PATHS, removeBody } from "../src/modules/apps/lib/requests.ts";
import { DELETE_WARNING, RECORD_ERASED, badges } from "../src/modules/apps/lib/status.ts";
import {
  canUpdate,
  pendingSentence,
  readUpdate,
  readUpdatePlan,
  readVersions,
  rollbackBody,
  rollbackChoices,
  rollbackLocalSentence,
  rollbackSentence,
  updateConfirmBody,
  updatePlanBody,
  updateWords,
} from "../src/modules/apps/lib/update.ts";
import { readApp } from "../src/modules/apps/lib/wire.ts";

const COMMIT = "3f2a9c1e5b7d4a6c8e0f1a2b3c4d5e6f70819203";
const NET = { id: "net:api.example.com", permission: "net", host: "api.example.com", sentence: "Send data to and read data from api.example.com.", strong: false, always: false };

// What server/jimmyhq/apps/update.py `_plan_update` sends, trimmed to what the screen reads.
const SERVER_UPDATE_PLAN = {
  plan: "01JD3V7Q2M8K4N6P0R5T9W1XYZ",
  app: { id: "water", name: "Water", version: "1.1.0", tier: "sandboxed" },
  needs_typed_name: false,
  warning: null,
  fingerprint: "9f".repeat(32),
  permissions: [NET, { id: "tasks.write", permission: "tasks.write", host: null, sentence: "Add tasks, and mark the ones it added as done." }],
  update: { app_id: "water", from_version: "1.0.0", from_tree: "5d".repeat(32), ask: [NET], removed: [], tier: null, needs_consent: true },
};

const VERSIONS = [
  { name: "1.1.0-0a1b2c3d", version: "1.1.0", tree: "0a1b2c3d".repeat(8), used_at: 1795000500000, current: true, has_backup: false, backup_at: null, permissions: [] },
  { name: "1.0.0-5d5d5d5d", version: "1.0.0", tree: "5d".repeat(32), used_at: 1795000000000, current: false, has_backup: true, backup_at: 1795000400000, permissions: [] },
];

test("an update plan lists only what is new, and its button says the owner is agreeing", () => {
  const plan = readUpdatePlan(SERVER_UPDATE_PLAN);
  assert.deepEqual(plan.ask.map((line) => line.id), ["net:api.example.com"]);
  const words = updateWords(plan);
  assert.equal(words.title, "Update Water to 1.1.0?");
  assert.equal(words.newHeading, "This version asks for more. Only what is new is listed:");
  assert.equal(words.button, "Agree and update");
  assert.match(words.intro, /roll back to 1\.0\.0/);
});

test("an update that asks for nothing new says so plainly", () => {
  const plan = readUpdatePlan({ ...SERVER_UPDATE_PLAN, update: { ...SERVER_UPDATE_PLAN.update, ask: [], needs_consent: false } });
  const words = updateWords(plan);
  assert.equal(words.newHeading, "It asks for nothing new.");
  assert.equal(words.button, "Update");
});

test("an update is never shown as asking for nothing when the server listed something", () => {
  const plan = readUpdatePlan({ ...SERVER_UPDATE_PLAN, update: { ...SERVER_UPDATE_PLAN.update, needs_consent: false } });
  assert.equal(plan.needsConsent, true);
  const tier = readUpdatePlan({ ...SERVER_UPDATE_PLAN, update: { ...SERVER_UPDATE_PLAN.update, ask: [], needs_consent: false, tier: ["sandboxed", "full"] } });
  assert.equal(tier.needsConsent, true);
  assert.equal(updateWords(tier).tier, "It changes from a sandboxed app to a full app.");
});

test("an update that becomes a full app waits for its name to be typed", () => {
  const plan = readUpdatePlan({ ...SERVER_UPDATE_PLAN, needs_typed_name: true });
  assert.equal(canUpdate(plan, ""), false);
  assert.equal(canUpdate(plan, " Water "), true);
  assert.deepEqual(updateConfirmBody(plan, " Water "), { plan: SERVER_UPDATE_PLAN.plan, fingerprint: SERVER_UPDATE_PLAN.fingerprint, typed_name: "Water" });
  assert.deepEqual(updateConfirmBody(readUpdatePlan(SERVER_UPDATE_PLAN), "x"), { plan: SERVER_UPDATE_PLAN.plan, fingerprint: SERVER_UPDATE_PLAN.fingerprint });
});

test("an update plan the screen cannot read is refused rather than guessed at", () => {
  assert.throws(() => readUpdatePlan({ ...SERVER_UPDATE_PLAN, update: undefined }), /nothing was changed/);
  assert.throws(() => readUpdatePlan({ ...SERVER_UPDATE_PLAN, fingerprint: "" }), /nothing was changed/);
});

test("the update source becomes one body in the server's words", () => {
  assert.deepEqual(updatePlanBody({ kind: "found" }), {});
  assert.deepEqual(updatePlanBody({ kind: "folder", path: " my-apps/water " }), { folder: "my-apps/water" });
  assert.deepEqual(updatePlanBody({ kind: "git", url: "https://git.example.com/apps/water.git", commitOrRef: COMMIT }), {
    url: "https://git.example.com/apps/water.git",
    commit: COMMIT,
  });
  assert.deepEqual(updatePlanBody({ kind: "git", url: "", commitOrRef: "main" }), { ref: "main" });
});

test("roll back offers every kept version but the one in use, and sends its server name", () => {
  const versions = readVersions(VERSIONS);
  const choices = rollbackChoices(versions);
  assert.deepEqual(choices.map((v) => v.name), ["1.0.0-5d5d5d5d"]);
  assert.deepEqual(rollbackBody(choices[0]!, true), { version: "1.0.0-5d5d5d5d", restore_data: true });
  assert.match(rollbackSentence("Water", choices[0]!, true), /data goes back/);
  assert.match(rollbackSentence("Water", choices[0]!, false), /data stays as it is now/);
});

test("a kept version whose name could be a path is not offered", () => {
  const versions = readVersions([...VERSIONS, { name: "../../etc-0a1b2c3d", version: "x" }, { name: "1.0.0", version: "1.0.0" }]);
  assert.deepEqual(versions.map((v) => v.name), ["1.1.0-0a1b2c3d", "1.0.0-5d5d5d5d"]);
});

test("the detail reads the update found, the versions kept and why it runs with JimmyHQ's own Python", () => {
  const app = readApp({
    id: "water", name: "Water", version: "1.1.0", tier: "full", enabled: true,
    update_available: "1.2.0", versions: VERSIONS, environment_note: "Water runs with JimmyHQ's own Python, because uv was not found on this computer.",
    update: { source: "git", repo: "https://git.example.com/apps/water.git", ref: "main", commit: COMMIT, version: "1.2.0", found_at: 1795000600000, needs_consent: true, problem: null },
  });
  assert.equal(app.update?.version, "1.2.0");
  assert.equal(app.update?.needsConsent, true);
  assert.equal(app.versions?.length, 2);
  assert.match(app.environment_note ?? "", /uv was not found/);
  assert.ok(badges(app).includes("Update available: 1.2.0"));
  assert.equal(pendingSentence("Water", app.update ?? null), "Water has version 1.2.0 waiting. It asks for more, so it waits for you.");
});

test("an update found by its commit alone is named by the commit", () => {
  const found = readUpdate({ source: "git", commit: COMMIT, version: null, needs_consent: null, problem: null });
  assert.equal(pendingSentence("Water", found), "Water has a new commit (3f2a9c1e) waiting for you to press Update.");
  assert.equal(readUpdate(null), null);
  assert.equal(readUpdate({ source: "" }), null);
});

test("remove sends whether to erase the record only when the owner chose", () => {
  assert.deepEqual(removeBody({ keepData: true }), { keep_data: true });
  assert.deepEqual(removeBody({ keepData: false, eraseRecord: true }), { keep_data: false, erase_record: true });
  assert.deepEqual(removeBody({ keepData: false, eraseRecord: false }), { keep_data: false, erase_record: false });
  assert.match(DELETE_WARNING, /cannot be undone/);
  assert.match(RECORD_ERASED, /keep their place/);
});

test("every update address is under the app's id, and nothing else is put in one", () => {
  assert.equal(APPS_PATHS.updatePlan("water"), "/api/apps/water/update/plan");
  assert.equal(APPS_PATHS.updateConfirm("water"), "/api/apps/water/update/confirm");
  assert.equal(APPS_PATHS.updateCheck("water"), "/api/apps/water/update/check");
  assert.equal(APPS_PATHS.rollback("water"), "/api/apps/water/rollback");
  assert.equal(APPS_PATHS.rollback("../water"), "/api/apps/_none/rollback");
});

const HOME = "http://192.0.2.10:8000";

test("an update names the addresses on the owner's network it keeps, and those a new tier takes back", () => {
  const keeps = readUpdatePlan({ ...SERVER_UPDATE_PLAN, update: { ...SERVER_UPDATE_PLAN.update, local_addresses: { addresses: [HOME, "javascript:x"], kept: true } } });
  assert.deepEqual(keeps.localAddresses, { addresses: [HOME], kept: true });
  assert.match(updateWords(keeps).ownNetwork ?? "", new RegExp(`keeps the address on your own network you let it reach: ${HOME}`));
  const loses = readUpdatePlan({ ...SERVER_UPDATE_PLAN, update: { ...SERVER_UPDATE_PLAN.update, tier: ["full", "sandboxed"], local_addresses: { addresses: [HOME], kept: "yes" } } });
  assert.equal(loses.localAddresses?.kept, false); // anything but a plain true is never read as kept
  assert.match(updateWords(loses).ownNetwork ?? "", /taken back/);
  assert.equal(updateWords(readUpdatePlan(SERVER_UPDATE_PLAN)).ownNetwork, null);
});

test("a rollback keeps the addresses on the owner's network only for a version of the same tier", () => {
  const [older] = rollbackChoices(readVersions(VERSIONS.map((v) => ({ ...v, tier: "full" }))));
  assert.equal(older?.tier, "full");
  assert.match(rollbackLocalSentence("Water", older!, "full", [HOME]) ?? "", /keeps the address/);
  assert.match(rollbackLocalSentence("Water", older!, "sandboxed", [HOME]) ?? "", /taken back/);
  assert.match(rollbackLocalSentence("Water", { ...older!, tier: null }, "full", [HOME]) ?? "", /taken back/);
  assert.equal(rollbackLocalSentence("Water", older!, "full", []), null);
  assert.deepEqual(readApp({ id: "water", name: "Water", version: "1.1.0", tier: "full", enabled: true, local_addresses: [HOME, "nope"] }).local_addresses, [HOME]);
});
