import assert from "node:assert/strict";
import { test } from "node:test";
import type { MemoryLine, Snapshot, VillageEvent } from "../src/modules/village/api.ts";
import {
  aboutLabel,
  byWords,
  describeNightWork,
  evidenceText,
  newestSnapshots,
  nightsBySnapshot,
  pathWords,
  provenanceOf,
  rollbackEffects,
  sectionLabel,
  sectionOrder,
  snapshotTitle,
  sortCandidates,
  sortImpressions,
  sortLines,
  strengthOf,
  tallyResults,
  weightWord,
} from "../src/modules/village/lib/memory.ts";
import { commandProgress } from "../src/modules/village/lib/commands.ts";

test("each source of a memory line is said in plain words, and the unchecked ones are marked as such", () => {
  assert.equal(provenanceOf("owner").label, "From you");
  assert.equal(provenanceOf("seen").label, "Seen in its own actions");
  assert.equal(provenanceOf("village").label, "From the village's news");
  assert.equal(provenanceOf("web:unconfirmed").label, "From the web, unconfirmed");
  assert.equal(provenanceOf("self-reported").label, "Its own account, not checked");
  assert.equal(provenanceOf("owner").trust, "checked");
  assert.equal(provenanceOf("web:unconfirmed").trust, "unchecked");
  assert.equal(provenanceOf("self-reported").trust, "unchecked");
});

test("a line with no source, or one this screen has no words for, says so instead of hiding it", () => {
  assert.equal(provenanceOf(undefined).label, "Source not recorded");
  assert.equal(provenanceOf("").trust, "unknown");
  assert.equal(provenanceOf("telepathy").label, "Source: telepathy");
});

test("strength is a percent with a word, clamped to 0 to 100", () => {
  assert.deepEqual(strengthOf(0.9), { percent: 90, word: "Very strong" });
  assert.deepEqual(strengthOf(0.5), { percent: 50, word: "Moderate" });
  assert.equal(strengthOf(0.05)?.word, "Faint");
  assert.equal(strengthOf(3)?.percent, 100);
  assert.equal(strengthOf(-1)?.percent, 0);
  assert.equal(strengthOf(undefined), null);
});

test("weights and evidence counts are worded, and no evidence is said outright", () => {
  assert.match(weightWord("core") ?? "", /^Core/);
  assert.equal(weightWord(undefined), null);
  assert.equal(evidenceText(undefined), "No evidence cited");
  assert.equal(evidenceText(["evt-1"]), "1 piece of evidence");
  assert.equal(evidenceText(["evt-1", "evt-2", "evt-3"]), "3 pieces of evidence");
});

test("core lines come first, then the strongest, with a steady order for ties", () => {
  const line = (id: number, weight: string, strength: number): MemoryLine => ({ id, text: `line ${id}`, weight, strength });
  const sorted = sortLines([line(1, "useful", 0.4), line(2, "routine", 0.9), line(3, "core", 0.2), line(4, "useful", 0.7), line(5, "useful", 0.7)]);
  assert.deepEqual(sorted.map((item) => item.id), [3, 4, 5, 1, 2]);
});

test("the seven sections come in the server's order, with any new one after them", () => {
  assert.deepEqual(sectionOrder({}), ["self", "skills", "tools", "wants", "moments", "facts", "story"]);
  assert.deepEqual(sectionOrder({ habits: [] }).slice(-1), ["habits"]);
  assert.equal(sectionLabel("self"), "Who it is");
  assert.equal(sectionLabel("habits"), "Habits");
});

test("impressions of the owner come first and a blank one is left out", () => {
  const sorted = sortImpressions([
    { about: "agent-b", text: "Quick." },
    { about: "agent-a", text: "  " },
    { about: "owner", text: "Kind." },
  ]);
  assert.deepEqual(sorted.map((item) => item.about), ["owner", "agent-b"]);
});

test("an impression is about you, a coworker by name, or by id when they are gone", () => {
  const everyone = [{ id: "agent-a", name: "Wren", role: "writer" }];
  assert.equal(aboutLabel("owner", everyone), "You");
  assert.equal(aboutLabel("agent-a", everyone), "Wren");
  assert.equal(aboutLabel("agent-z", everyone), "Agent Z");
});

test("candidates waiting for the night are listed newest first, whether times are in seconds or milliseconds", () => {
  const sorted = sortCandidates([
    { text: "old", ts: 1_700_000_000 },
    { text: "new", ts: 1_700_000_500_000 },
  ]);
  assert.deepEqual(sorted.map((item) => item.text), ["new", "old"]);
});

test("snapshots are listed newest first and titled by what took them", () => {
  const list: Snapshot[] = [
    { seq: 1, ts: 1, reason: "night", day: "2026-03-01" },
    { seq: 3, ts: 3, reason: "before rollback", to: 1 },
    { seq: 2, ts: 2, reason: "nap", day: "2026-03-02" },
  ];
  assert.deepEqual(newestSnapshots(list).map((item) => item.seq), [3, 2, 1]);
  const plain = (day: string) => `D(${day})`;
  assert.equal(snapshotTitle(list[0] as Snapshot, plain), "Night after D(2026-03-01)");
  assert.equal(snapshotTitle(list[2] as Snapshot, plain), "Nap on D(2026-03-02)");
  assert.equal(snapshotTitle(list[1] as Snapshot, plain), "Saved just before rolling back to snapshot 1");
  assert.equal(snapshotTitle({ seq: 9, ts: 1 }, plain), "Snapshot 9");
});

test("what a night did is counted from its own sentences about each change", () => {
  assert.equal(tallyResults(["added #1", "added #2", "reinforced #1", "rejected: section full of stronger memories", "merged #3 into #4", "odd thing"]), "added 2, reinforced 1, merged 1, rejected 1");
  assert.equal(tallyResults(undefined), "");
});

test("a finished night is summarised from the log, with the model that did it and its notes", () => {
  const event: VillageEvent = {
    id: "evt-9",
    ts: 1,
    kind: "consolidation",
    data: { ops: 3, by: "fallback", notes: "Kept the lesson about the report.", dream: true, snapshot: 4, results: ["added #1", "added #2", "rejected: x"], decayed_out: [7] },
  };
  const work = describeNightWork(event, undefined);
  assert.equal(work?.summary, "3 changes to memory: added 2, rejected 1. 1 line faded out.");
  assert.equal(work?.by, "Sorted by the everyday model, standing in for the night model");
  assert.equal(work?.notes, "Kept the lesson about the report.");
  assert.equal(work?.dreamt, true);
});

test("a night that did not finish says why, and one with nothing to change says that", () => {
  const failed = describeNightWork({ id: "evt-1", ts: 1, kind: "consolidation", data: { failed: true, why: "no model answered" } }, undefined);
  assert.equal(failed?.failed, "no model answered");
  assert.match(failed?.summary ?? "", /did not finish: no model answered/);
  const quiet = describeNightWork({ id: "evt-2", ts: 1, kind: "consolidation", data: { ops: 0 } }, undefined);
  assert.equal(quiet?.summary, "Nothing needed changing.");
});

test("when the log has no record of a night the card's last-night summary is used, and with neither there is nothing to say", () => {
  const fromCard = describeNightWork(undefined, { ops: 2, rejected: 1, by: "smart", notes: "" });
  assert.equal(fromCard?.summary, "2 changes to memory. 1 was turned down.");
  assert.equal(fromCard?.by, "Sorted by the night model");
  assert.equal(describeNightWork(undefined, undefined), null);
  assert.equal(byWords(null), null);
});

test("only finished sorts are matched to the snapshot they took", () => {
  const events: VillageEvent[] = [
    { id: "evt-1", ts: 1, kind: "consolidation", data: { snapshot: 1 } },
    { id: "evt-2", ts: 2, kind: "consolidation", data: { snapshot: 2, failed: true } },
    { id: "evt-3", ts: 3, kind: "tick", data: { snapshot: 3 } },
  ];
  assert.deepEqual([...nightsBySnapshot(events).keys()], [1]);
});

test("a snapshot's contents are listed in words", () => {
  assert.equal(pathWords(["memory", "impressions", "diary"]), "its memory, its impressions and its diary");
  assert.equal(pathWords(["memory"]), "its memory");
  assert.equal(pathWords([]), "nothing");
});

test("the rollback says the diary is only restored when it is asked for, and that it can be undone", () => {
  const without = rollbackEffects(4, false, "Wren");
  assert.ok(without.restored.some((line) => /diary is not touched/.test(line)));
  const withDiary = rollbackEffects(4, true, "Wren");
  assert.ok(withDiary.restored.some((line) => /diary goes back too/.test(line)));
  assert.match(withDiary.undo, /saved as a new snapshot/);
  assert.match(without.untouched.join(" "), /Wren/);
});

test("a rollback in progress, finished and refused are each said in words", () => {
  const waiting = commandProgress({ kind: "rollback", state: undefined, elapsedMs: 0, processOk: true });
  assert.equal(waiting.phase, "waiting");
  assert.match(waiting.text, /pause/);
  const done = commandProgress({ kind: "rollback", state: { done: true, result: { ok: true, to: 3, undo: 5, paths: ["memory", "impressions"] } }, elapsedMs: 900, processOk: true });
  assert.equal(done.phase, "done");
  assert.match(done.text, /Rolled back to snapshot 3\./);
  assert.match(done.text, /Restored: memory, impressions\./);
  assert.match(done.text, /snapshot 5/);
  const refused = commandProgress({ kind: "rollback", state: { done: true, result: { ok: false, error: "There is no snapshot 9 for agent-a. Nothing was changed." } }, elapsedMs: 900, processOk: true });
  assert.equal(refused.phase, "refused");
  assert.match(refused.text, /no snapshot 9/);
  const stalled = commandProgress({ kind: "rollback", state: undefined, elapsedMs: 100, processOk: false });
  assert.equal(stalled.phase, "stalled");
});
