// The Posts page: what is drawn again when the program says something changed.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { afterChange } from "../../apps/posts/web/lib/redraw.js";

test("a view without fields is always drawn again, so it never shows stale data", () => {
  for (const view of ["today", "drafts", "queue", "went", "dropped"]) {
    assert.equal(afterChange({ view, typed: true, focused: true }), "all");
  }
});

test("New and Pages are drawn again when the owner has not typed in them", () => {
  assert.equal(afterChange({ view: "new" }), "all");
  assert.equal(afterChange({ view: "pages", typed: false, focused: false }), "all");
});

test("a form the owner is typing in is never wiped: only the counts are drawn", () => {
  assert.equal(afterChange({ view: "pages", typed: true }), "nav");
  assert.equal(afterChange({ view: "new", focused: true }), "nav");
});

test("New keeps what was typed, so once the owner leaves its fields it is drawn again", () => {
  assert.equal(afterChange({ view: "new", typed: true, focused: false }), "all");
});
