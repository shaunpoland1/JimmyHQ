// The Posts page: what each view lists, Today, and the search and page filters.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { draftsToDecide, dropped, filterRows, inTheWorks, isPracticePost, queue, wentOut } from "../../apps/posts/web/lib/lists.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { NOTHING, needs, stuckSteps, underWay, waiting } from "../../apps/posts/web/lib/today.js";

const post = (id: number, more: Record<string, unknown> = {}) => ({
  id, piece_id: id * 10, page_id: 1, title: "", body: `Post ${id}`, state: "draft", piece_state: "ready",
  slot_ms: null, updated: id, questions: 0, warnings: 0, out_of_step: false, note: "", ...more,
});
const piece = (id: number, more: Record<string, unknown> = {}) => ({ id, page_id: 1, state: "writing", words: "drafting", created: id, updated: id, post_id: null, error: "", ...more });
const pages = [{ id: 1, name: "Garden notes" }, { id: 2, name: "Valley news" }];

test("with nothing to do, Today says so plainly", () => {
  const data = { pages, posts: [post(1, { state: "went_out" })], pieces: [], scheduler: { label: "Practice" } };
  assert.deepEqual(needs(data), []);
  assert.equal(NOTHING, "Nothing needs you right now.");
});

test("Today lists drafts, unanswered bookings, posts out of step and stopped pieces, in that order", () => {
  const data = {
    pages, scheduler: { label: "Practice" },
    posts: [post(1, { questions: 2 }), post(2, { state: "unsure" }), post(3, { state: "booked", out_of_step: true, note: "This change did not reach Practice." }), post(4, { state: "queued" })],
    pieces: [piece(5, { state: "stalled", error: "The model gave back an empty draft." }), piece(6)],
  };
  const groups = needs(data);
  assert.deepEqual(groups.map((g: { key: string }) => g.key), ["drafts", "unsure", "out_of_step", "stuck", "untimed"]);
  assert.equal(groups[0].items[0].detail, "2 questions open");
  assert.equal(groups[0].items[0].go.view, "drafts");
  assert.equal(groups[2].items[0].detail, "This change did not reach Practice.");
  assert.equal(groups[3].items[0].detail, "The model gave back an empty draft.");
  assert.equal(underWay(data), "1 piece being worked on.");
});

test("a first run with no pages leads to Pages", () => {
  const groups = needs({ pages: [], posts: [], pieces: [], hint: "You have no pages yet." });
  assert.equal(groups[0].key, "pages");
  assert.equal(groups[0].items[0].go.view, "pages");
});

test("the hint on a first run is shown on Today but never counted as something waiting", () => {
  const first = needs({ pages: [], posts: [], pieces: [], hint: "You have no pages yet." });
  assert.equal(first.length, 1);
  assert.equal(waiting(first), 0);
  const withDraft = needs({ pages: [], posts: [post(1)], pieces: [] });
  assert.equal(waiting(withDraft), 1);
  const real = needs({ pages, scheduler: { label: "Practice" }, posts: [post(1), post(2, { state: "unsure" })], pieces: [piece(5, { state: "stalled" })] });
  assert.equal(waiting(real), 3);
  assert.equal(waiting([]), 0);
  assert.equal(waiting(undefined), 0);
});

test("the sidebar badge is the program's own number, else counted through waiting; the Today tab counts through waiting, never the raw lines", () => {
  const text = readFileSync(new URL("../../apps/posts/web/app.js", import.meta.url), "utf8");
  assert.ok(text.includes("Number.isInteger(state.data.waiting) ? state.data.waiting : waiting(needs(state.data))"));
  assert.ok(text.includes("hq.ui.badge(said)"));
  assert.ok(text.includes("today: waiting(needs(data))"));
  assert.ok(!text.includes("g.items.length"));
});

test("drafts wait in the order they came; a draft still being worked on is not one to decide", () => {
  const posts = [post(2, { updated: 50 }), post(1, { updated: 10 }), post(3, { piece_state: "revising" })];
  assert.deepEqual(draftsToDecide(posts).map((p: { id: number }) => p.id), [1, 2]);
  const working = inTheWorks([piece(20, { state: "ready" }), piece(7), piece(8, { state: "dropped" })], posts);
  assert.deepEqual(working.map((p: { id: number }) => p.id), [7]);
});

test("the queue is in slot order with untimed posts last, and leaves out what went out", () => {
  const posts = [post(1, { state: "queued", slot_ms: 300 }), post(2, { state: "booked", slot_ms: 100 }), post(3, { state: "queued" }), post(4, { state: "went_out", slot_ms: 50 })];
  assert.deepEqual(queue(posts).map((p: { id: number }) => p.id), [2, 1, 3]);
  assert.deepEqual(wentOut(posts).map((p: { id: number }) => p.id), [4]);
});

test("search matches every word in any order and any case, within the chosen page", () => {
  const rows = [
    post(1, { state: "went_out", title: "Solar roofs", body: "Panels on 1,240 homes" }),
    post(2, { state: "went_out", page_id: 2, body: "Roofs and rain in the valley" }),
    post(3, { state: "went_out", body: "Bees in the orchard" }),
  ];
  assert.deepEqual(filterRows(rows, { query: "ROOFS" }).map((p: { id: number }) => p.id), [1, 2]);
  assert.deepEqual(filterRows(rows, { query: "homes solar" }).map((p: { id: number }) => p.id), [1]);
  assert.deepEqual(filterRows(rows, { query: "roofs", page: "2" }).map((p: { id: number }) => p.id), [2]);
  assert.deepEqual(filterRows(rows, { query: "", page: "" }).length, 3);
  assert.deepEqual(filterRows(rows, { query: "nothing like it" }), []);
});

test("Dropped lists declined posts and dropped pieces that never had a post, newest first", () => {
  const rows = dropped([post(1, { state: "declined", note: "Declined: Voice off.", updated: 5 }), post(2, { state: "went_out" })],
    [piece(9, { state: "dropped", topic: "Hedgehogs", updated: 9 }), piece(10, { state: "dropped", post_id: 1 })]);
  assert.deepEqual(rows.map((r: { kind: string; id: number }) => `${r.kind}:${r.id}`), ["piece:9", "post:1"]);
  assert.equal(rows[1].note, "Declined: Voice off.");
  assert.deepEqual(filterRows(rows, { query: "hedgehogs" }).map((r: { id: number }) => r.id), [9]);
});

test("a post that went out on Practice is marked as Practice in Went out, and no other is", () => {
  assert.equal(isPracticePost({ state: "went_out", scheduler: "practice" }), true);
  assert.equal(isPracticePost({ state: "went_out", scheduler: "socialbu" }), false);
  assert.equal(isPracticePost({ state: "went_out", scheduler: "by_hand" }), false);
  assert.equal(wentOut([{ id: 1, state: "went_out", scheduler: "practice", slot_ms: 1 }]).length, 1);
});

test("a piece that has sat drafting for longer than a step may take is named with the likely reason, not passed over", () => {
  const now = 10 * 3_600_000;
  const data = { pages, now_ms: now, slow_minutes: 30, posts: [], pieces: [piece(1, { updated: now - 3 * 3_600_000, topic: "Spring pruning" }), piece(2, { updated: now - 5 * 60_000 }), piece(3, { state: "reading", updated: 0 })] };
  assert.deepEqual(needs(data), []);
  const stuck = stuckSteps(data);
  assert.equal(stuck.length, 1);
  assert.match(stuck[0].text, /Garden notes: Spring pruning has been drafting for 3 hours and has not moved/);
  assert.match(stuck[0].detail, /No model has answered/);
  assert.match(stuck[0].detail, /Settings, Models and roles/);
  assert.match(stuck[0].detail, /Village/);
  assert.deepEqual(stuckSteps({ pages, posts: [], pieces: [] }), []);
});

test("a piece stuck for one minute is said to have been stuck for 1 minute, not 1 minutes", () => {
  const now = 10 * 3_600_000;
  const stuck = stuckSteps({ pages, now_ms: now, slow_minutes: 0.5, posts: [], pieces: [piece(1, { updated: now - 60_000, topic: "Spring pruning" })] });
  assert.match(stuck[0].text, /for 1 minute and has not moved/);
});
