import assert from "node:assert/strict";
import { test } from "node:test";
import { readApp, readApps, readPlan, readRefusal, refusalKind } from "../src/modules/apps/lib/wire.ts";

test("the manifest parser's problems arrive as a list of field and sentence pairs", () => {
  const body = {
    detail: {
      message: "This app's manifest has 2 problems.",
      errors: [
        { field: "version", message: "\"version\" must look like 1.2.3. Write it that way." },
        { field: "permissions", message: "\"tasks.write\" is listed twice. List it once." },
      ],
    },
  };
  const refusal = readRefusal(422, body);
  assert.equal(refusal.kind, "manifest");
  assert.equal(refusal.message, "This app's manifest has 2 problems.");
  assert.deepEqual(refusal.problems, [
    { field: "version", message: "\"version\" must look like 1.2.3. Write it that way." },
    { field: "permissions", message: "\"tasks.write\" is listed twice. List it once." },
  ]);
});

test("problems written as [field, sentence] pairs are read the same way", () => {
  const refusal = readRefusal(422, { errors: [["id", "\"id\" must start with a letter."], ["tier", "\"tier\" must be sandboxed or full."]] });
  assert.deepEqual(refusal.problems.map((problem) => problem.field), ["id", "tier"]);
  assert.equal(refusal.kind, "manifest");
});

test("problems as the whole detail are read, with the field taken from where the server says it was", () => {
  const refusal = readRefusal(422, { detail: [{ loc: ["body", "source", "commit"], msg: "Type the full commit." }] });
  assert.deepEqual(refusal.problems, [{ field: "source.commit", message: "Type the full commit." }]);
});

test("a refusal that is only a sentence stays a sentence with no list", () => {
  const refusal = readRefusal(404, { detail: "There is no folder there. Check the path." });
  assert.equal(refusal.message, "There is no folder there. Check the path.");
  assert.deepEqual(refusal.problems, []);
  assert.equal(refusal.kind, "other");
});

test("a refusal with a list but no sentence gets a sentence that counts the problems", () => {
  assert.equal(readRefusal(422, { errors: [{ field: "a", message: "x" }] }).message, "This app's manifest has a problem.");
  assert.equal(readRefusal(422, { errors: [{ field: "a", message: "x" }, { field: "b", message: "y" }] }).message, "This app's manifest has 2 problems.");
});

test("a reply that is not JSON at all still gives a sentence that says what failed", () => {
  assert.equal(readRefusal(500, null).message, "Request failed (500).");
});

test("the setting that forbids unreviewed full apps from a URL is recognised by its code and keeps the server's sentence", () => {
  const sentence = "Your settings do not allow installing a full app that nobody has reviewed from a web address. Nothing was installed.";
  const refusal = readRefusal(403, { detail: { message: sentence, code: "unreviewed_full_forbidden" } });
  assert.equal(refusal.kind, "setting");
  assert.equal(refusal.message, sentence);
});

test("the same refusal is recognised without a code when it is a 403 that says unreviewed", () => {
  assert.equal(refusalKind(403, null, false, "A full app that is unreviewed cannot be installed from a URL."), "setting");
  assert.equal(refusalKind(409, null, false, "Unreviewed full apps are off."), "setting");
});

test("a 403 that is about something else is not taken for the setting", () => {
  assert.equal(refusalKind(403, null, false, "Only the owner can install apps."), "other");
});

test("manifest problems win over a code that mentions a setting", () => {
  assert.equal(refusalKind(422, "bad_setting_type", true, "x"), "manifest");
});

test("an app arrives bare or wrapped, and a missing optional field is a default, not a failure", () => {
  const bare = readApp({ id: "water", name: "Water", version: "1.0.0", tier: "sandboxed", enabled: true });
  assert.equal(bare.enabled, true);
  assert.deepEqual(bare.permissions, []);
  assert.equal(bare.data_bytes, null);
  assert.equal(bare.consent, null);
  assert.equal(readApp({ app: { id: "water" } }).id, "water");
});

test("an app with no id cannot be read, and a list skips it instead of failing as a whole", () => {
  assert.throws(() => readApp({ name: "x" }), /without an id/);
  assert.deepEqual(readApps({ apps: [{ name: "x" }, { id: "water" }] }).map((app) => app.id), ["water"]);
  assert.deepEqual(readApps([{ id: "water" }]).map((app) => app.id), ["water"]);
  assert.throws(() => readApps({ nope: 1 }), /cannot read/);
});

test("an app that says nothing about its tier is treated as the cautious sandboxed kind and not as full", () => {
  assert.equal(readApp({ id: "water" }).tier, "sandboxed");
  assert.equal(readApp({ id: "water", tier: "surprise" }).tier, "sandboxed");
  assert.equal(readApp({ id: "water", tier: "full" }).tier, "full");
});

test("an app is off unless the server says it is on", () => {
  assert.equal(readApp({ id: "water" }).enabled, false);
  assert.equal(readApp({ id: "water", enabled: "yes" }).enabled, false);
});

test("permission lines keep the server's sentence and mark strong and always lines", () => {
  const app = readApp({
    id: "drafts",
    permissions: [
      { id: "always.full", permission: "always.full", sentence: "Runs as you.", strong: true, always: true },
      { id: "net:api.example.com", permission: "net", host: "api.example.com", sentence: "Send data to api.example.com." },
      "tasks.read",
      { id: "no-sentence" },
    ],
  });
  assert.equal(app.permissions?.length, 3);
  assert.equal(app.permissions?.[0]?.strong, true);
  assert.equal(app.permissions?.[1]?.host, "api.example.com");
  assert.equal(app.permissions?.[2]?.sentence, "tasks.read");
});

test("a plan with no fingerprint is refused, because step two could not install what was shown", () => {
  assert.throws(() => readPlan({ source: { kind: "folder", path: "x" }, manifest: { id: "water" } }), /no fingerprint/);
});

test("a plan with no source is refused for the same reason", () => {
  assert.throws(() => readPlan({ plan: "01JD", fingerprint: "abc", manifest: { id: "water" } }), /no source/);
  assert.throws(() => readPlan({ fingerprint: "abc", source: { kind: "folder" }, manifest: { id: "water" } }), /no id/);
});

test("a plan is read from a manifest or an app key, and step two sends back the plan id and fingerprint it holds", () => {
  const raw = {
    plan: "01JD0000000000000000000000",
    fingerprint: "abc",
    source: { kind: "git", url: "https://git.example.com/a.git", commit: "c".repeat(40) },
    manifest: { id: "water", name: "Water", version: "1.0.0", tier: "sandboxed", author: "Example Author" },
    permissions: [{ id: "tasks.write", sentence: "Add tasks, and mark the ones it added as done." }],
    installed: { version: "0.9.0" },
  };
  const plan = readPlan(raw);
  assert.equal(plan.fingerprint, "abc");
  assert.equal(plan.plan, raw.plan);
  assert.deepEqual(plan.source, raw.source);
  assert.equal(plan.app.author, "Example Author");
  assert.equal(plan.installed_version, "0.9.0");
  assert.equal(plan.reviewed, false);
  assert.equal(readPlan({ ...raw, manifest: undefined, app: raw.manifest }).app.name, "Water");
});
