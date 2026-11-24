// An app's secrets on its own Settings page: where they are sent, what is read back, and
// that a value is never kept or shown.

import assert from "node:assert/strict";
import { test } from "node:test";
import { appIdOfSection, cleanSecret, clearQuestion, readSecrets, secretBody, secretPath, secretProblem, secretsPath, statusWord } from "../src/modules/apps/lib/secrets.ts";
import { storageSentence } from "../src/modules/apps/lib/wire.ts";

const KEY = "sk-test-SECRET-0123456789";

test("the extra fields are drawn only on an installed app's own page", () => {
  assert.equal(appIdOfSection("app.posts"), "posts");
  for (const other of ["apps", "apps_limits", "app.posts.auto_update", "app.", "app.Posts", "app.../x", "models"]) assert.equal(appIdOfSection(other), null);
});

test("a secret is sent only to its own app's address, and only by a declared-looking name", () => {
  assert.equal(secretsPath("posts"), "/api/apps/posts/secrets");
  assert.equal(secretPath("posts", "booking_key"), "/api/apps/posts/secrets/booking_key");
  for (const [app, name] of [["../x", "key"], ["posts", "../key"], ["posts", "Key"], ["posts", "key/x"], ["posts", ""], ["_installed", "key"]]) {
    assert.equal(secretPath(app as string, name as string), null);
  }
});

test("what the server lists is read as set or not set, and a value it never sends is never kept", () => {
  const read = readSecrets({ secrets: [{ name: "booking_key", label: "Booking key", help: "From your account.", set: true, value: KEY }, { name: "other", set: "yes" }, { name: "../x" }, null] });
  assert.deepEqual(read, [
    { name: "booking_key", label: "Booking key", help: "From your account.", set: true },
    { name: "other", label: "other", help: "", set: false },
  ]);
  assert.equal(JSON.stringify(read).includes(KEY), false);
  assert.deepEqual(readSecrets(null), []);
  assert.equal(statusWord({ set: true }), "Set");
  assert.equal(statusWord({ set: false }), "Not set");
});

test("a pasted secret loses the spaces around it and an empty or many-line one is refused", () => {
  assert.equal(cleanSecret(`  ${KEY}\n`), KEY);
  assert.deepEqual(secretBody(` ${KEY} `), { value: KEY });
  assert.equal(secretProblem(KEY), null);
  for (const bad of ["", "   ", `${KEY}\nmore`, `a\tb`]) {
    const problem = secretProblem(bad);
    assert.ok(problem && problem.includes("Nothing was saved"));
    assert.equal(problem?.includes(KEY), false);
  }
  assert.match(secretProblem("x".repeat(20), 16) ?? "", /at most 16 characters/);
});

test("Clear asks first, in plain words, naming the app", () => {
  const question = clearQuestion("Posts", { label: "Booking key" });
  assert.equal(question.title, "Clear Booking key?");
  assert.match(question.message, /^Posts will no longer have it\./);
});

test("an app's ask for more storage is shown as the server's one sentence", () => {
  assert.equal(storageSentence({ asked_mb: 500, usual_mb: 50, sentence: "Keep up to 500 MB." }), "Keep up to 500 MB.");
  for (const odd of [null, undefined, {}, { sentence: 5 }, "500"]) assert.equal(storageSentence(odd), null);
});
