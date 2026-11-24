// The Articles page: what each list holds, the filters, and what Today says needs the owner.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { checksLine, filterPieces, finished, groupOf, headline, inTheWorks, siteName, toDecide, toPublish } from "../../apps/articles/web/lib/lists.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { NOTHING, count, needs, underWay } from "../../apps/articles/web/lib/today.js";

const piece = (id: number, state: string, more: Record<string, unknown> = {}) => ({
  id, state, site_id: 1, topic: `Topic ${id}`, title: "", angle: "", words: state, questions: 0, warnings: 0,
  created: id, updated: 100 - id, error: "", close: null, attempt: null, ...more,
});
const sites = [{ id: 1, name: "Garden notes", sends_out: false }, { id: 2, name: "Plot news", sends_out: true }];

test("each article is in one group, and each list keeps its own order", () => {
  const all = [piece(1, "ready", { updated: 50 }), piece(2, "ready", { updated: 10 }), piece(3, "drafting"), piece(4, "unsure"),
    piece(5, "approved"), piece(6, "published"), piece(7, "dropped"), piece(8, "held_coverage")];
  assert.deepEqual(toDecide(all).map((p: { id: number }) => p.id), [2, 1]);
  assert.deepEqual(inTheWorks(all).map((p: { id: number }) => p.id), [3, 8]);
  assert.deepEqual(toPublish(all).map((p: { id: number }) => p.id), [4, 5]);
  assert.deepEqual(finished(all).map((p: { id: number }) => p.id), [6, 7]);
  assert.deepEqual(all.map(groupOf), ["decide", "decide", "working", "publish", "publish", "done", "done", "working"]);
});

test("the search finds every word in any order, within a site and a group", () => {
  const all = [piece(1, "ready", { title: "Compost on clay soil" }), piece(2, "drafting", { topic: "Clay pots", site_id: 2 }), piece(3, "ready", { topic: "Seeds" })];
  assert.deepEqual(filterPieces(all, { query: "CLAY" }).map((p: { id: number }) => p.id), [1, 2]);
  assert.deepEqual(filterPieces(all, { query: "soil compost" }).map((p: { id: number }) => p.id), [1]);
  assert.deepEqual(filterPieces(all, { site: "2" }).map((p: { id: number }) => p.id), [2]);
  assert.deepEqual(filterPieces(all, { group: "decide" }).map((p: { id: number }) => p.id), [1, 3]);
  assert.deepEqual(filterPieces(all, {}).length, 3);
});

test("a line names a title, else the topic, cut to fit, and a site that has gone in plain words", () => {
  assert.equal(headline({ title: "A title", topic: "x" }), "A title");
  assert.equal(headline({ title: "", topic: "a  topic" }), "a topic");
  assert.equal(headline({ title: "x".repeat(200) }).length, 110);
  assert.equal(siteName(sites, 9), "A site you removed");
  assert.equal(checksLine({ questions: 2, warnings: 1 }), "2 questions open · 1 warning");
  assert.equal(checksLine({ questions: 0, warnings: 0 }), "Every check passed");
});

test("Today lists what needs the owner, in the order it is best dealt with", () => {
  const data = { sites, ideas: [{ id: 1 }], replies: { unread: 3 }, pieces: [
    piece(1, "ready", { questions: 2 }), piece(2, "unsure"), piece(3, "held_coverage", { close: { said: "86% like an earlier one." } }),
    piece(4, "stopped_research", { error: "Only 2 sources." }), piece(5, "stalled"), piece(6, "approved", { site_id: 2 }), piece(7, "sent"),
    piece(8, "drafting"), piece(9, "published")] };
  const groups = needs(data);
  assert.deepEqual(groups.map((g: { key: string }) => g.key), ["decide", "unsure", "close", "sources", "stuck", "approved", "sent", "ideas"]);
  assert.equal(groups[0].title, "1 article to decide, 2 questions open");
  assert.equal(groups[0].items[0].detail, "2 questions open");
  assert.match(groups[2].items[0].detail, /86% like an earlier one/);
  assert.equal(groups[3].items[0].detail, "Only 2 sources.");
  assert.equal(groups[1].items[0].go.view, "publish");
  assert.equal(groups[5].items[0].detail, "Press Publish to send it.");
  assert.deepEqual(groups[7].items.map((i: { text: string }) => i.text), ["1 idea from readers waiting", "3 replies not looked at yet"]);
  assert.equal(count(data), 9);
  assert.equal(underWay(data), "1 article being worked on.");
});

test("an empty Today says so plainly, and a first run asks for a site", () => {
  assert.deepEqual(needs({ sites, pieces: [piece(1, "published")], ideas: [], replies: { unread: 0 } }), []);
  assert.equal(NOTHING, "Nothing needs you right now.");
  const first = needs({ sites: [], pieces: [], ideas: [], replies: { unread: 0 } });
  assert.deepEqual(first.map((g: { key: string }) => g.key), ["sites"]);
  assert.equal(count({ sites: [], pieces: [] }), 0);
});
