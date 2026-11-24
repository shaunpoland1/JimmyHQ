// The Posts page: what waits on the owner beyond a draft, and the way out of each.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { askChips, buttonKind, cancelQuestion, noticeButtons, noticeChip, troubleButtons, troubleChip, wayQuestion } from "../../apps/posts/web/lib/trouble.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { queue } from "../../apps/posts/web/lib/lists.js";

const FAILED = {
  kind: "failed", said: "It could not publish it.",
  actions: [{ call: "post.book_again", label: "Put it back in the queue" }, { call: "post.cancel", label: "Decline it", kind: "danger" }],
};

test("a post the scheduler could not publish says so and offers its ways out in order", () => {
  assert.deepEqual(troubleChip(FAILED, "SocialBu"), { text: "SocialBu could not publish it", kind: "ask" });
  assert.deepEqual(troubleButtons(FAILED).map((b: { call: string }) => b.call), ["post.book_again", "post.cancel"]);
  assert.equal(troubleButtons(FAILED)[1].kind, "danger");
});

test("every kind of scheduler trouble reads as words, and none means no chip", () => {
  for (const kind of ["failed", "missing", "lost", "no_answer", "partial", "out_of_step"]) {
    const chip = troubleChip({ kind, actions: [] }, "SocialBu");
    assert.ok(chip && chip.text.length > 5, kind);
  }
  assert.equal(troubleChip(null), null);
  assert.deepEqual(troubleButtons(undefined), []);
});

test("a failed or missing post stays in the queue, where its way out is", () => {
  const posts = [{ id: 1, state: "failed", slot_ms: 5 }, { id: 2, state: "missing", slot_ms: 3 }, { id: 3, state: "went_out", slot_ms: 1 }];
  assert.deepEqual(queue(posts).map((p: { id: number }) => p.id), [2, 1]);
});

test("a quiet way out is never the first choice, and otherwise the first is primary", () => {
  assert.equal(buttonKind({ kind: "quiet" }, true), "");
  assert.equal(buttonKind({}, true), "primary");
  assert.equal(buttonKind({}, false), "");
  assert.equal(buttonKind({ kind: "danger" }, true), "danger");
});

test("a post not found after its time says so and does not put Put it back first", () => {
  const lost = { kind: "lost", actions: [{ call: "post.book_again", label: "I checked: it did not go out. Put it back in the queue", kind: "quiet" }, { call: "post.cancel", label: "Decline it", kind: "danger" }] };
  assert.equal(troubleChip(lost, "Buffer")?.text, "Not found on Buffer after its time");
  assert.deepEqual(troubleButtons(lost).map((b: { kind: string }) => buttonKind(b, false)), ["", "danger"]);
});

test("the question before a way out is the server's own, naming the tool", () => {
  const rest = { kind: "partial", actions: [{ call: "post.book", label: "Book the rest (Second)", ask: "Look on {tool} for Second first." }] };
  const [way] = troubleButtons(rest);
  assert.equal(way.call, "post.book");
  assert.equal(wayQuestion(way, "Buffer"), "Look on Buffer for Second first.");
  assert.equal(wayQuestion({ call: "post.clear", label: "x" }, "Buffer"), "Look on Buffer first.");
});

test("stuck agent work offers the card's own choices, Drop marked as dangerous", () => {
  const notice = { work_id: 4, role: "writer", why: "The writer is late.", actions: [{ id: "wait", label: "Wait" }, { id: "finish_plain", label: "Finish without agents" }, { id: "drop", label: "Drop" }] };
  assert.equal(noticeChip(notice)?.text, "Waiting for you: the writer");
  assert.deepEqual(noticeButtons(notice).map((b: { action: string; kind: string }) => [b.action, b.kind]),
    [["wait", "primary"], ["finish_plain", ""], ["drop", "danger"]]);
  assert.equal(noticeChip(null), null);
});

test("a question only the owner can answer, did this happen, is named first", () => {
  const chips = askChips([{ check: "numbers", detail: "" }, { check: "lived", detail: "" }, { check: "quotes", detail: "" }]);
  assert.deepEqual(chips.map((c: { text: string }) => c.text), ["Asks if it happened", "2 questions open"]);
  assert.deepEqual(askChips([]), []);
});

test("the way out of cancelling a post is Keep the post, never Cancel beside Cancel the post", () => {
  for (const state of ["queued", "booked", "failed", "missing"]) {
    const q = cancelQuestion({ state }, "SocialBu");
    assert.equal(q.no, "Keep the post", state);
    assert.notEqual(q.no.toLowerCase(), "cancel", state);
    assert.notEqual(q.yes, q.no, state);
  }
});

test("what is said after a post is cancelled or declined matches the button that was pressed", () => {
  const cancelled = cancelQuestion({ state: "queued" });
  assert.equal(cancelled.yes, "Cancel the post");
  assert.match(cancelled.done, /^Cancelled\./);
  const declined = cancelQuestion({ state: "failed" }, "SocialBu");
  assert.equal(declined.yes, "Decline it");
  assert.match(declined.done, /^Declined\./);
});

test("cancelling a booked post says it is taken off the scheduler first", () => {
  assert.match(cancelQuestion({ state: "booked" }, "SocialBu").intro, /taken off SocialBu first/);
});
