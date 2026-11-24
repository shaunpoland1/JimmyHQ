import assert from "node:assert/strict";
import { test } from "node:test";
import type { AgentSummary } from "../src/modules/village/api.ts";
import { headcountCap, showVitals, touchFrom, touchesVillageSettings } from "../src/modules/village/lib/live.ts";
import { parseVillageRoute, tabAfterKey, villageSub } from "../src/modules/village/lib/route.ts";
import {
  colourOf,
  describeDoing,
  displayName,
  filterCounts,
  headcount,
  initials,
  matchesFilter,
  roleLine,
  sortRoster,
  toMs,
  untilText,
  waitingOnYou,
} from "../src/modules/village/lib/status.ts";

const NEEDS = { energy: 80, frustration: 10, social: 55, curiosity: 40, purpose: 50 };

function agent(id: string, extra: Partial<AgentSummary> = {}): AgentSummary {
  return { id, name: null, role: "researcher", retired: false, mode: "work", asleep: false, needs: NEEDS, current_task: null, waiting: 0, unanswered: 0, ...extra };
}

test("a working agent is described by its task", () => {
  assert.equal(describeDoing(agent("agent-a", { current_task: "the weekly report" })).headline, "Working on the weekly report");
  const idle = describeDoing(agent("agent-a"));
  assert.equal(idle.headline, "Awake, between tasks");
  assert.equal(idle.state, "awake");
});

test("each mode is said in plain words", () => {
  assert.equal(describeDoing(agent("agent-a", { mode: "explore" })).headline, "Exploring something it is curious about");
  assert.equal(describeDoing(agent("agent-a", { mode: "outlet" })).headline, "Taking a break to cool off");
  assert.equal(describeDoing(agent("agent-a", { mode: "social" })).headline, "In the break room with the others");
  assert.equal(describeDoing(agent("agent-a", { mode: "rest" })).state, "resting");
});

test("sleep says which kind it is", () => {
  const asleep = (sleep_kind: string | null) => describeDoing({ ...agent("agent-a", { asleep: true, mode: "sleep" }), vitals: { sleep_kind } });
  assert.equal(asleep("night").headline, "Asleep for the night");
  assert.equal(asleep("nap").headline, "Taking a nap");
  assert.equal(asleep("forced").headline, "Asleep: it ran out of energy");
  assert.equal(asleep(null).headline, "Asleep");
  assert.equal(asleep("nap").state, "asleep");
});

test("a rest or a sleep carries its end time only when the server says it", () => {
  const resting = describeDoing({ ...agent("agent-a", { mode: "rest" }), vitals: { rest_until: 1_800_000_000 } });
  assert.equal(resting.untilMs, 1_800_000_000_000);
  assert.equal(describeDoing({ ...agent("agent-a", { mode: "rest" }), vitals: {} }).untilMs, null);
  assert.equal(describeDoing({ ...agent("agent-a", { asleep: true }), vitals: { sleep_kind: "night" } }).untilMs, null);
  assert.equal(describeDoing({ ...agent("agent-a", { asleep: true }), vitals: { sleep_kind: "nap", nap_until: 1_800_000_000 } }).untilMs, 1_800_000_000_000);
});

test("an agent with no body yet has not started, and a retired one is retired", () => {
  assert.equal(describeDoing({ id: "agent-a", name: null, role: "", retired: false }).state, "new");
  assert.equal(describeDoing(agent("agent-a", { retired: true })).headline, "Retired");
});

test("a time in seconds and a time in milliseconds are the same moment", () => {
  assert.equal(toMs(1_800_000_000), 1_800_000_000_000);
  assert.equal(toMs(1_800_000_000_000), 1_800_000_000_000);
  assert.equal(toMs(1_800_000_000.5), 1_800_000_000_500);
  assert.equal(toMs(null), null);
  assert.equal(toMs(0), null);
});

test("time until waking is said the way a person would", () => {
  const now = 1_000_000_000_000;
  const minutes = (count: number) => now + count * 60_000;
  assert.equal(untilText(minutes(-5), now), "any moment now");
  assert.equal(untilText(minutes(1), now), "in 1 minute");
  assert.equal(untilText(minutes(20), now), "in 20 minutes");
  assert.equal(untilText(minutes(60), now), "in 1 hour");
  assert.equal(untilText(minutes(80), now), "in 1 hour 20 minutes");
  assert.equal(untilText(minutes(7 * 60 + 10), now), "in about 7 hours");
  assert.equal(untilText(minutes(3 * 1440), now), "in about 3 days");
});

test("what an agent wants from the owner is spelled out", () => {
  assert.deepEqual(waitingOnYou(agent("agent-a")), []);
  assert.deepEqual(waitingOnYou(agent("agent-a", { unanswered: 1, waiting: 2 })), ["1 message for you", "Waiting on you for 2 things"]);
  assert.deepEqual(waitingOnYou(agent("agent-a", { unanswered: 3, retired: true })), []);
});

test("the roster puts whoever is waiting on the owner first and sleepers last", () => {
  const roster = [
    agent("agent-a", { asleep: true }),
    agent("agent-b", { mode: "rest" }),
    agent("agent-c"),
    agent("agent-d", { asleep: true, unanswered: 2 }),
    agent("agent-e"),
  ];
  assert.deepEqual(sortRoster(roster).map((item) => item.id), ["agent-d", "agent-c", "agent-e", "agent-b", "agent-a"]);
});

test("the filters count each agent under every heading it fits", () => {
  const roster = [agent("agent-a", { asleep: true, waiting: 1 }), agent("agent-b", { mode: "rest" }), agent("agent-c")];
  assert.deepEqual(filterCounts(roster), { all: 3, you: 1, awake: 1, resting: 1, asleep: 1 });
  assert.equal(matchesFilter(roster[0] as AgentSummary, "you"), true);
  assert.equal(matchesFilter(roster[2] as AgentSummary, "asleep"), false);
});

test("an agent keeps its colour when someone before it retires", () => {
  const everyone = [{ id: "agent-a" }, { id: "agent-b" }, { id: "agent-c" }, { id: "agent-d" }, { id: "agent-e" }];
  assert.equal(colourOf("agent-b", everyone), "b");
  assert.equal(colourOf("agent-e", everyone), "a");
  assert.equal(colourOf("agent-z", everyone), "a");
});

test("an agent goes by its working name until it has named itself, with its role beside it", () => {
  assert.equal(displayName({ id: "agent-a", name: null, role: "project manager" }), "Agent A");
  assert.equal(roleLine({ id: "agent-a", name: null, role: "project manager" }), "Project manager · has not chosen a name yet");
  assert.equal(displayName({ id: "agent-a", name: "Wren", role: "researcher" }), "Wren");
  assert.equal(roleLine({ id: "agent-a", name: "Wren", role: "researcher" }), "Researcher");
  assert.equal(displayName({ id: "agent-b", name: null, role: "" }), "Agent B");
  assert.equal(roleLine({ id: "agent-b", name: null, role: "" }), "No role yet · has not chosen a name yet");
  assert.equal(initials({ id: "agent-a", name: "Wren", role: "" }), "W");
  assert.equal(initials({ id: "agent-a", name: null, role: "household admin" }), "AA");
});

test("the village is full when the living reach the cap", () => {
  assert.deepEqual(headcount(2, 4), { full: false, text: "2 of 4 living here" });
  assert.equal(headcount(4, 4).full, true);
  assert.deepEqual(headcount(9, null), { full: false, text: "9 living here" });
});

test("the address names an agent and one of its tabs", () => {
  assert.deepEqual(parseVillageRoute(null), { agent: null });
  assert.deepEqual(parseVillageRoute("agent-a"), { agent: "agent-a", tab: "overview" });
  assert.deepEqual(parseVillageRoute("agent-a/chat"), { agent: "agent-a", tab: "chat" });
  assert.deepEqual(parseVillageRoute("agent-a/integrations"), { agent: "agent-a", tab: "integrations" });
  assert.deepEqual(parseVillageRoute("agent-a/nonsense"), { agent: "agent-a", tab: "overview" });
  assert.deepEqual(parseVillageRoute("Not An Id"), { agent: null });
  assert.equal(villageSub({ agent: "agent-a", tab: "overview" }), "agent-a");
  assert.equal(villageSub({ agent: "agent-a", tab: "diary" }), "agent-a/diary");
  assert.equal(villageSub({ agent: null }), null);
});

test("arrow keys move between tabs and wrap at the ends", () => {
  assert.equal(tabAfterKey("ArrowRight", "overview"), "chat");
  assert.equal(tabAfterKey("ArrowLeft", "overview"), "activity");
  assert.equal(tabAfterKey("ArrowRight", "activity"), "overview");
  assert.equal(tabAfterKey("Home", "files"), "overview");
  assert.equal(tabAfterKey("End", "chat"), "activity");
  assert.equal(tabAfterKey("a", "chat"), null);
  assert.equal(tabAfterKey("ArrowRight", "files"), "integrations");
});

test("only village events make the screen read again, and each says what changed", () => {
  assert.equal(touchFrom({ kind: "memory.fact.added", payload: {} }), null);
  assert.deepEqual(touchFrom({ kind: "village.said", payload: { agent_id: "agent-a" } }), { agent: "agent-a", roster: true, chat: true, priorities: false });
  assert.deepEqual(touchFrom({ kind: "village.born", payload: { agent_id: "agent-b" } }), { agent: "agent-b", roster: true, chat: false, priorities: false });
  assert.deepEqual(touchFrom({ kind: "village.prioritized", payload: { priority_id: "p-x" } }), { agent: null, roster: false, chat: false, priorities: true });
  assert.equal(touchesVillageSettings({ kind: "setting.updated", payload: { key: "village.headcount_cap" } }), true);
  assert.equal(touchesVillageSettings({ kind: "setting.updated", payload: { key: "appearance.theme" } }), false);
});

test("vitals are shown unless a setting switches them off", () => {
  assert.equal(showVitals(undefined), true);
  assert.equal(showVitals([{ key: "village.headcount_cap", value: 4 }]), true);
  assert.equal(showVitals([{ key: "village.show_vitals", value: false }]), false);
  assert.equal(showVitals([{ key: "village.show_vitals", value: true }]), true);
  assert.equal(headcountCap([{ key: "village.headcount_cap", value: 4 }]), 4);
  assert.equal(headcountCap([]), null);
});
