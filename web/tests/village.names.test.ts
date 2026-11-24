import assert from "node:assert/strict";
import { test } from "node:test";
import { agentLabel, labelForId, workingName } from "../src/modules/village/lib/names.ts";
import { appliesTo } from "../src/modules/village/lib/priorities.ts";
import { whoIs } from "../src/modules/village/lib/room.ts";

test("a working name comes from the id: a letter or a number after agent", () => {
  assert.equal(workingName("agent-a"), "Agent A");
  assert.equal(workingName("agent-27"), "Agent 27");
  assert.equal(workingName("odd id"), "odd id");
  assert.equal(workingName(""), "New agent");
});

test("before it picks a name the label is its working name, its role and a quiet note", () => {
  assert.deepEqual(agentLabel({ id: "agent-a", name: null, role: "project manager" }), {
    title: "Agent A",
    role: "Project manager",
    note: "has not chosen a name yet",
    named: false,
  });
  assert.equal(agentLabel({ id: "agent-a", name: "  ", role: "" }).title, "Agent A");
  assert.equal(agentLabel({ id: "agent-a", name: "  ", role: "" }).role, null);
});

test("once it has chosen a name that is the title, and there is no note", () => {
  const label = agentLabel({ id: "agent-a", name: "Wren", role: "project manager" });
  assert.equal(label.title, "Wren");
  assert.equal(label.note, null);
  assert.equal(label.named, true);
});

test("an agent the roster no longer lists is still shown by its working name", () => {
  const everyone = [{ id: "agent-a", name: "Wren", role: "" }];
  assert.equal(labelForId("agent-a", everyone), "Wren");
  assert.equal(labelForId("agent-b", everyone), "Agent B");
});

test("the same agent has the same label in the break room and in priorities", () => {
  const everyone = [{ id: "agent-a", name: null, role: "project manager" }];
  assert.equal(whoIs("agent-a", everyone), "Agent A");
  assert.equal(appliesTo({ agents: ["agent-a"] }, everyone), "Agent A");
  assert.equal(whoIs("agent-z", everyone), "Agent Z");
});
