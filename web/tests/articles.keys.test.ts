// The Articles page: j / k, Enter, a, e, s, d and Esc, and when they are left to the browser.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { KEY_HINT, KEY_HINT_NO_LETTERS, chooseWords, keyAction, keyHint, moveIndex } from "../../apps/articles/web/lib/keys.js";

const on = { view: "articles", dialogOpen: false };
const press = (key: string, more: Record<string, unknown> = {}) => ({ key, target: { tagName: "BODY" }, ...more });

test("the letters move, open and decide on the Articles view", () => {
  assert.deepEqual(["j", "k", "a", "e", "s", "d"].map((k) => keyAction(press(k), on)),
    ["next", "previous", "approve", "edit", "send_back", "decline"]);
  assert.equal(keyAction(press("Enter"), on), "open");
  assert.equal(keyAction(press("Escape"), on), "close");
});

test("typing in a field, a dialog, another view and modified keys are left alone, except Esc", () => {
  assert.equal(keyAction(press("a", { target: { tagName: "TEXTAREA" } }), on), null);
  assert.equal(keyAction(press("j", { target: { isContentEditable: true } }), on), null);
  assert.equal(keyAction(press("a"), { view: "articles", dialogOpen: true }), null);
  assert.equal(keyAction(press("Escape"), { view: "articles", dialogOpen: true }), "close");
  assert.equal(keyAction(press("a"), { view: "publish", dialogOpen: false }), null);
  assert.equal(keyAction(press("a", { metaKey: true }), on), null);
  assert.equal(keyAction(press("A", { shiftKey: true }), on), null);
  assert.equal(keyAction(press("Enter", { target: { tagName: "BUTTON" } }), on), null);
});

test("moving through the list holds at both ends", () => {
  assert.equal(moveIndex(-1, 3, 1), 0);
  assert.equal(moveIndex(-1, 3, -1), 2);
  assert.equal(moveIndex(2, 3, 1), 2);
  assert.equal(moveIndex(0, 3, -1), 0);
  assert.equal(moveIndex(0, 0, 1), -1);
});

test("the hint names every key in one line", () => {
  for (const words of ["j / k", "Enter", "a approve", "e edit", "s send back", "d decline", "Esc"]) assert.ok(KEY_HINT.includes(words));
  assert.ok(!KEY_HINT.includes("\n"));
});

test("with single-letter keys switched off no letter does anything, and Enter and Esc still work", () => {
  const off = { view: "articles", dialogOpen: false, letters: false };
  assert.deepEqual(["j", "k", "a", "e", "s", "d"].map((k) => keyAction(press(k), off)), [null, null, null, null, null, null]);
  assert.equal(keyAction(press("Enter"), off), "open");
  assert.equal(keyAction(press("Escape"), off), "close");
  assert.equal(keyAction(press("a"), { ...off, letters: true }), "approve");
  assert.equal(keyAction(press("a"), { view: "articles", dialogOpen: false }), "approve"); // on unless switched off
});

test("the letters mean what they mean on Posts: j next, k previous, a approve, s send back", () => {
  assert.equal(keyAction(press("j"), on), "next");
  assert.equal(keyAction(press("k"), on), "previous");
  assert.equal(keyAction(press("a"), on), "approve");
  assert.equal(keyAction(press("s"), on), "send_back");
});

test("under the key hint the page says where single-letter keys are switched off, and on again", () => {
  const shown = keyHint(true);
  assert.equal(shown.keys, KEY_HINT);
  assert.match(shown.note, /switch the single-letter keys off, open Settings, then Apps, then Articles/);
  assert.match(shown.note, /Single-letter keys/);
  const off = keyHint(false);
  assert.equal(off.keys, KEY_HINT_NO_LETTERS);
  for (const letter of ["j / k", "a approve", "s send back", "d decline", "e edit"]) assert.ok(!off.keys.includes(letter));
  assert.match(off.note, /are off\. To switch them on, open Settings, then Apps, then Articles/);
  assert.ok(chooseWords(true).includes("press j"));
  assert.ok(!chooseWords(false).includes("press"));
});
