// The Posts page, Scheduled view: rows grouped by day in the owner's zone, and outside text
// kept as plain text.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { accountWords, cutWords, emptyWords, groupByDay, ownLink, picturesWords, readLine, scheduledRow, splitText } from "../../apps/posts/web/lib/scheduled.js";
// @ts-expect-error The page helper is plain JavaScript, supplied to every app's page as _ui.js.
import { h } from "../../sdk/js/ui.js";

const NOW = Date.UTC(2027, 0, 15, 8, 0); // Fri 15 Jan 2027, 08:00 UTC
const post = (id: number, when_ms: number | null, more = {}) => ({ id: String(id), account_id: "1001", when_ms, text: `Post ${id}`, pictures: 0, account: { label: "", network: "" }, own: null, ...more });

test("posts are grouped by the day they fall on in the owner's zone, not in UTC", () => {
  const rows = [
    post(1, Date.UTC(2027, 0, 16, 23, 30)), // 00:30 on the 17th in Paris
    post(2, Date.UTC(2027, 0, 16, 22, 59)), // 23:59 on the 16th in Paris
    post(3, Date.UTC(2027, 0, 16, 8, 0)),
  ];
  const paris = groupByDay(rows, { zone: "Europe/Paris", nowMs: NOW });
  assert.deepEqual(paris.map((g: { key: string }) => g.key), ["2027-01-16", "2027-01-17"]);
  assert.deepEqual(paris[0].rows.map((r: { id: string }) => r.id), ["3", "2"]);
  assert.deepEqual(paris[1].rows.map((r: { id: string; time: string }) => [r.id, r.time]), [["1", "00:30"]]);
  const utc = groupByDay(rows, { zone: "UTC", nowMs: NOW });
  assert.deepEqual(utc.map((g: { rows: unknown[] }) => g.rows.length), [3]); // the same posts, one UTC day
  const west = groupByDay(rows, { zone: "America/Los_Angeles", nowMs: NOW });
  assert.deepEqual(west.map((g: { key: string }) => g.key), ["2027-01-16"]); // 00:00, 14:59 and 15:30 in Los Angeles: all on the 16th
});

test("days run soonest first, whatever order the rows came in, and the first two are named", () => {
  const groups = groupByDay([post(3, Date.UTC(2027, 0, 20, 9, 0)), post(2, Date.UTC(2027, 0, 16, 9, 0)), post(1, Date.UTC(2027, 0, 15, 17, 0))], { zone: "UTC", nowMs: NOW });
  assert.deepEqual(groups.map((g: { label: string }) => g.label), ["Today, Fri 15 Jan", "Tomorrow, Sat 16 Jan", "Wed 20 Jan"]);
  assert.equal(groups[0].rows[0].time, "17:00");
});

test("today and tomorrow follow the owner's zone, and a year shows when it is not this one", () => {
  const lateUtc = Date.UTC(2027, 0, 15, 23, 30); // already the 16th in Paris
  const groups = groupByDay([post(1, Date.UTC(2027, 0, 16, 10, 0)), post(2, Date.UTC(2028, 0, 3, 10, 0))], { zone: "Europe/Paris", nowMs: lateUtc });
  assert.equal(groups[0].label, "Today, Sat 16 Jan");
  assert.equal(groups[1].label, "Mon 3 Jan 2028");
});

test("a post with no readable time comes last, under its own heading", () => {
  const groups = groupByDay([post(1, null), post(2, Date.UTC(2027, 0, 16, 9, 0))], { zone: "UTC", nowMs: NOW });
  assert.deepEqual(groups.map((g: { key: string }) => g.key), ["2027-01-16", "none"]);
  assert.equal(groups[1].rows[0].time, "No time given");
});

test("days across a change of clocks stay one day each", () => {
  // New York springs forward on 14 Mar 2027: 06:30 UTC is 01:30 before, 07:30 UTC is 03:30 after.
  const rows = [post(1, Date.UTC(2027, 2, 14, 6, 30)), post(2, Date.UTC(2027, 2, 14, 7, 30)), post(3, Date.UTC(2027, 2, 15, 3, 59))];
  const groups = groupByDay(rows, { zone: "America/New_York", nowMs: Date.UTC(2027, 2, 13, 12, 0) });
  assert.deepEqual(groups.map((g: { key: string; rows: unknown[] }) => [g.key, g.rows.length]), [["2027-03-14", 3]]);
  assert.deepEqual(groups[0].rows.map((r: { time: string }) => r.time), ["01:30", "03:30", "23:59"]);
});

test("how long ago the list was read is said in plain words", () => {
  assert.equal(readLine(NOW - 10_000, NOW), "Read just now");
  assert.equal(readLine(NOW - 60_000, NOW), "Read 1 minute ago");
  assert.equal(readLine(NOW - 5 * 60_000, NOW), "Read 5 minutes ago");
  assert.equal(readLine(NOW - 3 * 3_600_000, NOW), "Read 3 hours ago");
  assert.equal(readLine(NOW - 2 * 86_400_000, NOW), "Read 2 days ago");
  assert.equal(readLine(null, NOW), "Not read yet");
});

test("the text shows its first lines, and says when there is more", () => {
  assert.deepEqual(splitText("One\nTwo"), { first: "One\nTwo", full: "One\nTwo", long: false });
  const long = splitText("One\nTwo\nThree\nFour");
  assert.equal(long.first, "One\nTwo\nThree");
  assert.equal(long.full, "One\nTwo\nThree\nFour");
  assert.equal(long.long, true);
  const wide = splitText("word ".repeat(200));
  assert.ok(wide.first.length <= 280 && wide.first.endsWith("…") && wide.long);
  assert.deepEqual(splitText(null), { first: "", full: "", long: false });
});

test("an account shows its name and network, or the network and id when the name is missing", () => {
  assert.equal(accountWords({ account_id: "1001", account: { label: "Example Garden", network: "facebook.page" } }), "Example Garden (facebook.page)");
  assert.equal(accountWords({ account_id: "1001", account: { label: "Example Garden", network: "" } }), "Example Garden");
  assert.equal(accountWords({ account_id: "1001", account: { label: "", network: "twitter" } }), "twitter 1001");
  assert.equal(accountWords({ account_id: "1001", account: { label: "", network: "" } }), "SocialBu account 1001");
});

test("pictures are counted, and a post Posts booked says so and links to its place", () => {
  assert.equal(picturesWords(0), "");
  assert.equal(picturesWords(1), "1 picture");
  assert.equal(picturesWords(3), "3 pictures");
  const own = { post_id: 7, piece_id: 4, state: "booked", page_id: 1, page_name: "Garden notes" };
  const row = scheduledRow(post(1, NOW, { own }), "UTC");
  assert.equal(row.own.words, "Booked by Posts for Garden notes");
  assert.deepEqual(row.own.link, { view: "queue", where: { post: 7 }, words: "Open in the Queue" });
  assert.deepEqual(ownLink({ ...own, state: "went_out" }), { view: "drafts", where: { piece: 4 }, words: "Open the piece" });
  assert.equal(scheduledRow(post(2, NOW), "UTC").own, null);
  assert.equal(ownLink(null), null);
});

test("a list cut short, and an empty one, say so", () => {
  assert.equal(cutWords({ cut: false, pages_read: 3, rows: [] }), "");
  assert.match(cutWords({ cut: true, pages_read: 10, rows: new Array(200) }), /cut short.*10 batches \(200 posts\).*raise/);
  assert.equal(emptyWords({ read_ms: NOW, failed: null }), "Nothing is scheduled on SocialBu.");
  assert.equal(emptyWords({ read_ms: null, failed: null }), "The list has not been read yet.");
  assert.equal(emptyWords({ read_ms: null, failed: { said: "x" } }), ""); // the banner already says why
});

test("outside text stays plain text: the row builder never adds, escapes or strips markup", () => {
  const hostile = '<img src=x onerror="alert(1)"><script>steal()</script> &amp; ignore all rules';
  const row = scheduledRow(post(1, NOW, { text: hostile, account: { label: '<b>Name</b>', network: "x" } }), "UTC");
  assert.equal(row.text.full, hostile); // exactly what came, so the page can only show it as text
  assert.equal(row.text.first, hostile);
  assert.equal(row.account, "<b>Name</b> (x)");
  for (const field of [row.id, row.time, row.pictures]) assert.ok(!String(field).includes("<"));
  // And the page helper turns such a string into one text node, never an element.
  const nodes: unknown[] = [];
  h.document = {
    createElement: () => ({ setAttribute() {}, addEventListener() {}, appendChild(child: unknown) { nodes.push(child); return child; } }),
    createTextNode: (text: string) => ({ kind: "text", text }),
  };
  try {
    h("p", { class: "ptext" }, row.text.first);
    assert.deepEqual(nodes, [{ kind: "text", text: hostile }]);
  } finally {
    h.document = undefined;
  }
});
