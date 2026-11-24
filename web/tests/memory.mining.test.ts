import assert from "node:assert/strict";
import { test } from "node:test";
import type { ActivityEntry, Candidate, CandidateCounts, MineResult, Mining, Tally } from "../src/modules/memory/api.ts";
import { activityFactText, activityNote, actorSubject, dayHeading, dayKey, describeActivity, groupByDay } from "../src/modules/memory/lib/activity.ts";
import {
  VIEWS,
  canDecline,
  canKeep,
  checkReason,
  confidenceNumber,
  countsAfter,
  flattenCandidates,
  matchesFilter,
  predict,
  quoteAuthor,
  routeFor,
  settleInPages,
  stateWords,
  uniqueCandidates,
  viewCount,
} from "../src/modules/memory/lib/candidates.ts";
import type { CandidatePages } from "../src/modules/memory/lib/candidates.ts";
import { awaitsReview, describeActor } from "../src/modules/memory/lib/facts.ts";
import {
  benchLine,
  breakdown,
  describeMine,
  dropReasonWords,
  miningHeadline,
  modelPlace,
  outcomesLine,
  sectionsFor,
  skipReasonWords,
  sourceState,
  tallyFromCounts,
  tallyFromFlat,
  tallySentence,
  thresholdScale,
  touchesMining,
  touchesSources,
  waitingWords,
  withSourceSwitched,
} from "../src/modules/memory/lib/mining.ts";
import { factPurged, notePurged, noteWarning } from "../src/modules/memory/lib/purge.ts";

const ID = (n: number) => `01JABCDEFGHJKMNPQRSTVWXY${String(n).padStart(2, "0")}`;

function tally(over: Partial<Tally> = {}): Tally {
  return { read: 0, skipped: {}, skipped_total: 0, proposed: 0, saved: 0, sent: 0, ignored: 0, dropped: {}, dropped_total: 0, ...over };
}

function candidate(n: number, over: Partial<Candidate> = {}): Candidate {
  return {
    id: ID(n),
    source: "chat",
    statement: `Statement ${n}`,
    kind: "note",
    confidence: 0.7,
    evidence: ["tape:01JABCDEFGHJKMNPQRSTVWXYZ0"],
    quotes: [],
    proposed_ms: 1000 + n,
    state: "bench",
    state_ms: 1000 + n,
    reason: null,
    fact_id: null,
    card_id: null,
    saved_automatically: false,
    needs_review: false,
    ...over,
  };
}

const COUNTS: CandidateCounts = { waiting: 1, bench: 2, saved: 3, declined: 0, ignored: 4, withdrawn: 0, review: 2 };

// ---- reasons ----

test("every reason the server drops a fact for is said in plain words", () => {
  for (const reason of ["duplicate", "declined", "no_evidence", "not_from_owner", "secret", "too_long", "invalid"]) {
    assert.notEqual(dropReasonWords(reason), reason);
  }
  assert.equal(dropReasonWords("duplicate"), "already known");
  assert.equal(dropReasonWords("declined"), "you said no before");
  assert.equal(dropReasonWords("not_from_owner"), "not your words");
  assert.equal(dropReasonWords("secret"), "looked like a secret");
});

test("every reason the server skips an item for is said in plain words", () => {
  for (const reason of ["archived", "not_owner", "incomplete", "off_record_question", "gone", "own_card", "empty", "too_long", "instruction", "unreadable", "model_failed"]) {
    assert.notEqual(skipReasonWords(reason), reason);
  }
});

test("a reason the screen does not know is shown by its raw name, not hidden", () => {
  assert.equal(dropReasonWords("brand_new"), "brand_new");
  assert.equal(skipReasonWords("brand_new"), "brand_new");
  const b = breakdown(tally({ proposed: 2, dropped: { brand_new: 2 }, dropped_total: 2 }));
  assert.deepEqual(b.droppedBy, [{ reason: "brand_new", words: "brand_new", n: 2 }]);
  assert.equal(b.unaccounted, 0);
});

// ---- tallies ----

test("a tally that adds up has nothing unaccounted for", () => {
  const b = breakdown(tally({ read: 4, proposed: 7, saved: 2, sent: 2, ignored: 1, dropped: { duplicate: 0, no_evidence: 1, secret: 1 }, dropped_total: 2 }));
  assert.equal(b.unaccounted, 0);
  assert.equal(b.dropped, 2);
  assert.deepEqual(
    b.droppedBy.map((item) => item.reason),
    ["no_evidence", "secret"],
  );
  assert.equal(outcomesLine(b), "2 saved automatically, 2 sent to the Bench, 1 ignored, 2 dropped (no evidence 1, looked like a secret 1)");
});

test("a tally that does not add up says so", () => {
  assert.match(outcomesLine(breakdown(tally({ proposed: 5, saved: 1, sent: 1 }))), /3 not accounted for/);
  assert.match(outcomesLine(breakdown(tally({ proposed: 1, saved: 2 }))), /1 more than were proposed/);
});

test("dropped and skipped totals are worked out from the reasons when the totals are missing", () => {
  const b = breakdown({ ...tally({ proposed: 3, dropped: { duplicate: 3 }, skipped: { archived: 2, empty: 1 } }) });
  assert.equal(b.dropped, 3);
  assert.equal(b.skipped, 3);
  assert.equal(b.skippedBy[0]?.words, "in an archived conversation");
});

test("the flat totals of a pass become a tally", () => {
  const flat = { read: 6, proposed: 14, saved: 2, sent: 2, ignored: 1, skipped: 3, "skipped.archived": 3, "dropped.duplicate": 5, "dropped.no_evidence": 2, "dropped.secret": 2, "dropped.invalid": 0 };
  const made = tallyFromFlat(flat);
  assert.equal(made.dropped_total, 9);
  assert.equal(made.skipped_total, 3);
  assert.deepEqual(made.skipped, { archived: 3 });
  assert.equal(breakdown(made).unaccounted, 0);
  // A bare `skipped` with no reasons is still counted.
  assert.equal(tallyFromFlat({ skipped: 4 }).skipped_total, 4);
  // Text and other fields beside the counts are ignored.
  assert.equal(tallyFromFlat({ source: "chat", trigger: "owner", read: 1 }).read, 1);
});

test("one source's counts from a pass become a tally with totals", () => {
  const made = tallyFromCounts({ read: 2, skipped: { empty: 1 }, proposed: 7, saved: 0, sent: 0, ignored: 0, dropped: { duplicate: 5, no_evidence: 1, secret: 1 }, error: null });
  assert.equal(made.dropped_total, 7);
  assert.equal(made.skipped_total, 1);
});

test("a pass is described in sentences", () => {
  assert.equal(tallySentence(tally()), "There was nothing new to read.");
  assert.equal(tallySentence(tally({ read: 1 })), "Read 1 item. No fact was proposed.");
  assert.equal(
    tallySentence(tally({ read: 4, skipped: { archived: 1 }, proposed: 2, saved: 1, sent: 1 })),
    "Read 4 items. Passed over 1 (in an archived conversation 1). 2 facts proposed: 1 saved automatically, 1 sent to the Bench.",
  );
});

test("Mine now says what the pass did, or the server's reason it could not run", () => {
  const ran: MineResult = {
    ran: true,
    reason: null,
    sources: { chat: { read: 4, skipped: {}, proposed: 1, saved: 0, sent: 1, ignored: 0, dropped: {}, error: null } },
    totals: { read: 4, proposed: 1, saved: 0, sent: 1, ignored: 0, skipped: 0 },
    filed: 1,
  };
  assert.deepEqual(describeMine(ran), { ok: true, line: "Read 4 items. 1 fact proposed: 1 sent to the Bench. 1 card filed on the Bench.", errors: [] });

  const nothing: MineResult = { ran: true, reason: null, sources: {}, totals: { read: 0, proposed: 0 }, filed: 0 };
  assert.equal(describeMine(nothing).line, "There was nothing new to read.");

  const refused: MineResult = { ran: false, reason: "Mining cannot run. Change this.", sources: {}, totals: {}, filed: 0 };
  assert.deepEqual(describeMine(refused), { ok: false, line: "Mining cannot run. Change this.", errors: [] });

  const failed: MineResult = { ...ran, sources: { chat: { read: 0, skipped: {}, proposed: 0, saved: 0, sent: 0, ignored: 0, dropped: {}, error: "The model did not answer." } } };
  const outcome = describeMine(failed, { chat: "Chat" });
  assert.equal(outcome.ok, false);
  assert.deepEqual(outcome.errors, [{ source: "Chat", message: "The model did not answer." }]);
});

// ---- the thresholds ----

test("the scale puts the two thresholds where they are", () => {
  const scale = thresholdScale(0.4, 0.85);
  assert.deepEqual(
    scale.bands.map((band) => [band.id, band.percent]),
    [
      ["ignored", 40],
      ["bench", 45],
      ["saved", 15],
    ],
  );
  assert.deepEqual(scale.marks, { ignore: 40, auto: 85 });
  assert.equal(scale.words, "Ignored below 0.4, asked on the Bench from 0.4 up to 0.85, saved automatically from 0.85.");
  assert.equal(scale.bands.reduce((total, band) => total + band.percent, 0), 100);
});

test("a threshold above 1 means nothing is saved without the owner", () => {
  const scale = thresholdScale(0.4, 1.5);
  assert.equal(scale.bands[2]?.percent, 0);
  assert.equal(scale.bands[1]?.percent, 60);
  assert.equal(scale.marks.auto, null);
  assert.match(scale.words, /nothing is saved without you/);
});

test("a first threshold of 0 ignores nothing, and thresholds that meet leave no Bench band", () => {
  assert.match(thresholdScale(0, 0.85).words, /^Nothing is ignored/);
  assert.equal(thresholdScale(0, 0.85).bands[0]?.percent, 0);
  const met = thresholdScale(0.5, 0.5);
  assert.equal(met.bands[1]?.percent, 0);
  assert.match(met.words, /nothing is sent to the Bench/);
  // A pair that crosses is drawn as meeting, never with a negative width.
  assert.ok(thresholdScale(0.5, 0.2).bands.every((band) => band.percent >= 0));
  assert.ok(thresholdScale(Number.NaN, Number.NaN).bands.every((band) => band.percent >= 0));
});

test("a confidence is routed by the thresholds the way the server does", () => {
  const mining = { ignore_below: 0.4, auto_save_above: 0.85 };
  assert.equal(routeFor(0.85, mining), "saved");
  assert.equal(routeFor(0.84, mining), "bench");
  assert.equal(routeFor(0.4, mining), "bench");
  assert.equal(routeFor(0.39, mining), "ignored");
});

// ---- the overall line ----

test("the reason's own words pick the settings section to link", () => {
  assert.deepEqual(sectionsFor(null), []);
  assert.deepEqual(sectionsFor("Mining is switched off. Turn it on under Settings, Memory."), ["memory"]);
  assert.deepEqual(sectionsFor("No model is chosen. Choose one under Settings, Models, or pick another role under Settings, Memory."), ["models", "memory"]);
  assert.deepEqual(sectionsFor("Something else is wrong."), ["memory"]);
});

test("the overall state of mining is said in a few words", () => {
  assert.equal(miningHeadline({ on: true, working: true }), "Mining is working");
  assert.equal(miningHeadline({ on: true, working: false }), "Mining is not happening");
  assert.equal(miningHeadline({ on: false, working: false }), "Mining is switched off");
  const model: Mining["model"] = { role: "cheap", name: "home/example-model", is_local: true, local_only: true };
  assert.equal(modelPlace(model), "runs on your own machines");
  assert.match(modelPlace({ ...model, is_local: false }), /^hosted/);
  assert.equal(modelPlace({ ...model, name: null, is_local: null }), "No model can be asked");
  assert.equal(benchLine({ open_cards: 2, max_open_cards: 5, waiting_for_bench: 0 }), "2 of 5 Bench cards open");
  assert.equal(benchLine({ open_cards: 5, max_open_cards: 5, waiting_for_bench: 3 }), "5 of 5 Bench cards open, 3 more waiting for a slot");
});

test("what waits to be read is worded, and unknown until the worker has counted", () => {
  assert.equal(waitingWords({ items_waiting: null, items_waiting_more: false }), null);
  assert.equal(waitingWords({ items_waiting: 0, items_waiting_more: false }), "none waiting");
  assert.equal(waitingWords({ items_waiting: 12, items_waiting_more: false }), "12 waiting");
  assert.equal(waitingWords({ items_waiting: 20, items_waiting_more: true }), "at least 20 waiting");
});

test("a source says whether it is being read, and why not", () => {
  assert.deepEqual(sourceState({ on: true }, { on: true, working: true }), { reading: true, words: "Being read" });
  assert.equal(sourceState({ on: false }, { on: true, working: true }).words, "Off: not read");
  assert.equal(sourceState({ on: true }, { on: false, working: false }).reading, false);
  assert.match(sourceState({ on: true }, { on: true, working: false }).words, /mining is not happening/);
});

test("switching a source shows at once and touches no other source", () => {
  const source = (id: string, on: boolean) => ({ id, label: id, description: "", on, setting: `memory.source.${id}`, last_read_ms: null, items_read: 0, items_waiting: null, items_waiting_more: false, items_waiting_as_of_ms: null, last_run: tally(), all_time: tally(), last_error: null });
  const data = { sources: [source("chat", true), source("tasks", true)] };
  const next = withSourceSwitched(data, "tasks", false);
  assert.deepEqual(
    next.sources.map((item) => item.on),
    [true, false],
  );
  assert.equal(next.sources[0], data.sources[0]);
});

// ---- staying current ----

test("memory events and Bench events about memory's own cards refresh the mining panels", () => {
  assert.equal(touchesMining({ kind: "memory.mined", payload: {} }), true);
  assert.equal(touchesMining({ kind: "memory.candidate.settled", payload: {} }), true);
  assert.equal(touchesMining({ kind: "memory.recalled", payload: {} }), false);
  assert.equal(touchesMining({ kind: "bench.decided", payload: { card_kind: "memory.candidate" } }), true);
  assert.equal(touchesMining({ kind: "bench.filed", payload: { kind: "memory.candidate" } }), true);
  assert.equal(touchesMining({ kind: "bench.withdrawn", payload: { subject: `memory_candidate:${ID(1)}` } }), true);
  assert.equal(touchesMining({ kind: "bench.decided", payload: { card_kind: "chat.approval" } }), false);
  assert.equal(touchesMining({ kind: "task.created", payload: {} }), false);
});

test("a change to memory's settings or to the models reads the sources again", () => {
  assert.equal(touchesSources({ kind: "setting.updated", payload: { key: "memory.auto_save_above", value: 0.9 } }), true);
  assert.equal(touchesSources({ kind: "setting.reset", payload: { key: "memory.source.chat" } }), true);
  assert.equal(touchesSources({ kind: "setting.updated", payload: { key: "wall.tiles" } }), false);
  assert.equal(touchesSources({ kind: "model.role.set", payload: { role: "cheap" } }), true);
  assert.equal(touchesSources({ kind: "model.provider.saved", payload: {} }), true);
  assert.equal(touchesSources({ kind: "model.called", payload: {} }), false);
});

// ---- candidates ----

test("the default view is what needs the owner: the Bench plus what was saved automatically and not confirmed", () => {
  assert.equal(VIEWS[0]?.id, "needs");
  assert.equal(viewCount(COUNTS, "needs"), 4);
  assert.equal(viewCount(COUNTS, "ignored"), 4);
  assert.equal(viewCount(undefined, "needs"), 0);
  for (const state of ["waiting", "bench", "saved", "declined", "ignored", "withdrawn", "review"]) assert.ok(VIEWS.some((view) => view.id === state));
});

test("a candidate is listed under its state, and under review only while unconfirmed", () => {
  assert.equal(matchesFilter(candidate(1), "bench"), true);
  assert.equal(matchesFilter(candidate(1, { state: "saved", needs_review: true }), "review"), true);
  assert.equal(matchesFilter(candidate(1, { state: "saved", needs_review: true }), "saved"), true);
  assert.equal(matchesFilter(candidate(1, { state: "saved" }), "review"), false);
});

test("a candidate's state, confidence and quote author are worded", () => {
  assert.equal(stateWords(candidate(1, { state: "waiting" })), "Waiting for a Bench slot");
  assert.equal(stateWords(candidate(1, { state: "saved", saved_automatically: true, needs_review: true })), "Saved automatically, not confirmed");
  assert.equal(stateWords(candidate(1, { state: "saved", saved_automatically: true })), "Saved automatically, confirmed");
  assert.equal(stateWords(candidate(1, { state: "saved" })), "Saved");
  assert.equal(confidenceNumber(0.7), "0.70");
  assert.equal(confidenceNumber(7), "1.00");
  assert.equal(quoteAuthor("owner"), "you");
  assert.equal(quoteAuthor("assistant"), "the model");
  assert.equal(quoteAuthor("agent:scout"), "agent:scout");
});

test("Keep and Decline are offered only where the server accepts them", () => {
  assert.equal(canKeep(candidate(1)), true);
  assert.equal(canKeep(candidate(1, { state: "saved", needs_review: true })), true);
  assert.equal(canKeep(candidate(1, { state: "saved" })), false);
  assert.equal(canKeep(candidate(1, { state: "declined", statement: "" })), false);
  assert.equal(canDecline(candidate(1)), true);
  assert.equal(canDecline(candidate(1, { state: "declined" })), false);
});

test("declining needs a reason of 1 to 300 characters", () => {
  assert.deepEqual(checkReason("   "), { ok: false, error: "A reason is needed to decline. Say why in a sentence." });
  assert.deepEqual(checkReason("  Not   true any more. "), { ok: true, reason: "Not true any more." });
  assert.equal(checkReason("x".repeat(301)).ok, false);
  assert.equal(checkReason("x".repeat(300)).ok, true);
});

test("counts follow a candidate from one state to another and never go below zero", () => {
  const kept = countsAfter(COUNTS, candidate(1), predict(candidate(1), "keep", null, 5));
  assert.deepEqual([kept.bench, kept.saved, kept.review], [1, 4, 2]);
  const reviewed = candidate(2, { state: "saved", needs_review: true, saved_automatically: true });
  const confirmed = countsAfter(COUNTS, reviewed, predict(reviewed, "keep", null, 5));
  assert.deepEqual([confirmed.saved, confirmed.review], [3, 1]);
  const declined = countsAfter(COUNTS, reviewed, predict(reviewed, "decline", "No.", 5));
  assert.deepEqual([declined.saved, declined.declined, declined.review], [2, 1, 1]);
  const empty: CandidateCounts = { waiting: 0, bench: 0, saved: 0, declined: 0, ignored: 0, withdrawn: 0, review: 0 };
  assert.equal(countsAfter(empty, candidate(1), predict(candidate(1), "keep", null, 5)).bench, 0);
});

test("a decision takes the candidate out of a list it no longer belongs to and keeps it where it does", () => {
  const before = candidate(1);
  const after = predict(before, "decline", "Not so.", 9);
  const data: CandidatePages = { pages: [{ candidates: [before, candidate(2)], next_before: null, counts: COUNTS }], pageParams: [undefined] };
  const bench = settleInPages(data, "bench", before, after);
  assert.deepEqual(flattenCandidates(bench).map((item) => item.id), [ID(2)]);
  assert.equal(bench.pages[0]?.counts.bench, 1);
  const all = settleInPages(data, null, before, after);
  assert.equal(flattenCandidates(all)[0]?.state, "declined");
  assert.equal(flattenCandidates(all)[0]?.reason, "Not so.");
  // The cached data itself is not changed, so it can be put back.
  assert.equal(data.pages[0]?.candidates.length, 2);
});

test("a candidate that turns up in two lists is shown once", () => {
  assert.deepEqual(
    uniqueCandidates([[candidate(1), candidate(2)], [candidate(2), candidate(3)]]).map((item) => item.id),
    [ID(1), ID(2), ID(3)],
  );
});

// ---- facts saved automatically ----

test("a fact mining saved is marked for review until it is confirmed or retired", () => {
  const fact = { saved_by: "system:memory", confirmed: false, retired_ms: null };
  assert.equal(awaitsReview(fact), true);
  assert.equal(awaitsReview({ ...fact, confirmed: true }), false);
  assert.equal(awaitsReview({ ...fact, retired_ms: 5 }), false);
  assert.equal(awaitsReview({ ...fact, saved_by: "owner" }), false);
  assert.equal(describeActor("system:memory"), "mining");
});

// ---- the timeline ----

function entry(n: number, over: Partial<ActivityEntry> = {}): ActivityEntry {
  return { id: ID(n), ts: Date.UTC(2026, 0, 8, 12, 0, n), type: "fact_saved", actor: "owner", note: "shared/ideas.md", detail: {}, fact: null, ...over };
}

test("each kind of timeline entry is said in plain words", () => {
  const say = (over: Partial<ActivityEntry>) => describeActivity(entry(1, over), { chat: "Chat" });
  assert.deepEqual(say({ type: "fact_saved", actor: "system:memory", detail: { confirmed: false, revived: false } }), { line: "Mining saved a fact automatically", more: "Not confirmed yet." });
  assert.equal(say({ type: "fact_saved", detail: { confirmed: true } }).line, "You saved a fact");
  assert.equal(say({ type: "fact_saved", detail: { revived: true } }).line, "You brought back a fact");
  assert.equal(say({ type: "fact_edited", detail: { changed: ["text", "kind"] } }).line, "You changed a fact: its text, its kind");
  assert.equal(say({ type: "fact_confirmed" }).line, "You confirmed a fact");
  assert.deepEqual(say({ type: "fact_forgotten", detail: { reason: "No longer true." } }), { line: "You forgot a fact", more: "Reason: No longer true." });
  assert.equal(say({ type: "note_saved", actor: "owner:disk", detail: { created: true, moved_from: null } }).line, "An edit on disk created a note");
  assert.equal(say({ type: "note_saved", detail: { created: false, moved_from: null } }).line, "You changed a note");
  assert.deepEqual(say({ type: "note_saved", detail: { moved_from: "shared/old.md" } }), { line: "You moved a note", more: "It was shared/old.md." });
  assert.equal(say({ type: "note_removed" }).line, "You removed a note");
  assert.equal(say({ type: "candidate_kept", detail: { via: "bench" } }).line, "You kept a proposed fact on the Bench");
  assert.equal(say({ type: "candidate_declined", detail: { via: "memory" } }).line, "You declined a proposed fact in Memory");
  assert.deepEqual(say({ type: "history_purged", detail: { why: "note_deleted", versions: 2 } }), { line: "You deleted a note with its history", more: "2 stored versions erased for good." });
  assert.equal(say({ type: "history_purged", detail: { why: "forgotten_everywhere", versions: 1 } }).more, "1 stored version erased for good.");
  assert.match(say({ type: "history_purged", actor: "system", detail: { why: "retention", versions: 3 } }).line, /retention/);
});

test("a mining pass in the timeline carries its counts", () => {
  const said = describeActivity(
    entry(1, { type: "mined", actor: "system:memory", note: null, detail: { source: "chat", trigger: "owner", read: 4, proposed: 7, saved: 2, sent: 2, ignored: 1, skipped: 0, "dropped.no_evidence": 1, "dropped.secret": 1 } }),
    { chat: "Chat" },
  );
  assert.equal(said.line, "Mining read Chat, when you asked");
  assert.equal(said.more, "Read 4 items. 7 facts proposed: 2 saved automatically, 2 sent to the Bench, 1 ignored, 2 dropped (no evidence 1, looked like a secret 1).");
  assert.equal(describeActivity(entry(1, { type: "mined", detail: { source: "journal", trigger: "schedule" } })).line, "Mining read journal, on schedule");
});

test("a timeline type the screen does not know is shown by its name", () => {
  assert.equal(describeActivity(entry(1, { type: "something_new", actor: "agent:scout" })).line, "something_new (by agent scout)");
  assert.equal(actorSubject("agent:scout"), "Agent scout");
});

test("an entry links to its note unless the note is gone", () => {
  assert.equal(activityNote(entry(1)), "shared/ideas.md");
  assert.equal(activityNote(entry(1, { type: "note_removed" })), null);
  assert.equal(activityNote(entry(1, { type: "history_purged", detail: { why: "note_deleted" } })), null);
  assert.equal(activityNote(entry(1, { type: "history_purged", detail: { why: "forgotten_everywhere" } })), "shared/ideas.md");
  assert.equal(activityNote(entry(1, { note: null })), null);
});

test("a purged fact shows as no longer stored", () => {
  assert.equal(activityFactText(entry(1)), null);
  assert.deepEqual(activityFactText(entry(1, { fact: { id: ID(9), text: "Likes tea", kind: "note", retired: false } })), { text: "Likes tea", stored: true });
  assert.equal(activityFactText(entry(1, { fact: { id: ID(9), text: "", kind: null, retired: true } }))?.stored, false);
});

test("the timeline is grouped by calendar day, newest first, each entry once", () => {
  const a = entry(3, { ts: Date.UTC(2026, 0, 8, 23, 30) });
  const b = entry(2, { ts: Date.UTC(2026, 0, 8, 0, 5) });
  const c = entry(1, { ts: Date.UTC(2026, 0, 7, 23, 59) });
  const groups = groupByDay([a, b, b, c], "UTC");
  assert.deepEqual(
    groups.map((group) => [group.day, group.entries.length]),
    [
      ["2026-01-08", 2],
      ["2026-01-07", 1],
    ],
  );
  // The same moments fall on other days further east.
  assert.equal(dayKey(a.ts, "Asia/Tokyo"), "2026-01-09");
  assert.equal(groupByDay([]).length, 0);
});

test("a day is headed Today, Yesterday or by its date", () => {
  const now = Date.UTC(2026, 0, 8, 15);
  assert.equal(dayHeading(Date.UTC(2026, 0, 8, 1), now, "en-GB", "UTC"), "Today");
  assert.equal(dayHeading(Date.UTC(2026, 0, 7, 23), now, "en-GB", "UTC"), "Yesterday");
  assert.equal(dayHeading(Date.UTC(2026, 0, 5, 12), now, "en-GB", "UTC"), "Mon 5 Jan");
  assert.match(dayHeading(Date.UTC(2025, 11, 25, 12), now, "en-GB", "UTC"), /2025/);
});

// ---- deleting for good ----

test("the counts the server returns after a permanent delete are said in a sentence", () => {
  assert.equal(notePurged({ versions: 2, bodies: 2 }), "The note is deleted and 2 stored versions of it were erased (2 stored texts).");
  assert.equal(notePurged({ versions: 0, bodies: 0 }), "The note is deleted. No stored version of it was left to erase.");
  assert.equal(factPurged({ versions: 1, bodies: 1, notes: 1 }), "The fact is forgotten and 1 stored version of 1 note was erased (1 stored text).");
  assert.equal(factPurged({ versions: 0, bodies: 0, notes: 0 }), "The fact is forgotten. No stored version of a note held its line.");
  assert.match(noteWarning("shared/ideas.md", 2), /Its 2 facts are retired\. This cannot be undone\.$/);
  assert.match(noteWarning("shared/ideas.md", 0), /restored\. This cannot be undone\.$/);
});
