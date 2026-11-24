// The Apps screen's lines about feeds between apps: shown as the server words them, never broken by an odd value.

import assert from "node:assert/strict";
import { test } from "node:test";

import { FEED_LINES_MOST, feedLines, feedsHeading } from "../src/modules/apps/lib/feeds.ts";

test("the server's sentences about who reads what are shown as they are, in order", () => {
  const lines = ["Posts reads \"Articles\", which Reader offers: Articles you offered.", "Reader reads \"Posted\" from Posts."];
  assert.deepEqual(feedLines(lines), lines);
});

test("a missing, empty or odd value shows no line rather than breaking the page", () => {
  assert.deepEqual(feedLines(undefined), []);
  assert.deepEqual(feedLines("Posts reads it"), []);
  assert.deepEqual(feedLines([1, null, "", "  ", { a: 1 }, "Kept."]), ["Kept."]);
});

test("a long list is cut to a few lines, and the heading counts them", () => {
  const many = Array.from({ length: 50 }, (_, n) => `Line ${n}.`);
  assert.equal(feedLines(many).length, FEED_LINES_MOST);
  assert.equal(feedsHeading(["One."]), "Who reads what");
  assert.equal(feedsHeading(["One.", "Two."]), "Who reads what (2)");
});
