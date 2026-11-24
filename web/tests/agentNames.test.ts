import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { NO_NAMES, ROSTER_KEY, ROSTER_PATH, chosenNames, initialsForId, initialsOf, shownInitials, shownName, workingName } from "../src/lib/agentNames.ts";
import { describeFiler, historyLine } from "../src/modules/bench/lib/format.ts";
import { assigneeLabel, tileFor } from "../src/modules/tasks/lib/group.ts";
import { agentLabel } from "../src/modules/village/lib/names.ts";
import { initials as villageInitials } from "../src/modules/village/lib/status.ts";

test("an agent known only by its id gets the same two letters the Village shows", () => {
  assert.equal(workingName("agent-b"), "Agent B");
  assert.equal(initialsForId("agent-a"), "AA");
  assert.equal(initialsForId("agent-b"), "AB");
  assert.equal(initialsForId("agent-27"), "A2");
});

test("a chosen name gives one letter for one word and two for more", () => {
  assert.equal(initialsOf("Wren"), "W");
  assert.equal(initialsOf("Mary Jane Watson"), "MJ");
  assert.equal(initialsOf(""), "?");
});

test("Tasks tells three unnamed agents apart by letters, not only colour", () => {
  const known = ["owner", "agent:agent-a", "agent:agent-b", "agent:agent-c"];
  const letters = known.slice(1).map((who) => (tileFor(who, known) as { letter: string }).letter);
  assert.deepEqual(letters, ["AA", "AB", "AC"]);
});

// --- the name an agent chose, on the screens that know it only by its id ---

const roster = [
  { id: "agent-a", name: "Wren", role: "writer" },
  { id: "agent-b", name: null, role: "reader" },
  { id: "agent-c", name: "  Mary Jane  ", role: "" },
  { id: "agent-d", name: "   ", role: "scout" },
  { id: "scout", name: "Pip", role: "scout" },
];

test("the chosen names are read from the Village's roster, trimmed, and an agent with none has none", () => {
  const chosen = chosenNames(roster);
  assert.deepEqual([...chosen], [["agent-a", "Wren"], ["agent-c", "Mary Jane"], ["scout", "Pip"]]);
});

test("a roster that is not a list, or has odd rows, gives no names and never throws", () => {
  for (const odd of [undefined, null, "agents", 7, {}, { agents: [] }]) assert.equal(chosenNames(odd).size, 0, String(odd));
  const odd = chosenNames([null, "x", 3, { id: 5, name: "Five" }, { id: "", name: "Nobody" }, { id: "agent-z", name: 9 }, {}]);
  assert.equal(odd.size, 0);
});

test("the same roster read twice is one map, so a long list of rows reads it once", () => {
  assert.equal(chosenNames(roster), chosenNames(roster));
});

test("an agent shows the name it chose and the letters of that name, else its working name and letters", () => {
  const chosen = chosenNames(roster);
  assert.equal(shownName("agent-a", chosen), "Wren");
  assert.equal(shownInitials("agent-a", chosen), "W");
  assert.equal(shownName("agent-c", chosen), "Mary Jane");
  assert.equal(shownInitials("agent-c", chosen), "MJ");
  assert.equal(shownName("agent-b", chosen), "Agent B");
  assert.equal(shownInitials("agent-b", chosen), "AB");
  assert.equal(shownName("agent-d", chosen), "Agent D");
  assert.equal(shownName("agent-b"), "Agent B", "with no roster at all (the Village off or empty)");
  assert.equal(shownInitials("agent-b", NO_NAMES), "AB");
});

test("an agent the roster no longer lists keeps its working name", () => {
  assert.equal(shownName("agent-q", chosenNames(roster)), "Agent Q");
  assert.equal(shownName("", chosenNames(roster)), "New agent");
});

test("every screen shows an agent exactly as the Village does, name and letters", () => {
  const chosen = chosenNames(roster);
  for (const agent of roster) {
    assert.equal(shownName(agent.id, chosen), agentLabel(agent).title, agent.id);
    assert.equal(shownInitials(agent.id, chosen), villageInitials(agent), agent.id);
  }
});

test("Tasks shows the chosen name and letters for an assignee, and the working ones until it has chosen", () => {
  const chosen = chosenNames(roster);
  const known = ["owner", "agent:agent-a", "agent:agent-b"];
  assert.equal(assigneeLabel("agent:agent-a", chosen), "Wren");
  assert.equal(assigneeLabel("agent:agent-b", chosen), "Agent B");
  assert.equal(assigneeLabel("agent:agent-a"), "Agent A");
  assert.equal(assigneeLabel("owner", chosen), "You");
  assert.equal((tileFor("agent:agent-a", known, chosen) as { letter: string }).letter, "W");
  assert.equal((tileFor("agent:agent-b", known, chosen) as { letter: string }).letter, "AB");
});

test("an agent's colour on Tasks does not change when it names itself", () => {
  const known = ["owner", "agent:agent-a", "agent:agent-b"];
  for (const who of known.slice(1)) {
    assert.equal((tileFor(who, known, chosenNames(roster)) as { tone: number }).tone, (tileFor(who, known) as { tone: number }).tone);
  }
});

test("the Bench shows who filed a card by the name the agent chose, with the same letters and its old colour", () => {
  const chosen = chosenNames(roster);
  const named = describeFiler("agent:agent-a", chosen);
  assert.deepEqual({ name: named.name, letter: named.letter }, { name: "Wren", letter: "W" });
  assert.equal(named.tone, describeFiler("agent:agent-a").tone);
  const unnamed = describeFiler("agent:agent-b", chosen);
  assert.deepEqual({ name: unnamed.name, letter: unnamed.letter }, { name: "Agent B", letter: "AB" });
  assert.deepEqual(describeFiler("owner", chosen), describeFiler("owner"));
  assert.deepEqual(describeFiler("token:importer", chosen), describeFiler("token:importer"));
});

test("the Bench's history says the chosen name too", () => {
  const entry = { type: "filed", actor: "agent:agent-a", ts: 1 } as unknown as Parameters<typeof historyLine>[0];
  assert.equal(historyLine(entry, 2, undefined, chosenNames(roster)).text, "Wren filed this");
  assert.equal(historyLine(entry, 2).text, "Agent A filed this");
});

test("the roster is asked for at the Village screen's own address and key, so one cached answer serves every screen", () => {
  const hooks = readFileSync(new URL("../src/modules/village/hooks.ts", import.meta.url), "utf8");
  const api = readFileSync(new URL("../src/modules/village/api.ts", import.meta.url), "utf8");
  assert.ok(hooks.includes(`AGENTS_KEY = ${JSON.stringify(ROSTER_KEY).replace(/,/g, ", ")} as const`), "the key");
  assert.ok(api.includes(`"GET", "${ROSTER_PATH}"`), "the address");
});
