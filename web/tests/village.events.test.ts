import assert from "node:assert/strict";
import { test } from "node:test";
import type { VillageEvent } from "../src/modules/village/api.ts";
import { GROUPS, PAGE, describeEvent, eventNumber, groupByDayOf, groupOf, kindsOf, mayHaveMore, mergeEvents, olderEdge, rawData, refusalCount, refusalsToday } from "../src/modules/village/lib/events.ts";

const event = (n: number, kind = "action", data: Record<string, unknown> = {}): VillageEvent => ({ id: `evt-${n}`, ts: n * 1000, kind, agent: "agent-a", data });

test("every filter has kinds, and ticks are in none of them", () => {
  for (const group of GROUPS) assert.ok(kindsOf(group).length > 0, group);
  assert.equal(kindsOf("everything").includes("tick"), false);
  assert.equal(groupOf("tick"), null);
  assert.deepEqual([...kindsOf("refusals")], ["validator_reject", "claim_unverified", "date_slip"]);
});

test("no kind belongs to two filters", () => {
  const seen = new Set<string>();
  for (const group of GROUPS.filter((item) => item !== "everything")) {
    for (const kind of kindsOf(group)) {
      assert.equal(seen.has(kind), false, `${kind} is in two filters`);
      seen.add(kind);
    }
  }
});

test("everything holds every kind of every other filter once", () => {
  const all = kindsOf("everything");
  assert.equal(new Set(all).size, all.length);
  for (const group of GROUPS.filter((item) => item !== "everything")) for (const kind of kindsOf(group)) assert.ok(all.includes(kind));
});

test("event ids are read as numbers and anything else is zero", () => {
  assert.equal(eventNumber("evt-12"), 12);
  assert.equal(eventNumber("chat-3"), 0);
  assert.equal(eventNumber(""), 0);
});

test("pages are joined newest first with an event that sits in two pages shown once", () => {
  const merged = mergeEvents([[event(5), event(6), event(7)], [event(3), event(4), event(5)]]);
  assert.deepEqual(merged.map((item) => item.id), ["evt-7", "evt-6", "evt-5", "evt-4", "evt-3"]);
  assert.deepEqual(mergeEvents([]), []);
});

test("the next older page is asked for by the oldest event held, and not at all when none is held", () => {
  assert.equal(olderEdge([event(9), event(4), event(6)]), "evt-4");
  assert.equal(olderEdge([]), undefined);
});

test("a full page may have more behind it and a short page is the start of the log", () => {
  assert.equal(mayHaveMore(PAGE), true);
  assert.equal(mayHaveMore(PAGE - 1), false);
  assert.equal(mayHaveMore(0), false);
  assert.equal(mayHaveMore(5, 5), true);
});

test("events are grouped under the day they happened in the order given", () => {
  const groups = groupByDayOf([event(3), event(2), event(1)], (ts) => (ts >= 2000 ? "later" : "earlier"));
  assert.deepEqual(groups.map((group) => [group.day, group.items.length]), [["later", 2], ["earlier", 1]]);
});

test("a refusal shows the tool and the reason the village gave, and is marked as a refusal", () => {
  const said = describeEvent(event(1, "validator_reject", { tool: "send_email", reason: "That needs the owner's approval first." }));
  assert.equal(said.tone, "refusal");
  assert.equal(said.group, "refusals");
  assert.equal(said.headline, "Was refused when it tried send email");
  assert.equal(said.detail, "That needs the owner's approval first.");
  assert.equal(describeEvent(event(1, "validator_reject", { tool: "x" })).detail, "No reason was recorded.");
});

test("a task claimed finished with nothing to show for it counts as a refusal", () => {
  const said = describeEvent(event(1, "claim_unverified", { task: "the weekly report" }));
  assert.equal(said.tone, "refusal");
  assert.match(said.headline, /"the weekly report"/);
});

test("an action that failed is a warning and one that found nothing new is quiet", () => {
  assert.equal(describeEvent(event(1, "action", { tool: "web_search", ok: false, result: "timed out" })).tone, "warn");
  const none = describeEvent(event(1, "action", { tool: "web_search", ok: true, no_new_result: true }));
  assert.equal(none.tone, "quiet");
  assert.equal(none.headline, "Used web search and got nothing new back");
  const fine = describeEvent(event(1, "action", { tool: "write_file", ok: true, task: "the report", result: "saved" }));
  assert.equal(fine.headline, "Used write file");
  assert.equal(fine.detail, "For: the report. saved");
});

test("a change in a need says which way it went, where it is now and why", () => {
  const up = describeEvent(event(1, "need_change", { need: "energy", delta: 2, value: 61.4, cause: "resting" }));
  assert.equal(up.headline, "Energy rose to 61");
  assert.equal(up.detail, "Because of resting.");
  const down = describeEvent(event(1, "need_change", { need: "social", delta: -1, value: 30, cause: "validator_reject" }));
  assert.equal(down.headline, "Company fell to 30");
  assert.equal(down.detail, "Because of a refusal.");
});

test("moods and modes are said as the agent's own state", () => {
  assert.equal(describeEvent(event(1, "feeling", { phrase: "aimless" })).headline, "Felt aimless");
  assert.equal(describeEvent(event(1, "mode_change", { old: "work", new: "rest" })).headline, "Went from working to resting");
  assert.equal(describeEvent(event(1, "sleep", { sleep_kind: "nap" })).headline, "Took a nap");
  assert.equal(describeEvent(event(1, "wake", { cause: "urgent_call" })).headline, "Was woken by your urgent message");
});

test("talk names who was spoken to and what was said", () => {
  const names = (id: string | null | undefined) => (id === "agent-b" ? "Sparrow" : (id ?? "someone"));
  assert.equal(describeEvent(event(1, "message", { other: "agent-b" }), names).headline, "Spoke to Sparrow");
  assert.equal(describeEvent(event(1, "message", { other: "agent-b", heard: true }), names).headline, "Was spoken to by Sparrow");
  const chat = describeEvent(event(1, "chat", { sender: "owner", text: "How is it going?" }));
  assert.equal(chat.headline, "You wrote to it");
  assert.equal(chat.detail, "How is it going?");
  assert.equal(describeEvent(event(1, "chat", { sender: "agent", text: "Fine." })).headline, "It wrote to you");
});

test("a night is said with its changes, and a night that failed is a warning", () => {
  const done = describeEvent(event(1, "consolidation", { ops: 4, dream: true, notes: "Tidied up." }));
  assert.equal(done.headline, "Sorted the day into memory: 4 changes, and dreamt");
  assert.equal(done.detail, "Tidied up.");
  const failed = describeEvent(event(1, "consolidation", { failed: true, why: "no model answered" }));
  assert.equal(failed.tone, "warn");
  assert.equal(failed.detail, "no model answered");
});

test("model calls give the time and tokens, and a slip is a warning", () => {
  const call = describeEvent(event(1, "model_call", { mode: "work", ms: 2500, input_tokens: 1200, output_tokens: 80, model: "local-model" }));
  assert.equal(call.headline, "Asked the model (work)");
  assert.equal(call.detail, "local-model · 2.5 seconds · 1200 tokens in, 80 out");
  assert.equal(describeEvent(event(1, "model_call", { slip: "cut_off" })).tone, "warn");
});

test("a kind this screen has no wording for is still shown, quietly, in plain words", () => {
  const said = describeEvent(event(1, "something_new", { a: 1 }));
  assert.equal(said.headline, "Something new");
  assert.equal(said.tone, "quiet");
  assert.equal(said.group, null);
});

test("the raw data behind an event keeps its id, kind and every field", () => {
  const parsed = JSON.parse(rawData(event(4, "feeling", { phrase: "settled", evidence: ["evt-1"] })));
  assert.equal(parsed.id, "evt-4");
  assert.equal(parsed.kind, "feeling");
  assert.deepEqual(parsed.data, { phrase: "settled", evidence: ["evt-1"] });
});

test("refusals are counted in a list and in the day's totals", () => {
  assert.equal(refusalCount([event(1, "validator_reject"), event(2, "action"), event(3, "claim_unverified")]), 2);
  assert.equal(refusalsToday({ validator_reject: 3, action: 40 }), "3 refusals in the last 24 hours. A run of them means it is struggling with something.");
  assert.match(refusalsToday({ validator_reject: 1 }) ?? "", /^1 refusal in/);
  assert.equal(refusalsToday({ action: 9 }), null);
  assert.equal(refusalsToday(undefined), null);
});
