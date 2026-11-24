// Posts' Today and New about Reader: a hand-over that fails is said loudly, first, with
// what to do and when Posts last heard from Reader; ideas wait for Start.

import assert from "node:assert/strict";
import { test } from "node:test";

// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { heardLine, ideasWaiting, readerTrouble, waitsForBrandLine } from "../../apps/posts/web/lib/reader.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { needs } from "../../apps/posts/web/lib/today.js";

const HOUR = 3_600_000;
const NOW = 1_800_000_000_000;
const PAGES = [{ id: 1, name: "Garden notes" }];

test("when Posts last heard from Reader is said in plain words", () => {
  assert.equal(heardLine(null, NOW), "Posts has not heard from Reader yet.");
  assert.equal(heardLine(NOW - 10, NOW), "Posts last heard from Reader less than an hour ago.");
  assert.equal(heardLine(NOW - 3 * HOUR, NOW), "Posts last heard from Reader 3 hours ago.");
  assert.equal(heardLine(NOW - 49 * HOUR, NOW), "Posts last heard from Reader 2 days ago.");
});

test("a feed that cannot be read is the first thing Today says, with what to do and when Posts last heard", () => {
  const data = { now_ms: NOW, pages: PAGES, posts: [], pieces: [], ideas: [],
    reader: { loud: true, said: "The app \"Reader\" was removed.", todo: "Open the Apps screen.", heard_at: NOW - 2 * HOUR } };
  const [first] = needs(data);
  assert.equal(first.key, "reader");
  assert.equal(first.items[0].text, "The app \"Reader\" was removed.");
  assert.equal(first.items[0].detail, "Open the Apps screen. Posts last heard from Reader 2 hours ago.");
});

test("Reader working, absent or quietly never installed puts nothing loud on Today", () => {
  for (const reader of [undefined, { loud: false }, { loud: false, quiet: "Install Reader to have the articles it finds arrive here as ideas." }]) {
    assert.equal(readerTrouble({ reader }), null);
    assert.deepEqual(needs({ pages: PAGES, posts: [], pieces: [], reader }), []);
  }
});

test("ideas from Reader wait on Today for Start or Dismiss", () => {
  const data = { pages: PAGES, posts: [], pieces: [], ideas: [{ id: 1, title: "Solar roofs", url: "https://news.example.com/a", topic: "Solar" }] };
  const group = ideasWaiting(data);
  assert.equal(group.title, "1 idea from Reader");
  assert.deepEqual(group.items[0], { text: "Solar roofs", detail: "Topic: Solar", go: { view: "new" } });
  assert.equal(needs(data).at(-1).key, "ideas");
});

test("an idea that waits for a brand says why, only when Posts found there are two or more", () => {
  assert.equal(waitsForBrandLine({ reader: { waits_for_brand: true } }), "You have more than one brand, so choose which one this is for.");
  assert.equal(waitsForBrandLine({ reader: { waits_for_brand: false } }), "");
  assert.equal(waitsForBrandLine({}), "");
});
