// The Reader page: what the status line says and offers after a choice on an article
// that is, or was, offered to other apps.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { afterChoice, afterTakenBack } from "../../apps/reader/web/lib/offers.js";

test("after Send on the status line offers Undo", () => {
  assert.deepEqual(afterChoice("send", ""), { words: "Sent on to other apps.", undo: "Undo" });
});

test("a choice that took the offer back says it is no longer offered, with nothing left to undo", () => {
  for (const action of ["keep", "later", "skip"]) {
    const said = afterChoice(action, "withdrawn");
    assert.ok(said.words.endsWith("It is no longer offered to other apps."));
    assert.equal(said.undo, "");
  }
});

test("a choice that leaves the offer says plainly that it stays, and offers to stop it", () => {
  assert.deepEqual(afterChoice("skip", "stays"), { words: "Skipped. It stays offered to other apps, as its topic says.", undo: "Stop offering it" });
});

test("a choice on an article never offered says only what was chosen", () => {
  assert.deepEqual(afterChoice("keep"), { words: "Kept.", undo: "" });
  assert.deepEqual(afterChoice("mine", ""), { words: "Choosing facts for Memory.", undo: "" });
  assert.deepEqual(afterChoice("something else"), { words: "Done.", undo: "" });
});

test("taking an offer back says whether the article waits in Triage again", () => {
  assert.ok(afterTakenBack({ back_in_triage: true }).includes("waiting in Triage again"));
  assert.ok(!afterTakenBack({ back_in_triage: false }).includes("Triage"));
  assert.ok(afterTakenBack(null).startsWith("Taken back."));
});
