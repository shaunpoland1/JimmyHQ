// The Posts page: each page may book through its own tool, and the screen says which.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { bookThroughChoices, bookedThroughWords, isPractice, nameList, notices, toolLabelOfPost, toolOfPage, waitNotices } from "../../apps/posts/web/lib/tools.js";

const PRACTICE = { id: "practice", label: "Practice", sends_out: false, known: true };
const BY_HAND = { id: "by_hand", label: "By hand", sends_out: false, known: true };
const BUFFER = { id: "buffer", label: "Buffer", sends_out: true, known: true };
const page = (id: number, name: string, tool: object, own = false, lost = false) => ({ id, name, books_through: { ...tool, own, lost } });

test("names are listed as a sentence", () => {
  assert.equal(nameList([]), "");
  assert.equal(nameList(["A"]), "A");
  assert.equal(nameList(["A", "B"]), "A and B");
  assert.equal(nameList(["A", "B", "C"]), "A, B and C");
});

test("with every page on Practice the notice is the loud one about the whole app, as before", () => {
  const data = { scheduler: PRACTICE, pages: [page(1, "Garden", PRACTICE), page(2, "Kitchen", PRACTICE)] };
  const [only] = notices(data);
  assert.equal(notices(data).length, 1);
  assert.equal(only.kind, "practice");
  assert.equal(only.strong, "Practice: nothing is really being booked. ");
  assert.match(only.text, /^Posts records what it would send, and nothing leaves this computer\./);
});

test("the notice points to the setting by its real label", () => {
  const data = { scheduler: PRACTICE, pages: [] };
  assert.match(notices(data)[0].text, /Settings, then Apps, then Posts, “Book through \(the default\)”\./);
});

test("with no page yet the notice is about Posts' setting alone", () => {
  assert.equal(notices({ scheduler: PRACTICE, pages: [] })[0].kind, "practice");
  assert.deepEqual(notices({ scheduler: BUFFER, pages: [] }), []);
  assert.deepEqual(notices({ pages: [] }), []);
});

test("when only some pages are on Practice the notice names those pages and no others", () => {
  const data = { scheduler: BUFFER, pages: [page(1, "Garden", PRACTICE, true), page(2, "Kitchen", BUFFER), page(3, "Shed", PRACTICE, true)] };
  const [only] = notices(data);
  assert.equal(notices(data).length, 1);
  assert.equal(only.kind, "practice");
  assert.equal(only.strong, "Practice: nothing is really being booked for Garden and Shed. ");
  assert.match(only.text, /for them, and nothing leaves this computer/);
  assert.ok(!only.strong.includes("Kitchen") && !only.text.includes("Kitchen"));
});

test("a page that follows a setting of Practice is named too, beside pages that chose a real tool", () => {
  const data = { scheduler: PRACTICE, pages: [page(1, "Garden", BUFFER, true), page(2, "Kitchen", PRACTICE)] };
  const [only] = notices(data);
  assert.equal(only.strong, "Practice: nothing is really being booked for Kitchen. ");
  assert.match(only.text, /for it, and nothing leaves/);
});

test("pages on different tools that book nothing each get their own notice", () => {
  const data = { scheduler: PRACTICE, pages: [page(1, "Garden", PRACTICE), page(2, "Kitchen", BY_HAND, true), page(3, "Shed", BUFFER, true)] };
  assert.deepEqual(notices(data).map((n: { kind: string }) => n.kind), ["practice", "plain"]);
  assert.equal(notices(data)[1].strong, "By hand for Kitchen. ");
});

test("a tool Posts does not know is said loudly, for the pages it books", () => {
  const unknown = { id: "gone", label: "gone", sends_out: false, known: false };
  const all = notices({ scheduler: unknown, pages: [page(1, "Garden", unknown)] });
  assert.equal(all[0].kind, "bad");
  assert.equal(all[0].strong, "Nothing can be booked. ");
  const some = notices({ scheduler: BUFFER, pages: [page(1, "Garden", unknown), page(2, "Kitchen", BUFFER)] });
  assert.equal(some[0].strong, "Nothing can be booked for Garden. ");
});

test("a tool that sends out needs no notice", () => {
  assert.deepEqual(notices({ scheduler: BUFFER, pages: [page(1, "Garden", BUFFER)] }), []);
});

test("the tool for a page is its own, else the setting; for a post, the one it was booked with first", () => {
  const data = { scheduler: PRACTICE, pages: [page(1, "Garden", BUFFER, true), { id: 2, name: "Kitchen" }] };
  assert.equal(toolOfPage(data, 1).id, "buffer");
  assert.equal(toolOfPage(data, 2).id, "practice");
  assert.equal(toolLabelOfPost(data, { page_id: 1 }), "Buffer");
  assert.equal(toolLabelOfPost(data, { page_id: 1, tool: { id: "socialbu", label: "SocialBu" } }), "SocialBu");
  assert.equal(toolLabelOfPost({}, { page_id: 9 }), "the scheduler");
  assert.equal(isPractice(data, { page_id: 2 }), true);
  assert.equal(isPractice(data, { page_id: 1 }), false);
  assert.equal(isPractice(data, { page_id: 1, tool: { id: "practice", label: "Practice" } }), true);
});

test("the select starts with Posts' setting and says what it is now", () => {
  const offered = [{ id: "practice", label: "Practice" }, { id: "buffer", label: "Buffer" }];
  const choices = bookThroughChoices(offered, BUFFER);
  assert.deepEqual(choices[0], { value: "", label: "Posts' setting (currently Buffer)" });
  assert.deepEqual(choices.slice(1).map((c: { value: string }) => c.value), ["practice", "buffer"]);
  assert.equal(bookThroughChoices(offered, null)[0].label, "Posts' setting (currently not known)");
});

test("a stored choice that is no longer offered is shown as such, never dropped silently", () => {
  const choices = bookThroughChoices([{ id: "practice", label: "Practice" }], PRACTICE, "retired");
  assert.deepEqual(choices.at(-1), { value: "retired", label: "retired (no longer offered)" });
  assert.equal(bookThroughChoices([{ id: "practice", label: "Practice" }], PRACTICE, "practice").length, 2);
});

test("a page's card says which tool books it and why", () => {
  assert.equal(bookedThroughWords(page(1, "G", BUFFER, true), PRACTICE), "books through Buffer");
  assert.equal(bookedThroughWords(page(1, "G", PRACTICE), PRACTICE), "books through Practice (Posts' setting)");
  assert.match(bookedThroughWords(page(1, "G", PRACTICE, false, true), PRACTICE), /no longer offered/);
  assert.equal(bookedThroughWords({ id: 1 }, PRACTICE), "");
});

test("a scheduler that asked Posts to wait is named on the queue with the time it waits until", () => {
  const data = { now_ms: 1000, waits: [{ scheduler: "buffer", label: "Buffer", until_ms: 5000 }, { scheduler: "old", label: "Old", until_ms: 500 }] };
  const [one, ...rest] = waitNotices(data, (ms: number) => `at ${ms}`);
  assert.equal(rest.length, 0);
  assert.equal(one.kind, "bad");
  assert.equal(one.strong, "Buffer asked Posts to wait. ");
  assert.match(one.text, /until at 5000\./);
  assert.deepEqual(waitNotices({ now_ms: 1 }), []);
});
