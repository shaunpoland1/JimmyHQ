import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  anythingChosen,
  apartProblem,
  choiceFrom,
  choiceOptions,
  choiceValue,
  choicesBody,
  chosenToast,
  effectWords,
  emptyChoices,
  holderLine,
  newAgentForEach,
  planRolesKey,
  readRolesView,
  sameModelWarnings,
  toolLine,
  toolSummary,
} from "../src/modules/apps/lib/roles.ts";
import type { Choices } from "../src/modules/apps/lib/roles.ts";

const RAW = {
  roles: [
    { id: "writer", label: "Writer", purpose: "Drafts posts.", charter: "# Writer", tools: [{ name: "app_posts_submit_draft", description: "Submit.", effect: "record" }], model: "smart", not_with: ["checker"], optional: true, holder: { agent_id: "agent-a", label: "Wren" }, open_work: 2 },
    { id: "checker", label: "Checker", purpose: "Checks drafts.", charter: null, tools: [], model: "smart", not_with: ["writer"], optional: false, holder: null },
    { id: "researcher", label: "Researcher", purpose: "Finds articles.", charter: "# R", tools: [], model: "cheap", not_with: [] },
    { label: "no id, dropped" },
  ],
  agents: [
    { id: "agent-a", label: "Wren", role: "helper", model: "role:smart" },
    { id: "agent-b", label: "Moss", role: "", model: "role:smart" },
    { id: "agent-c", label: "Fern", role: "", model: "role:cheap" },
  ],
  warnings: ["a warning", 3],
};

const view = readRolesView(RAW);
const roles = view.roles;

test("what the server sends is read defensively, and a role without an id is dropped", () => {
  assert.deepEqual(roles.map((r) => r.id), ["writer", "checker", "researcher"]);
  assert.equal(roles[0]?.holder?.label, "Wren");
  assert.equal(roles[1]?.charter, null);
  assert.equal(roles[1]?.optional, false);
  assert.deepEqual(view.warnings, ["a warning"]);
  assert.deepEqual(readRolesView(null), { roles: [], agents: [], warnings: [] });
});

test("every role starts empty, so installing changes no agent unless the owner chooses", () => {
  const choices = emptyChoices(roles);
  assert.equal(anythingChosen(choices), false);
  assert.deepEqual(Object.values(choices).map((c) => c.mode), ["empty", "empty", "empty"]);
});

test("creating an agent for each role never puts two roles kept apart on one agent", () => {
  const choices = newAgentForEach(roles);
  assert.equal(anythingChosen(choices), true);
  assert.equal(apartProblem("Posts", roles, choices), null);
});

test("one agent chosen for two roles kept apart is stopped with a sentence, by an agent or a shared new one", () => {
  const same: Choices = { writer: { mode: "existing", agentId: "agent-b" }, checker: { mode: "existing", agentId: "agent-b" } };
  assert.match(apartProblem("Posts", roles, same) ?? "", /cannot be both Checker and Writer/);
  const shared: Choices = { writer: { mode: "new", group: "writer" }, checker: { mode: "new", group: "writer" } };
  assert.notEqual(apartProblem("Posts", roles, shared), null);
  const fine: Choices = { writer: { mode: "existing", agentId: "agent-b" }, researcher: { mode: "existing", agentId: "agent-b" } };
  assert.equal(apartProblem("Posts", roles, fine), null);
});

test("a role kept apart is never offered the same new agent as its pair", () => {
  const choices = newAgentForEach(roles);
  const forChecker = choiceOptions(roles[1]!, roles, view.agents, choices).map((o) => o.label);
  assert.ok(!forChecker.includes("The same new agent as Writer"));
  assert.ok(forChecker.includes("The same new agent as Researcher"));
  assert.ok(forChecker[0]?.startsWith("Leave it empty"));
  assert.ok(forChecker.includes("Wren (helper)"));
});

test("two roles kept apart on the same model are warned about, not refused", () => {
  const choices: Choices = { writer: { mode: "existing", agentId: "agent-a" }, checker: { mode: "existing", agentId: "agent-b" } };
  const [warning] = sameModelWarnings("Posts", roles, view.agents, choices);
  assert.match(warning ?? "", /same model/);
  const apart: Choices = { writer: { mode: "existing", agentId: "agent-a" }, checker: { mode: "existing", agentId: "agent-c" } };
  assert.deepEqual(sameModelWarnings("Posts", roles, view.agents, apart), []);
  assert.equal(sameModelWarnings("Posts", roles, view.agents, newAgentForEach(roles)).length, 1);
});

test("a choice goes to the server in its own words and round-trips through the select", () => {
  const choices: Choices = { writer: { mode: "existing", agentId: "agent-a" }, checker: { mode: "new", group: "checker" }, researcher: { mode: "empty" } };
  assert.deepEqual(choicesBody(choices), {
    choices: { writer: { mode: "existing", agent_id: "agent-a" }, checker: { mode: "new", group: "checker" }, researcher: { mode: "empty" } },
  });
  for (const choice of Object.values(choices)) assert.deepEqual(choiceFrom(choiceValue(choice)), choice);
});

test("the panel says who holds a role and how much work is with them, in plain words", () => {
  assert.equal(holderLine(roles[0]!), "Held by Wren. 2 pieces of work are with them.");
  assert.equal(holderLine(roles[1]!), "Empty. The app asks for this role to be filled.");
  assert.equal(holderLine(roles[2]!), "Empty. The app does this part without an agent.");
  assert.equal(effectWords("record"), "writes into the app's own records only");
  assert.match(effectWords("send"), /ladder/);
});

test("the toast after choosing says what happened, with no word for a turn as a beat", () => {
  const said = chosenToast("Posts", { filled: [{}], creating: [{}, {}] });
  assert.equal(said, "1 role was given, and 2 agents are being created in the Village for Posts.");
  assert.doesNotMatch(said, /beat/i);
  assert.equal(chosenToast("Posts", {}), "The roles of Posts were updated.");
});

test("a role's tool is listed by what it does and its effect, with the raw name only as small print", () => {
  const line = toolLine({ name: "app_drafts_submit_draft", description: "Hands back a draft. Each claim names its sentences.", effect: "record" });
  assert.deepEqual(line, { summary: "Hands back a draft.", effect: "writes into the app's own records only", name: "app_drafts_submit_draft" });
  assert.equal(toolLine({ name: "app_x_y", description: "", effect: "read" }).summary, "app_x_y");
});

test("a tool's one line is its description's first sentence, never cut inside a number or a name", () => {
  assert.equal(toolSummary("Shows 1.5 times the figures. Then more."), "Shows 1.5 times the figures.");
  assert.equal(toolSummary("  Lists   the pages  "), "Lists the pages.");
  assert.ok(toolSummary("Word ".repeat(60)).length <= 160);
});

test("each effect says in plain words what the tool does, and anything beyond the app's records waits for the ladder", () => {
  assert.equal(effectWords("read"), "only reads");
  assert.equal(effectWords("send"), "sends something out: waits for your say on the ladder");
  assert.equal(effectWords("delete"), "deletes something: waits for your say on the ladder");
  assert.match(effectWords("something-new"), /^changes something: .*ladder/);
});

test("each of Posts' tools reads to the owner in one short line, about the tool and not to the agent", () => {
  const manifest = JSON.parse(readFileSync(new URL("../../apps/posts/jimmyhq.app.json", import.meta.url), "utf8"));
  for (const tool of manifest.tools) {
    const line = toolSummary(tool.description);
    assert.ok(line.length <= 100, `${tool.name}: ${line}`);
    assert.doesNotMatch(line, /\byou(r)?\b/i, `${tool.name} speaks to the agent: ${line}`);
    assert.match(line, /^[A-Z][a-z]+s\b/, `${tool.name} does not start with what it does: ${line}`);
  }
});

test("a waiting install's roles are kept outside the apps queries, so installing does not ask for a plan that is gone", () => {
  const key = planRolesKey("plan-1");
  assert.notEqual(key[0], "apps");
  assert.deepEqual(key, ["install-plan-roles", "plan-1"]);
});
