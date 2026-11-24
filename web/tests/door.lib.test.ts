import assert from "node:assert/strict";
import { test } from "node:test";
import { MESSAGES_NOTE, NEVER, affectsDoor, callLine, canDo, checkLimit, checkName, limitFor, parseAreas, ruleWords, sortClients, statusLine, toolOn, usageText, withLimit, withTool } from "../src/modules/door/lib/door.ts";

const WHOAMI = { name: "whoami", title: "Who am I", description: "", write: false, always: true, per_day: 200 };
const LIST = { name: "tasks_list", title: "List", description: "", write: false, always: false, per_day: 200 };
const ADD = { name: "tasks_add", title: "Add", description: "", write: true, always: false, per_day: 20 };
const TOOLS = [WHOAMI, LIST, ADD];

test("a client's name is trimmed, lower-cased and accepted when it is plain", () => {
  assert.deepEqual(checkName("  Helper-2 "), { ok: true, value: "helper-2" });
});

test("an empty, long, odd or already used name is refused with a sentence", () => {
  for (const bad of ["", "  ", "has space", "a:b", "-lead", "x".repeat(33), "café"]) {
    const check = checkName(bad);
    assert.equal(check.ok, false, JSON.stringify(bad));
    if (!check.ok) assert.match(check.error, /\.$/);
  }
  const used = checkName("helper", ["helper"]);
  assert.equal(used.ok, false);
  if (!used.ok) assert.match(used.error, /not reused/);
});

test("an empty daily limit means the usual one and anything but a whole number is refused", () => {
  assert.deepEqual(checkLimit(""), { ok: true, value: null });
  assert.deepEqual(checkLimit(" 15 "), { ok: true, value: 15 });
  assert.deepEqual(checkLimit("0"), { ok: true, value: 0 });
  for (const bad of ["-1", "1.5", "many", "100001"]) assert.equal(checkLimit(bad).ok, false, bad);
});

test("a new client has only the tool every client has", () => {
  const client = { tools: {} };
  assert.equal(toolOn(client, WHOAMI), true);
  assert.equal(toolOn(client, LIST), false);
});

test("switching a tool on and off keeps the other tools and their limits", () => {
  const tools = { tasks_list: null, tasks_add: 5 };
  assert.deepEqual(withTool(tools, "bench_cards", true), { tasks_list: null, tasks_add: 5, bench_cards: null });
  assert.deepEqual(withTool(tools, "tasks_add", true), tools);
  assert.deepEqual(withTool(tools, "tasks_add", false), { tasks_list: null });
  assert.deepEqual(tools, { tasks_list: null, tasks_add: 5 });
});

test("a tool's own limit replaces the usual one and clearing it brings the usual one back", () => {
  const client = { tools: withLimit({ tasks_add: null }, "tasks_add", 3), used_today: { tasks_add: 2 } };
  assert.equal(limitFor(client, ADD), 3);
  assert.equal(usageText(client, ADD), "2 of 3 today");
  assert.equal(limitFor({ tools: withLimit(client.tools, "tasks_add", null) }, ADD), 20);
  assert.equal(usageText({ tools: {}, used_today: {} }, LIST), "0 of 200 today");
  assert.deepEqual(withLimit({}, "whoami", null, true), {});
  assert.deepEqual(withLimit({}, "whoami", 9, true), { whoami: 9 });
});

test("areas typed with commas become a clean list without repeats", () => {
  assert.deepEqual(parseAreas(" Home, work ,, home"), ["home", "work"]);
  assert.deepEqual(parseAreas(""), []);
});

test("the status line says off, a problem, local only, or reachable from the internet", () => {
  const base = { enabled: true, listening: true, problem: null, listen: "own_port" as const, public: null };
  assert.equal(statusLine({ ...base, enabled: false }).tone, "off");
  assert.deepEqual(statusLine({ ...base, problem: "The port is taken." }), { tone: "warn", text: "The port is taken." });
  assert.equal(statusLine({ ...base, listening: false }).tone, "warn");
  assert.match(statusLine(base).text, /this computer only/);
  assert.match(statusLine({ ...base, public: "https://door.example.com/mcp" }).text, /anyone on the internet can knock/);
  assert.match(statusLine({ ...base, listen: "main_port" }).text, /JimmyHQ's own address/);
});

test("what a client can do is said in plain words, and what none can do is always said", () => {
  assert.match(canDo({ tools: {} }, TOOLS), /nothing else/);
  assert.equal(canDo({ tools: { tasks_list: null } }, TOOLS), "This client can use 1 tool: 1 that only read.");
  assert.equal(canDo({ tools: { tasks_list: null, tasks_add: 2 } }, TOOLS), "This client can use 2 tools: 1 that only read and 1 that can add something.");
  for (const word of ["only if you gave that agent", "hear from outside clients and write back", "is refused"]) assert.ok(MESSAGES_NOTE.includes(word), word);
  for (const word of ["decide a card", "private memory", "change a setting", "signed with its name"]) assert.ok(NEVER.includes(word), word);
});

test("a receipt reads as who did what and how it went", () => {
  assert.equal(callLine({ client: "helper", tool: "tasks_list", ok: true, rule: "", created: null }), "helper: tasks_list");
  assert.equal(callLine({ client: "helper", tool: "tasks_add", ok: true, rule: "", created: "task:01JABCDEFGHJKMNPQRSTVWXYZ0" }), "helper: tasks_add, made task VWXYZ0");
  assert.equal(callLine({ client: "helper", tool: "tasks_add", ok: false, rule: "door_daily_limit", created: null }), "helper: tasks_add was refused (reached the day's limit)");
  assert.equal(callLine({ client: "helper", tool: "(any)", ok: false, rule: "door_rate_limit", created: null }), "helper: a request was refused (asked too fast)");
  assert.equal(ruleWords("door_task_must_be_yours"), "task must be yours");
});

test("a door event or a change to a door setting makes the door worth asking for again", () => {
  assert.equal(affectsDoor({ kind: "door.called" }), true);
  assert.equal(affectsDoor({ kind: "door.client.revoked" }), true);
  assert.equal(affectsDoor({ kind: "setting.updated", payload: { key: "door.enabled" } }), true);
  assert.equal(affectsDoor({ kind: "setting.updated", payload: { key: "chat.local_only" } }), false);
  assert.equal(affectsDoor({ kind: "task.filed" }), false);
});

test("live clients come first in the order they were added and revoked ones after", () => {
  const rows = [
    { name: "c", created: 3, revoked: null },
    { name: "x", created: 1, revoked: 10 },
    { name: "a", created: 2, revoked: null },
    { name: "y", created: 0, revoked: 20 },
  ];
  assert.deepEqual(sortClients(rows).map((row) => row.name), ["a", "c", "y", "x"]);
});
