import assert from "node:assert/strict";
import { test } from "node:test";
import { EXAMPLES, FIRST_PARTY_LIST, exampleSource, notInstalled, readFirstParty } from "../src/modules/apps/lib/examples.ts";

// What `GET /api/apps/_examples` sends: the apps that ship, each with its group.
const SHIPPED = {
  examples: [
    { id: "water", name: "Water", summary: "Count glasses.", tier: "sandboxed", described: false, group: "example", installed: false },
    { id: "alpha", name: "Alpha", summary: "The first one.", tier: "full", described: false, group: "first_party", installed: false },
    { id: "beta", name: "Beta", summary: "The second one.", tier: "full", described: false, group: "first_party", installed: true },
    { id: "gamma", name: "Gamma", summary: "Only a description.", tier: "sandboxed", described: true, group: "first_party", installed: false },
  ],
};

test("the first-party apps are read from what the server ships, apart from the examples, in the order sent", () => {
  assert.equal(FIRST_PARTY_LIST.heading, "Made by the JimmyHQ project");
  const apps = readFirstParty(SHIPPED);
  assert.deepEqual(apps.map((app) => app.id), ["alpha", "beta", "gamma"]);
  const examples = new Set(EXAMPLES.map((e) => e.id));
  assert.ok(apps.every((app) => !examples.has(app.id)));
  assert.deepEqual(apps[0], { id: "alpha", name: "Alpha", tier: "full", blurb: "The first one." });
  assert.equal(apps[2]!.tier, "described");
});

test("an entry the server sends in a shape this screen cannot read is skipped, and a list that is not one is refused", () => {
  const odd = { examples: [null, 7, { id: "x" }, { id: "", name: "No id", group: "first_party" }, { id: "ok", name: "Ok", group: "first_party" }] };
  assert.deepEqual(readFirstParty(odd).map((app) => app.id), ["ok"]);
  assert.deepEqual(readFirstParty({ examples: [] }), []);
  for (const bad of [null, "text", {}, { examples: "no" }]) assert.throws(() => readFirstParty(bad), /shape/);
});

test("a first-party app installs by its id, which the server checks among the apps it ships", () => {
  assert.deepEqual(exampleSource(readFirstParty(SHIPPED)[0]!), { kind: "example", id: "alpha" });
});

test("only the first-party apps not installed yet are offered", () => {
  const apps = readFirstParty(SHIPPED);
  assert.deepEqual(notInstalled(apps, []).map((app) => app.id), ["alpha", "beta", "gamma"]);
  assert.deepEqual(notInstalled(apps, ["water", ...apps.map((app) => app.id)]), []);
  assert.deepEqual(notInstalled(apps, new Set(["water", "alpha", "gamma"])).map((app) => app.id), ["beta"]);
});
