import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChatMessage, Priority } from "../src/modules/village/api.ts";
import { deliveryNote, groupByDay, lastSeq, mergeMessages, messageProblem, olderCursor, unseenCount, unseenLabel } from "../src/modules/village/lib/chat.ts";
import {
  DEFAULT_BEAT,
  DEFAULT_NIGHT,
  NOT_RUNNING,
  SLOW_AFTER_MS,
  commandProgress,
  describeChoice,
  UNKNOWN_CAPABILITY,
  capabilityLabel,
  capabilityRows,
  localOnlyProblem,
  newAgent,
  parseSeed,
  roleProblem,
  sameConfig,
  toChoice,
  toTarget,
  toggleCapability,
} from "../src/modules/village/lib/commands.ts";
import { appliesTo, forAgent, priorityChange, sortPriorities, tidyTitle, titleProblem } from "../src/modules/village/lib/priorities.ts";

function line(seq: number, sender: "owner" | "agent" = "agent", ts = seq * 1000): ChatMessage {
  return { seq, id: `e${seq}`, ts, sender, text: `line ${seq}` };
}

test("a birth waits, then says it is slow, then reports the result", () => {
  assert.equal(commandProgress({ kind: "birth", state: { done: false, result: null }, elapsedMs: 500, processOk: true }).phase, "waiting");
  assert.equal(commandProgress({ kind: "birth", state: undefined, elapsedMs: SLOW_AFTER_MS, processOk: null }).phase, "slow");
  const born = commandProgress({ kind: "birth", state: { done: true, result: { ok: true, agent_id: "agent-c", tasks: 2 } }, elapsedMs: 900, processOk: true });
  assert.equal(born.phase, "done");
  assert.equal(born.text, "Born as agent-c. It will take its first turn shortly. It has 2 starter tasks to find its feet.");
});

test("a stopped village process is named, with what to do about it", () => {
  const stalled = commandProgress({ kind: "birth", state: { done: false, result: null }, elapsedMs: 100, processOk: false });
  assert.equal(stalled.phase, "stalled");
  assert.equal(stalled.text, NOT_RUNNING);
  assert.match(stalled.text, /Start it with/);
});

test("a refused command shows the village's own sentence", () => {
  const refused = commandProgress({ kind: "birth", state: { done: true, result: { ok: false, error: "The village is full." } }, elapsedMs: 0, processOk: true });
  assert.deepEqual(refused, { phase: "refused", text: "The village is full." });
  const silent = commandProgress({ kind: "retire", state: { done: true, result: { ok: false } }, elapsedMs: 0, processOk: true });
  assert.match(silent.text, /Nothing was changed/);
});

test("a finished command wins over a process that looks stopped", () => {
  assert.equal(commandProgress({ kind: "retire", state: { done: true, result: { ok: true } }, elapsedMs: 0, processOk: false }).phase, "done");
});

test("a seed is optional and must be a whole number in range", () => {
  assert.deepEqual(parseSeed("  "), { ok: true, value: null });
  assert.deepEqual(parseSeed("42"), { ok: true, value: 42 });
  assert.equal(parseSeed("-1").ok, false);
  assert.equal(parseSeed("4.5").ok, false);
  assert.equal(parseSeed("2147483648").ok, false);
  assert.deepEqual(parseSeed("2147483647"), { ok: true, value: 2147483647 });
});

test("a model choice is a role or a provider's model, never a mix", () => {
  assert.deepEqual(toChoice({ kind: "role", role: "smart" }), { role: "smart" });
  assert.deepEqual(toChoice({ kind: "model", provider_id: "p1", model: "m1" }), { provider_id: "p1", model: "m1" });
  assert.deepEqual(toTarget({ provider_id: "p1", model: "m1", role: "default" }, DEFAULT_BEAT), { kind: "model", provider_id: "p1", model: "m1" });
  assert.deepEqual(toTarget({ provider_id: "p1" }, DEFAULT_NIGHT), DEFAULT_NIGHT);
  assert.deepEqual(toTarget(null, DEFAULT_BEAT), DEFAULT_BEAT);
  assert.deepEqual(newAgent(" helper ", null, DEFAULT_BEAT, DEFAULT_NIGHT, true), {
    role: "helper",
    seed: null,
    models: { beat: { role: "default" }, night: { role: "smart" } },
    local_only: true,
  });
});

test("local only warns when a chosen model is hosted", () => {
  const roles = [
    { role: "default", model: "m-small", provider_label: "This machine", local: true },
    { role: "smart", model: "m-big", provider_label: "A hosted service", local: false },
  ];
  const beat = describeChoice(null, DEFAULT_BEAT, roles, []);
  const night = describeChoice(null, DEFAULT_NIGHT, roles, []);
  assert.deepEqual(beat, { label: "The default role", detail: "m-small on This machine", onMachine: true });
  assert.equal(localOnlyProblem(false, beat, night), null);
  assert.match(localOnlyProblem(true, beat, night) ?? "", /^The night model is hosted/);
  assert.equal(localOnlyProblem(true, beat, beat), null);
  assert.equal(describeChoice({ role: "fast" }, DEFAULT_BEAT, roles, []).detail, "not set up yet");
  assert.equal(describeChoice({ provider_id: "gone", model: "m" }, DEFAULT_BEAT, roles, []).onMachine, null);
});

test("a role is checked before it is sent", () => {
  assert.equal(roleProblem("x".repeat(81)), "Keep the role to 80 characters or fewer.");
  assert.equal(roleProblem("researcher"), null);
});

test("what an agent may use is a switch for each capability the server offers", () => {
  const web = { id: "web", label: "Web lookup", description: "Lets it search.", ready: false, problem: "Web lookup is switched off.", settings: "lookup" };
  const plain = { id: "mail", label: "", description: "Lets it send mail." };
  assert.deepEqual(capabilityRows([web, plain], []), [
    { id: "web", label: "Web lookup", description: "Lets it search.", on: false, problem: "Web lookup is switched off.", settings: "lookup", known: true },
    { id: "mail", label: "mail", description: "Lets it send mail.", on: false, problem: null, settings: null, known: true },
  ]);
  assert.deepEqual(capabilityRows([web], ["web"]).map((row) => row.on), [true]);
  assert.deepEqual(capabilityRows(undefined, []), []);
  assert.equal(capabilityLabel([web], "web"), "Web lookup");
  assert.equal(capabilityLabel(undefined, "web"), "web");
});

test("a capability the server no longer offers stays visible so it can be switched off", () => {
  const rows = capabilityRows([], ["old.thing", "old.thing"]);
  assert.deepEqual(rows, [{ id: "old.thing", label: "old.thing", description: UNKNOWN_CAPABILITY, on: true, problem: null, settings: null, known: false }]);
});

test("flipping a switch adds or removes one capability and never repeats one", () => {
  assert.deepEqual(toggleCapability([], "web", true), ["web"]);
  assert.deepEqual(toggleCapability(["web", "mail"], "web", true), ["mail", "web"]);
  assert.deepEqual(toggleCapability(["web", "mail"], "web", false), ["mail"]);
  assert.deepEqual(toggleCapability(["mail"], "web", false), ["mail"]);
});

test("a setup that says the same thing counts as unchanged", () => {
  const stored = { role: "helper", beat: { role: "default" }, night: { role: "smart" }, local_only: false, capabilities: ["a", "b"] };
  assert.equal(sameConfig(stored, { ...stored, role: " helper ", capabilities: ["b", "a"] }), true);
  assert.equal(sameConfig(stored, { ...stored, local_only: true }), false);
  assert.equal(sameConfig(stored, { ...stored, night: { provider_id: "p1", model: "m1" } }), false);
});

test("chat pages join into one list with no line twice", () => {
  const merged = mergeMessages([{ messages: [line(5), line(6)] }, { messages: [line(3), line(4), line(5)] }, undefined]);
  assert.deepEqual(merged.map((message) => message.seq), [3, 4, 5, 6]);
  assert.equal(lastSeq(merged), 6);
  assert.equal(lastSeq([]), 0);
});

test("older lines are asked for only while the server says there are more", () => {
  assert.equal(olderCursor({ messages: [line(201), line(202)], more: true }), 201);
  assert.equal(olderCursor({ messages: [line(1), line(2)], more: false }), undefined);
  assert.equal(olderCursor({ messages: [] }), undefined);
});

test("only the agent's lines the owner has not seen count as new", () => {
  const messages = [line(1), line(2, "owner"), line(3), line(4, "owner"), line(5)];
  assert.equal(unseenCount(messages, 2), 2);
  assert.equal(unseenCount(messages, 5), 0);
  assert.equal(unseenLabel(1), "1 new message");
  assert.equal(unseenLabel(4), "4 new messages");
});

test("a message is checked before sending and the owner is told how it was delivered", () => {
  assert.equal(messageProblem("   "), "Write something to send.");
  assert.equal(messageProblem("hello"), null);
  assert.match(messageProblem("x".repeat(20_003)) ?? "", /3 characters too long/);
  assert.match(deliveryNote({ delivered: "when it wakes", urgent: false }) ?? "", /when it wakes/);
  assert.match(deliveryNote({ delivered: "now", urgent: true }) ?? "", /wakes it/);
  assert.equal(deliveryNote({ delivered: "now", urgent: false }), null);
});

test("chat lines are grouped under the day they were written", () => {
  const day = (ts: number) => (ts < 10_000 ? "Yesterday" : "Today");
  const groups = groupByDay([line(1), line(2), line(30)], day);
  assert.deepEqual(groups.map((group) => [group.day, group.messages.length]), [["Yesterday", 2], ["Today", 1]]);
});

const PRIORITY: Priority = { id: "p-drafts", title: "Draft newsletters", why: "Readers hear from us monthly.", details: "", agents: [], active: true };

test("a priority with no agents named applies to everyone", () => {
  const agents = [
    { id: "agent-a", name: "Wren", role: "writer" },
    { id: "agent-b", name: null, role: "researcher" },
    { id: "agent-c", name: null, role: "" },
  ];
  assert.equal(appliesTo(PRIORITY, agents), "Everyone");
  assert.equal(appliesTo({ agents: ["agent-a", "agent-b"] }, agents), "Wren and Agent B");
  assert.equal(appliesTo({ agents: ["agent-a", "agent-b", "agent-x"] }, agents), "Wren, Agent B and Agent X");
});

test("an edit sends only the fields that changed", () => {
  const same = { title: "Draft  newsletters ", why: PRIORITY.why, details: "", agents: [], active: true };
  assert.deepEqual(priorityChange(PRIORITY, same), {});
  assert.deepEqual(priorityChange(PRIORITY, { ...same, active: false, agents: ["agent-a"] }), { active: false, agents: ["agent-a"] });
  assert.deepEqual(priorityChange(PRIORITY, { ...same, title: "Draft the newsletter" }), { title: "Draft the newsletter" });
});

test("a priority needs a title, and paused ones sort last", () => {
  assert.equal(titleProblem("   "), "Give the priority a short title.");
  assert.equal(titleProblem("x".repeat(201)), "Keep the title to 200 characters or fewer.");
  assert.equal(titleProblem("Fine"), null);
  assert.equal(tidyTitle("  a   b "), "a b");
  const paused = { ...PRIORITY, id: "p-old", active: false };
  const second = { ...PRIORITY, id: "p-new" };
  assert.deepEqual(sortPriorities([paused, PRIORITY, second]).map((item) => item.id), ["p-drafts", "p-new", "p-old"]);
});

test("an agent sees the active priorities for everyone and those that name it", () => {
  const named = { ...PRIORITY, id: "p-b", agents: ["agent-b"] };
  const paused = { ...PRIORITY, id: "p-off", active: false };
  assert.deepEqual(forAgent([PRIORITY, named, paused], "agent-a").map((item) => item.id), ["p-drafts"]);
  assert.deepEqual(forAgent([PRIORITY, named, paused], "agent-b").map((item) => item.id), ["p-drafts", "p-b"]);
});
