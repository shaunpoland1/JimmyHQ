// The Posts page: a refused picture upload, in the owner's words.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { pickRanOut, uploadProblem } from "../../apps/posts/web/lib/uploads.js";

const refusal = (code: string, message: string) => Object.assign(new Error(message), { code });
const LATE = refusal("not_allowed", "The owner has not touched your page just now, so no picture was taken. Call hq.files.upload from the owner's own pick of a file.");

test("a pick that ran out of time tells the owner to choose the pictures again, never the developer's sentence", () => {
  const said = uploadProblem(LATE, 3);
  assert.equal(said, "That took too long, so the last 3 pictures were not added. Choose them again.");
  assert.ok(!/hq\.files|The owner|Call /.test(said));
});

test("one picture left out reads as one", () => {
  assert.equal(uploadProblem(LATE, 1), "That took too long, so the last picture was not added. Choose it again.");
});

test("the rest of a pick that ran out is not tried, since each would be refused the same way", () => {
  assert.equal(pickRanOut(LATE), true);
  assert.equal(pickRanOut(refusal("too_large", "That picture is too large.")), false);
  assert.equal(pickRanOut(null), false);
});

test("any other refusal keeps the sentence it came with", () => {
  assert.equal(uploadProblem(refusal("too_large", "That picture is over 25 MB, so it was not kept."), 2), "That picture is over 25 MB, so it was not kept.");
  assert.match(uploadProblem(null), /could not be added/);
});
