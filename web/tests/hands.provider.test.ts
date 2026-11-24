import assert from "node:assert/strict";
import { test } from "node:test";
import { addressField, argumentProblem, linesOf, missingSecrets, optionFields, optionsFromValues, sameOptions, secretFor, shapesOf, startShape, valuesFromOptions } from "../src/modules/hands/lib/provider.ts";
import { field, providerType, serverType } from "./hands.fixtures.ts";

const names = (fields: { name: string }[]) => fields.map((item) => item.name);

test("a type with two shapes shows the fields of the one chosen, and a plain type shows all of its own", () => {
  const server = serverType();
  assert.deepEqual(shapesOf(server), ["program", "address"]);
  assert.deepEqual(names(optionFields(server, "program")), ["command", "args", "env", "max_tools"]);
  assert.deepEqual(names(optionFields(server, "address")), ["headers", "max_tools"]);
  assert.equal(addressField(server, "address")?.label, "Address");
  assert.equal(addressField(server, "program"), null);
  const hosted = providerType({ fields: [field({ name: "base_url", where: "record", label: "Address (optional)" }), field({ name: "user_id" })] });
  assert.deepEqual(shapesOf(hosted), []);
  assert.deepEqual(names(optionFields(hosted, null)), ["user_id"]);
  assert.equal(addressField(hosted, null)?.label, "Address (optional)");
  assert.deepEqual(optionFields(undefined, null), []);
});

test("a saved record opens in its own shape and a new one in the first on offer", () => {
  const server = serverType();
  assert.equal(startShape(server, null), "program");
  assert.equal(startShape(server, { base_url: "https://mcp.example.com/mcp" }), "address");
  assert.equal(startShape(server, { base_url: null }), "program");
  assert.equal(startShape(providerType(), null), null);
});

test("saved options fill the boxes, a list one item to a line", () => {
  const server = serverType();
  const values = valuesFromOptions(optionFields(server, "program"), { command: "npx", args: ["-y", "@example/server", "--root", "My Notes"], env: ["EXAMPLE_API_TOKEN"], max_tools: 50 });
  assert.deepEqual(values, { command: "npx", args: "-y\n@example/server\n--root\nMy Notes", env: "EXAMPLE_API_TOKEN", max_tools: "50" });
  assert.deepEqual(valuesFromOptions(optionFields(server, "program"), undefined), { command: "", args: "", env: "", max_tools: "" });
});

test("the boxes become options: lists by line, numbers as numbers, empty boxes left out", () => {
  const server = serverType();
  const built = optionsFromValues(server, "program", { command: " npx ", args: "-y\r\n\n@example/server\nC:\\My Notes\\", env: "EXAMPLE_API_TOKEN\n", max_tools: "" }, undefined);
  assert.deepEqual(built, { ok: true, options: { command: "npx", args: ["-y", "@example/server", "C:\\My Notes\\"], env: ["EXAMPLE_API_TOKEN"] } });
  assert.deepEqual(linesOf("a\n\n b \n"), ["a", " b "]);
});

test("a saved option this screen has no box for is kept, and one of the other shape is removed", () => {
  const server = serverType();
  const saved = { command: "npx", args: ["x"], idle_s: 60, transport: "stdio" };
  const built = optionsFromValues(server, "address", { headers: "Authorization", max_tools: "20" }, saved);
  assert.deepEqual(built, { ok: true, options: { idle_s: 60, transport: "stdio", headers: ["Authorization"], max_tools: 20 } });
});

test("what the server would refuse about a field is said before saving", () => {
  const server = serverType();
  const wrong = optionsFromValues(server, "program", { command: "", args: "--token=abc123", env: "EXAMPLE TOKEN", max_tools: "many" }, undefined);
  assert.equal(wrong.ok, false);
  if (wrong.ok) return;
  assert.match(wrong.errors.command!, /Program is needed/);
  assert.match(wrong.errors.args!, /"--token" names a key.*\{secret:NAME\}/);
  assert.match(wrong.errors.env!, /Names only/);
  assert.match(wrong.errors.max_tools!, /number above 0/);
  const twice = optionsFromValues(server, "program", { command: "npx", env: "A\na" }, undefined);
  assert.ok(!twice.ok && /twice/.test(twice.errors.env!));
});

test("an argument that names a secret must refer to it, and a long path is just a path", () => {
  assert.equal(argumentProblem("--token={secret:api_token}"), null);
  assert.equal(argumentProblem("/srv/example/a-very-long-folder-name/with/many/parts/notes"), null);
  assert.equal(argumentProblem("--port=8440"), null);
  assert.match(argumentProblem("api_key=abc")!, /written out/);
  assert.match(argumentProblem("{secret:Not A Name}")!, /\{secret:NAME\}/);
});

test("unchanged options are recognised whatever order their names are in", () => {
  assert.equal(sameOptions({ a: 1, b: ["x"] }, { b: ["x"], a: 1 }), true);
  assert.equal(sameOptions({ a: 1 }, { a: 2 }), false);
  assert.equal(sameOptions(undefined, {}), true);
});

test("the secrets a record names and has not saved yet are listed for the owner", () => {
  assert.deepEqual(missingSecrets({ secrets: ["env_example_api_token"], secrets_wanted: ["api_token", "env_example_api_token"] }), ["api_token"]);
  assert.deepEqual(missingSecrets({ secrets: [], secrets_wanted: [] }), []);
  assert.equal(secretFor(field({ name: "env", kind: "names", secret_prefix: "env_" }), " EXAMPLE_API_TOKEN "), "env_example_api_token");
});
