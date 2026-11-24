// The Reader page: the keys of the Triage view.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { afterDecided, KEY_HINT, KEY_OF, KEYS_OFF_HINT, KEYS_ON_NOTE, keyAction, keyHint, moveIndex, VOTES } from "../../apps/reader/web/lib/keys.js";

const on = { view: "triage", dialogOpen: false };
const press = (key: string, more: Record<string, unknown> = {}) => ({ key, target: { tagName: "BODY" }, ...more });

test("each letter on the Triage view means its choice, and the buttons show the same letters", () => {
  const got = Object.fromEntries(["y", "l", "x", "o", "m", "j", "k", "Escape"].map((k) => [k, keyAction(press(k), on)]));
  assert.deepEqual(got, { y: "keep", l: "later", x: "skip", o: "send", m: "mine", j: "next", k: "previous", Escape: "close" });
  assert.deepEqual(KEY_OF, { keep: "y", later: "l", skip: "x", send: "o", mine: "m" });
  for (const letter of ["y", "l", "x", "o", "m", "j", "k"]) assert.ok(KEY_HINT.includes(`${letter} `));
});

test("the arrow, space and page keys are left to the browser, so they scroll", () => {
  for (const key of ["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", " ", "PageDown", "PageUp", "Home", "End"]) {
    assert.equal(keyAction(press(key), on), null);
    assert.equal(keyAction(press(key, { target: { tagName: "SUMMARY" } }), on), null);
  }
});

test("j and k move as they do in Posts, and no letter that decides in Posts changes anything here", () => {
  assert.equal(keyAction(press("j"), on), "next");
  assert.equal(keyAction(press("k"), on), "previous");
  // In Posts a approves, e edits, s sends back and d declines.
  for (const letter of ["a", "e", "s", "d"]) assert.equal(keyAction(press(letter), on), null);
  const changing = Object.values(KEY_OF);
  for (const letter of ["j", "k", "a", "e", "s", "d"]) assert.ok(!changing.includes(letter));
});

test("with the letter keys switched off only Esc is the page's, and the hint says where to switch them on", () => {
  const off = { ...on, lettersOff: true };
  for (const key of ["y", "l", "x", "o", "m", "j", "k"]) assert.equal(keyAction(press(key), off), null);
  assert.equal(keyAction(press("Escape"), off), "close");
  assert.deepEqual(keyHint(false), [KEYS_OFF_HINT]);
  assert.ok(KEYS_OFF_HINT.includes("Reader's settings"));
  assert.deepEqual(keyHint(true), [KEY_HINT, KEYS_ON_NOTE]);
  assert.ok(KEYS_ON_NOTE.includes("switch the letter keys off in Reader's settings"));
});

test("typing in a field never keeps or skips anything", () => {
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "input"]) {
    assert.equal(keyAction(press("y", { target: { tagName } }), on), null);
    assert.equal(keyAction(press("x", { target: { tagName } }), on), null);
  }
  assert.equal(keyAction(press("y", { target: { tagName: "DIV", isContentEditable: true } }), on), null);
  assert.equal(keyAction(press("Escape", { target: { tagName: "INPUT" } }), on), "close");
});

test("while a dialog is open only Esc is the page's", () => {
  const inDialog = { view: "triage", dialogOpen: true };
  assert.equal(keyAction(press("y"), inDialog), null);
  assert.equal(keyAction(press("Escape"), inDialog), "close");
});

test("letters mean nothing on the other views, and shortcuts with modifiers are the browser's", () => {
  for (const view of ["articles", "topics", "offered"]) assert.equal(keyAction(press("y"), { view, dialogOpen: false }), null);
  assert.equal(keyAction(press("y", { metaKey: true }), on), null);
  assert.equal(keyAction(press("x", { ctrlKey: true }), on), null);
  assert.equal(keyAction(press("o", { altKey: true }), on), null);
  assert.equal(keyAction(press("Y", { shiftKey: true }), on), null);
});

test("a key named like a property of every object means nothing", () => {
  for (const key of ["constructor", "toString", "__proto__", "hasOwnProperty"]) assert.equal(keyAction(press(key), on), null);
});

test("mine is not a vote, so it leaves the article where it is", () => {
  assert.deepEqual([...VOTES].sort(), ["keep", "later", "send", "skip"]);
  assert.equal(VOTES.has("mine"), false);
});

test("moving through the waiting list stops at both ends", () => {
  assert.equal(moveIndex(0, 3, 1), 1);
  assert.equal(moveIndex(2, 3, 1), 2);
  assert.equal(moveIndex(0, 3, -1), 0);
  assert.equal(moveIndex(-1, 3, -1), 2);
  assert.equal(moveIndex(0, 0, 1), -1);
});

test("after a choice the next article takes the place of the one decided", () => {
  assert.equal(afterDecided(0, 3), 0);
  assert.equal(afterDecided(2, 3), 1);
  assert.equal(afterDecided(0, 1), -1);
});
