import assert from "node:assert/strict";
import { test } from "node:test";
import { agentModelProblem, choiceUsable, villageModelProblem } from "../src/modules/village/lib/modelProblem.ts";

const provider = { id: "den", enabled: true, needs_key: false, has_key: false };
const roles = [{ role: "default", primary: { provider_id: "den" } }, { role: "smart", primary: null }];
const config = { beat: { role: "default" }, night: { role: "smart" } };

test("with no model set up, the Village says so and where to set one", () => {
  const said = villageModelProblem([{ role: "default", primary: null }], []);
  assert.match(said ?? "", /No model is set up/);
  assert.match(said ?? "", /Settings, Models and roles/);
  assert.equal(villageModelProblem(roles, [provider]), null);
  assert.equal(villageModelProblem(undefined, undefined), null);
});

test("an agent whose everyday model leads nowhere is told so, and one whose roles fall back to the default is not", () => {
  const none = agentModelProblem(config, [{ role: "default", primary: null }], [provider]);
  assert.match(none ?? "", /No model can answer for this agent/);
  assert.equal(agentModelProblem(config, roles, [provider]), null);
  assert.equal(agentModelProblem(undefined, roles, [provider]), null);
});

test("a model that needs a key it has not got, or a provider switched off, does not count", () => {
  assert.equal(choiceUsable({ role: "default" }, roles, [{ ...provider, needs_key: true }]), false);
  assert.equal(choiceUsable({ role: "default" }, roles, [{ ...provider, enabled: false }]), false);
  assert.equal(choiceUsable({ provider_id: "den", model: "small" }, [], [provider]), true);
  assert.equal(choiceUsable({ provider_id: "gone", model: "small" }, roles, [provider]), false);
});
