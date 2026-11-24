import assert from "node:assert/strict";
import { test } from "node:test";
import { changedLine, endedLine, refusalField, validateChange } from "../src/modules/access/lib/password.ts";

const CURRENT = "correct horse battery";
const NEXT = "nine quiet copper kettles";

test("a form that can be sent has no errors", () => {
  assert.deepEqual(validateChange({ current: CURRENT, next: NEXT, confirm: NEXT }), {});
});

test("each thing wrong with the form is said under its own field", () => {
  assert.deepEqual(validateChange({ current: "", next: "short", confirm: "other" }), {
    current: "Enter your current password.",
    next: "Use at least 10 characters.",
    confirm: "The passwords do not match.",
  });
});

test("a new password the same as the current one is caught before it is sent", () => {
  const errors = validateChange({ current: CURRENT, next: CURRENT, confirm: CURRENT });
  assert.deepEqual(Object.keys(errors), ["next"]);
});

test("the server's refusal goes under the field it is about", () => {
  assert.equal(refusalField(403), "current");
  assert.equal(refusalField(422), "next");
  assert.equal(refusalField(429), null);
  assert.equal(refusalField(0), null);
});

test("what happened is said with the right count and that this browser stays signed in", () => {
  assert.equal(changedLine(0), "Your password was changed. No other browser was signed in. This one stays signed in.");
  assert.equal(changedLine(1), "Your password was changed. 1 other browser was signed out. This one stays signed in.");
  assert.equal(endedLine(3), "3 other browsers were signed out. This one stays signed in.");
  assert.equal(endedLine(0), "No other browser was signed in. Nothing needed to change.");
});

test("no line about what happened ever contains a password", () => {
  for (const line of [changedLine(2), endedLine(2), ...Object.values(validateChange({ current: CURRENT, next: "short", confirm: NEXT }))]) {
    assert.ok(!line.includes(CURRENT) && !line.includes(NEXT));
  }
});
