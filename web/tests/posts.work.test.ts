// The Posts page: who worked on a piece, its record, and the page form's fields.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { declineReason, pageFromForm, parseHours } from "../../apps/posts/web/lib/forms.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { cameFrom, whoWorked, workLines } from "../../apps/posts/web/lib/work.js";

const turns = [
  { id: 1, step: "proposed", role: "owner", gloss: "Started from a link you pasted (news.example.com).", created: 1 },
  { id: 2, step: "reading", role: "code", gloss: "Read the article: 900 characters in 9 numbered sentences.", created: 2 },
  { id: 3, step: "writing", role: "smart", model: "big-model", local: 0, gloss: "Drafted it.", created: 3 },
  { id: 4, step: "in_check", role: "default", model: "small-model", local: 1, gloss: "Nothing to raise.", created: 4 },
  { id: 5, step: "ready", role: "owner", gloss: "You sent it back: shorter, please.", created: 5 },
  { id: 6, step: "revising", role: "smart", model: "big-model", local: 0, gloss: "Shortened it.", created: 6 },
];

test("the line on where a piece came from is the first step of its record", () => {
  assert.equal(cameFrom(turns), "From a link you pasted (news.example.com)");
  assert.equal(cameFrom([]), "Where it came from was not recorded");
});

test("who worked on a piece is said by role, writer apart from checker, with where each ran", () => {
  assert.equal(whoWorked(turns),
    "Wrote: the “smart” role (big-model, hosted). Checked: the “default” role (small-model, on your computer). You sent it back once.");
  assert.equal(whoWorked(turns.slice(0, 2)), "No model has worked on it yet.");
});

test("the record has one line a step, the owner's as You and the code's as Posts", () => {
  const lines = workLines(turns);
  assert.equal(lines.length, turns.length);
  assert.deepEqual(lines.map((l: { who: string }) => l.who).slice(0, 3), ["You", "Posts", "the “smart” role (big-model, hosted)"]);
  assert.equal(lines[3].step, "Checked the draft");
});

test("the page form becomes what pages.save takes", () => {
  const made = pageFromForm({ name: " Garden notes ", register: "Warm.", samples: "a\r\nb", timezone: "", hours: "08:00, 17:30", accounts: [], tags: "#garden  #local", book_through: " buffer " }, "3");
  assert.deepEqual(made.page, { name: "Garden notes", register: "Warm.", samples: "a\nb", timezone: "", hours: ["08:00", "17:30"],
    accounts: [], tags: ["#garden", "#local"], book_through: "buffer", id: 3 });
  assert.equal(pageFromForm({ name: "New" }).page.book_through, "");
  assert.equal(pageFromForm({ name: "New" }).page.id, undefined);
  assert.deepEqual(parseHours(""), []);
  const offered = [{ id: "socialbu", label: "SocialBu", sends_out: true }];
  assert.match(pageFromForm({ name: "New", accounts: [{ scheduler: "socialbu", account_id: "", label: "Main" }] }, null, offered).error, /^Account 1 needs its account id/);
});

test("a decline reason starts with the chosen reason, so the app files it under that reason", () => {
  assert.equal(declineReason("Voice off", "too stiff"), "Voice off: too stiff");
  assert.equal(declineReason("Other", ""), "Other");
  assert.equal(declineReason("", "no list"), "no list");
});
