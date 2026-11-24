// The Posts page: what it asks before the "a" key approves a draft.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { approvalQuestion } from "../../apps/posts/web/lib/approve.js";

test("approving by key names the time and the scheduler when approving books the post", () => {
  const q = approvalQuestion({ preview: { slot_ms: 1, books: true, scheduler: "Practice", note: "" }, when: "tomorrow 08:00" });
  assert.equal(q.title, "Approve this draft?");
  assert.match(q.intro, /booked through Practice for tomorrow 08:00/);
  assert.equal(q.yes, "Approve");
});

test("a draft that only joins the queue says nothing is booked until the owner books it", () => {
  const q = approvalQuestion({ preview: { slot_ms: 1, books: false, scheduler: "SocialBu", note: "" }, when: "Friday 17:30" });
  assert.match(q.intro, /joins the queue with Friday 17:30/);
  assert.match(q.intro, /Nothing is booked until you book it/);
  assert.ok(!/booked through/.test(q.intro));
});

test("a draft with no free time says so rather than naming a time", () => {
  const q = approvalQuestion({ preview: { slot_ms: null, books: false, scheduler: "Practice", note: "" }, when: "" });
  assert.match(q.intro, /No free posting time/);
});

test("a scheduler Posts cannot use is said in the app's own sentence", () => {
  const q = approvalQuestion({ preview: { slot_ms: 1, books: false, scheduler: "", note: "Choose a scheduler in the settings." }, when: "Friday 17:30" });
  assert.match(q.intro, /nothing is booked\. Choose a scheduler in the settings\./);
});

test("open questions are counted in the same question, and the button says Approve anyway", () => {
  const one = approvalQuestion({ open: 1, preview: { books: true, scheduler: "Practice" }, when: "tomorrow 08:00" });
  assert.match(one.intro, /1 question on this draft is not answered/);
  assert.equal(one.yes, "Approve anyway");
  const two = approvalQuestion({ open: 2, preview: { books: true, scheduler: "Practice" }, when: "tomorrow 08:00" });
  assert.match(two.intro, /2 questions on this draft are not answered/);
});
