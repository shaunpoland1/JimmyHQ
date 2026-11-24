import assert from "node:assert/strict";
import { test } from "node:test";
import type { Action } from "../src/modules/bench/api.ts";
import { KEYS_OFF_HINT, KEY_HELP, KEY_RULES, actionFor, benchCommand, isTyping, singleKeysOn } from "../src/modules/bench/lib/keys.ts";
import { linkify, safeHref, splitParagraphs } from "../src/modules/bench/lib/text.ts";

test("paragraphs split on blank lines and keep their line breaks", () => {
  assert.deepEqual(splitParagraphs("One\ntwo\n\n\nThree\r\n\r\nFour  "), ["One\ntwo", "Three", "Four"]);
  assert.deepEqual(splitParagraphs("  \n\n "), []);
  assert.deepEqual(splitParagraphs(""), []);
});

test("only http and https addresses become links", () => {
  assert.deepEqual(linkify("See https://example.com/a?b=1 now"), [
    { type: "text", value: "See " },
    { type: "link", value: "https://example.com/a?b=1", href: "https://example.com/a?b=1" },
    { type: "text", value: " now" },
  ]);
  assert.deepEqual(linkify("javascript:alert(1) data:text/html,x ftp://example.com"), [
    { type: "text", value: "javascript:alert(1) data:text/html,x ftp://example.com" },
  ]);
  assert.equal(safeHref("javascript:alert(1)"), null);
  assert.equal(safeHref("https://user:secret@example.com/"), null);
  assert.equal(safeHref("http://"), null);
  assert.equal(safeHref("http://example.com"), "http://example.com/");
});

test("markup in card text stays text", () => {
  const parts = linkify('<img src=x onerror=alert(1)> <a href="https://example.com">x</a>');
  assert.deepEqual(parts.filter((part) => part.type === "link").map((part) => part.value), ["https://example.com"]);
  assert.equal(parts.map((part) => part.value).join(""), '<img src=x onerror=alert(1)> <a href="https://example.com">x</a>');
});

test("punctuation after an address is not part of the link", () => {
  const links = (text: string) => linkify(text).filter((part) => part.type === "link").map((part) => part.value);
  assert.deepEqual(links("Read https://example.com/page."), ["https://example.com/page"]);
  assert.deepEqual(links("(see https://example.com/page)"), ["https://example.com/page"]);
  assert.deepEqual(links("https://example.com/a_(b), then"), ["https://example.com/a_(b)"]);
  assert.deepEqual(links("xhttps://example.com"), []);
  assert.deepEqual(links("HTTPS://EXAMPLE.COM/x and http://example.org"), ["HTTPS://EXAMPLE.COM/x", "http://example.org"]);
});

test("text with no address comes back whole", () => {
  assert.deepEqual(linkify("plain words"), [{ type: "text", value: "plain words" }]);
  assert.deepEqual(linkify(""), []);
});

test("keys map to Bench commands", () => {
  assert.equal(benchCommand({ key: "j" }), "next");
  assert.equal(benchCommand({ key: "K" }), "previous");
  assert.equal(benchCommand({ key: "a" }), "primary");
  assert.equal(benchCommand({ key: "n" }), "notYet");
  assert.equal(benchCommand({ key: "d" }), "danger");
  assert.equal(benchCommand({ key: "s" }), "snooze");
  assert.equal(benchCommand({ key: "?" }), "help");
  assert.equal(benchCommand({ key: "Enter", targetTag: "DIV" }), "open");
  assert.equal(benchCommand({ key: "x" }), null);
});

test("a key never acts while typing in a field or with a modifier held", () => {
  for (const targetTag of ["INPUT", "TEXTAREA", "SELECT", "input"]) {
    assert.equal(benchCommand({ key: "a", targetTag }), null);
    assert.equal(isTyping({ targetTag }), true);
  }
  assert.equal(benchCommand({ key: "a", targetTag: "DIV", editable: true }), null);
  assert.equal(benchCommand({ key: "a", metaKey: true }), null);
  assert.equal(benchCommand({ key: "a", ctrlKey: true }), null);
  assert.equal(benchCommand({ key: "a", altKey: true }), null);
  assert.equal(benchCommand({ key: "a", targetTag: "BUTTON" }), "primary");
});

test("Enter on a button or a tab presses it and does not also open the card", () => {
  assert.equal(benchCommand({ key: "Enter", targetTag: "BUTTON" }), null);
  assert.equal(benchCommand({ key: "Enter", targetTag: "A" }), null);
  assert.equal(benchCommand({ key: "Enter", targetTag: "DIV", targetRole: "menuitem" }), null);
});

const action = (id: string, over: Partial<Action> = {}): Action => ({
  id,
  label: id,
  style: "default",
  needs_reason: false,
  closes: false,
  moves_to: null,
  ...over,
});

test("shortcuts pick the card's own actions by style", () => {
  const actions = [
    action("approve", { style: "primary", closes: true }),
    action("not_yet", { moves_to: "waiting" }),
    action("no", { style: "danger", closes: true, needs_reason: true }),
  ];
  assert.equal(actionFor(actions, "primary")?.id, "approve");
  assert.equal(actionFor(actions, "notYet")?.id, "not_yet");
  assert.equal(actionFor(actions, "danger")?.id, "no");
  assert.equal(actionFor(actions, "snooze"), undefined);
});

test("a card without a not-yet or a danger action has nothing for those keys", () => {
  const done = [action("ok", { style: "primary", closes: true }), action("undo", { closes: true })];
  assert.equal(actionFor(done, "notYet"), undefined);
  assert.equal(actionFor(done, "danger"), undefined);
  const custom = [action("send", { style: "primary", closes: true }), action("park", { moves_to: "waiting" })];
  assert.equal(actionFor(custom, "notYet")?.id, "park");
});

test("a held key keeps moving through the list but never decides, snoozes or opens again", () => {
  assert.equal(benchCommand({ key: "j", repeat: true }), "next");
  assert.equal(benchCommand({ key: "k", repeat: true }), "previous");
  for (const key of ["a", "n", "d", "s", "?", "Enter"]) {
    assert.equal(benchCommand({ key, repeat: true }), null, key);
    assert.notEqual(benchCommand({ key, repeat: false }), null, key);
  }
});

test("a key pressed while focus is outside the Bench is not the Bench's", () => {
  // A ticked checkbox in the dock, a dock tab, a sidebar link: none of them may decide or move a card.
  assert.equal(benchCommand({ key: "a", targetTag: "BUTTON", targetRole: "checkbox", outside: true }), null);
  assert.equal(benchCommand({ key: "n", targetTag: "BUTTON", targetRole: "tab", outside: true }), null);
  assert.equal(benchCommand({ key: "d", targetTag: "A", outside: true }), null);
  assert.equal(benchCommand({ key: "j", targetTag: "A", outside: true }), null);
  assert.equal(benchCommand({ key: "?", targetTag: "DIV", targetRole: "separator", outside: true }), null);
  // The same keys still act on a button inside the Bench, or with focus on the page itself.
  assert.equal(benchCommand({ key: "a", targetTag: "BUTTON", outside: false }), "primary");
  assert.equal(benchCommand({ key: "a" }), "primary");
});

test("with focus on the page itself a letter never decides or snoozes the open card", () => {
  // After a click on plain text the focus is on nothing: a stray letter must not approve.
  for (const key of ["a", "n", "d", "s", "A", "D"]) {
    assert.equal(benchCommand({ key, targetTag: "BODY", nowhere: true }), null, key);
  }
  // The same letters act once focus is in the Bench.
  assert.equal(benchCommand({ key: "a", targetTag: "DIV", nowhere: false }), "primary");
  assert.equal(benchCommand({ key: "d", targetTag: "BUTTON" }), "danger");
});

test("with focus on the page itself the keys still move, open and show the help", () => {
  assert.equal(benchCommand({ key: "j", targetTag: "BODY", nowhere: true }), "next");
  assert.equal(benchCommand({ key: "k", targetTag: "BODY", nowhere: true }), "previous");
  assert.equal(benchCommand({ key: "Enter", targetTag: "BODY", nowhere: true }), "open");
  assert.equal(benchCommand({ key: "?", targetTag: "BODY", nowhere: true }), "help");
});

test("with the single-letter keys switched off no letter or ? does anything, and Enter still opens", () => {
  for (const key of ["j", "k", "a", "n", "d", "s", "?", "J", "A"]) {
    assert.equal(benchCommand({ key, targetTag: "DIV", lettersOff: true }), null, key);
    assert.equal(benchCommand({ key, repeat: true, lettersOff: true }), null, key);
  }
  assert.equal(benchCommand({ key: "Enter", targetTag: "DIV", lettersOff: true }), "open");
  assert.equal(benchCommand({ key: "Enter", targetTag: "BUTTON", lettersOff: true }), null);
});

test("the single-letter keys are on unless the owner's setting is a plain false", () => {
  assert.equal(singleKeysOn(false), false);
  for (const value of [true, undefined, null, "false", 0]) assert.equal(singleKeysOn(value), true);
});

test("the help under the keys says what the keys do: which need focus in the Bench, and where to switch them off", () => {
  // Every key the help lists as acting on the card is one that focus on the page itself refuses.
  const refused = KEY_HELP.flatMap((row) => row.keys).filter((key) => benchCommand({ key }) !== null && benchCommand({ key, nowhere: true }) === null);
  assert.deepEqual(refused, ["a", "n", "d", "s"]);
  assert.ok(KEY_RULES.includes("a, n, d and s act on the open card only while focus is in the Bench"));
  assert.ok(KEY_RULES.includes("the sidebar, the top strip or the dock"));
  assert.ok(KEYS_OFF_HINT.includes("Settings"));
});
