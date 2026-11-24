// The Reader page: suggested phrasings merged into the phrasings box.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { mergePhrasings } from "../../apps/reader/web/lib/phrasings.js";

test("suggestions go below what the owner wrote, which is never changed", () => {
  const got = mergePhrasings("local ai\r\n  my own line ", ["llm on a laptop", "run models locally"]);
  assert.equal(got.text, "local ai\nmy own line\nllm on a laptop\nrun models locally");
  assert.equal(got.added, 2);
});

test("a suggestion already in the box is skipped, ignoring case and spacing", () => {
  const got = mergePhrasings("Local  AI", ["local ai", "LOCAL AI", "local llm", "Local LLM"]);
  assert.equal(got.text, "Local  AI\nlocal llm");
  assert.equal(got.added, 1);
});

test("an empty box takes the suggestions and nothing odd breaks it", () => {
  assert.equal(mergePhrasings("", ["a b", "", null, 7]).text, "a b\n7");
  assert.deepEqual(mergePhrasings("keep", undefined), { text: "keep", added: 0 });
});

test("a box never grows past the most a topic holds", () => {
  const got = mergePhrasings("one\ntwo", ["a", "b", "c", "d"], 4);
  assert.equal(got.text, "one\ntwo\na\nb");
  assert.equal(got.added, 2);
});
