// The Posts page: the keys of the Drafts view.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { KEY_HINT, keyAction, keyHint, moveIndex } from "../../apps/posts/web/lib/keys.js";

const on = { view: "drafts", dialogOpen: false };
const press = (key: string, more: Record<string, unknown> = {}) => ({ key, target: { tagName: "BODY" }, ...more });

test("each letter on the Drafts view means its decision", () => {
  const got = Object.fromEntries(["j", "k", "a", "e", "s", "d", "Enter", "Escape"].map((k) => [k, keyAction(press(k), on)]));
  assert.deepEqual(got, { j: "next", k: "previous", a: "approve", e: "edit", s: "send_back", d: "decline", Enter: "open", Escape: "close" });
});

test("typing in a field never approves or declines anything", () => {
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    assert.equal(keyAction(press("a", { target: { tagName } }), on), null);
    assert.equal(keyAction(press("d", { target: { tagName } }), on), null);
  }
  assert.equal(keyAction(press("a", { target: { tagName: "DIV", isContentEditable: true } }), on), null);
});

test("while a dialog is open only Esc is the page's", () => {
  const inDialog = { view: "drafts", dialogOpen: true };
  assert.equal(keyAction(press("a"), inDialog), null);
  assert.equal(keyAction(press("Enter"), inDialog), null);
  assert.equal(keyAction(press("Escape"), inDialog), "close");
});

test("letters mean nothing on the other views, and shortcuts with modifiers are the browser's", () => {
  assert.equal(keyAction(press("a"), { view: "queue", dialogOpen: false }), null);
  assert.equal(keyAction(press("a", { metaKey: true }), on), null);
  assert.equal(keyAction(press("s", { ctrlKey: true }), on), null);
  assert.equal(keyAction(press("A", { shiftKey: true }), on), null);
});

test("Enter on a focused button presses that button rather than opening a draft", () => {
  assert.equal(keyAction(press("Enter", { target: { tagName: "BUTTON" } }), on), null);
});

test("Enter on the checks-passed summary, a link or anything else focused is left to the browser", () => {
  for (const tagName of ["SUMMARY", "A", "DETAILS", "DIV", "LI"]) {
    assert.equal(keyAction(press("Enter", { target: { tagName } }), on), null, tagName);
  }
  assert.equal(keyAction(press("Enter"), on), "open");
});

test("with single-letter keys switched off no letter does anything, and Enter and Esc still work", () => {
  const off = { view: "drafts", dialogOpen: false, letters: false };
  for (const key of ["j", "k", "a", "e", "s", "d"]) assert.equal(keyAction(press(key), off), null, key);
  assert.equal(keyAction(press("Enter"), off), "open");
  assert.equal(keyAction(press("Escape"), off), "close");
});

test("j is next, k is previous, a approves and s sends back", () => {
  assert.deepEqual(["j", "k", "a", "s"].map((k) => keyAction(press(k), on)), ["next", "previous", "approve", "send_back"]);
});

test("the hint under the keys says where to switch the single letters off, and that a asks first", () => {
  const shown = keyHint(true);
  assert.equal(shown.keys, KEY_HINT);
  assert.match(shown.note, /asks before it approves/);
  assert.match(shown.note, /Settings, then Apps, then Posts/);
  assert.match(shown.note, /Single-letter keys/);
});

test("with the letters off the hint names no letter and says how to switch them on", () => {
  const shown = keyHint(false);
  assert.ok(!/\b[jkaesd]\b/.test(shown.keys), shown.keys);
  assert.match(shown.note, /are off/);
  assert.match(shown.note, /Single-letter keys/);
});

test("moving through the list stops at both ends", () => {
  assert.equal(moveIndex(-1, 3, 1), 0);
  assert.equal(moveIndex(-1, 3, -1), 2);
  assert.equal(moveIndex(2, 3, 1), 2);
  assert.equal(moveIndex(0, 3, -1), 0);
  assert.equal(moveIndex(0, 0, 1), -1);
});

test("the key hint names every key, on one line", () => {
  for (const key of ["j", "k", "Enter", "a", "e", "s", "d", "Esc"]) assert.ok(KEY_HINT.includes(key), key);
  assert.ok(!KEY_HINT.includes("\n"));
});
