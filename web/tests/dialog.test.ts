// Questions the owner is asked, in the shell and in an app's page: the keyboard never
// starts on the button that deletes.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page helper is plain JavaScript, supplied to every app's page as _ui.js.
import { ask, closeDialog, dialog, startsOn, why } from "../../sdk/js/ui.js";
import { confirmStartsOn } from "../src/lib/dialog.ts";

test("a destructive confirm dialog starts on the way out, and a plain one on its answer", () => {
  assert.equal(confirmStartsOn(true), "cancel");
  assert.equal(confirmStartsOn(false), "confirm");
});

test("the shell's confirm dialog takes its first focus from that rule and nowhere else", () => {
  const text = readFileSync(new URL("../src/components/ConfirmDialog.tsx", import.meta.url), "utf8");
  assert.equal(text.match(/data-autofocus/g)?.length, 2);
  assert.ok(text.includes('data-autofocus={startsOn === "cancel" ? true : undefined}'));
  assert.ok(text.includes('data-autofocus={startsOn === "confirm" ? true : undefined}'));
});

type Fake = {
  tag: string;
  className: string;
  disabled: boolean;
  children: Fake[];
  text: string;
  classList: { contains: (name: string) => boolean };
  setAttribute: (name: string, value: string) => void;
  appendChild: (child: Fake) => Fake;
  addEventListener: () => void;
  querySelector: (selector: string) => Fake | null;
  focus: () => void;
  remove: () => void;
};

/** A document just big enough to open a dialog in and see where the keyboard went. */
function fakeDocument() {
  const doc = {
    activeElement: null as Fake | null,
    createElement(tag: string): Fake {
      const el: Fake = {
        tag, className: "", disabled: false, children: [], text: "",
        classList: { contains: (name) => el.className.split(" ").includes(name) },
        setAttribute() {},
        appendChild(child) { el.children.push(child); return child; },
        addEventListener() {},
        querySelector(selector) {
          const tags = selector.split(",").map((part) => part.trim());
          for (const child of el.children) {
            if (tags.includes(child.tag)) return child;
            const deeper = child.querySelector?.(selector);
            if (deeper) return deeper;
          }
          return null;
        },
        focus() { doc.activeElement = el; },
        remove() {},
      };
      return el;
    },
    createTextNode(text: string) { return { tag: "#text", text, children: [] } as unknown as Fake; },
    getElementById() { return null; },
    body: null as unknown as Fake,
  };
  doc.body = doc.createElement("body");
  return doc;
}

const words = (el: Fake | null) => el?.children.map((child) => child.text).join("") ?? "";

function withDocument<T>(run: (doc: ReturnType<typeof fakeDocument>) => T): T {
  const doc = fakeDocument();
  const scope = globalThis as { document?: unknown };
  const before = scope.document;
  scope.document = doc;
  try {
    return run(doc);
  } finally {
    closeDialog();
    scope.document = before;
  }
}

test("an app page's danger question opens with the keyboard on Cancel, never on the danger button", () => {
  withDocument((doc) => {
    void ask("Drop this piece?", "It will be gone.", "Drop it", "danger");
    assert.equal(doc.activeElement?.tag, "button");
    assert.equal(words(doc.activeElement), "Cancel");
    assert.equal(doc.activeElement?.classList.contains("danger"), false);
  });
});

test("a question with no primary answer starts on the way out, and one with a primary answer on that", () => {
  withDocument((doc) => {
    void dialog({ title: "Remove topic?", actions: [{ label: "Remove" }], cancel: "Keep it" });
    assert.equal(words(doc.activeElement), "Keep it");
  });
  withDocument((doc) => {
    void ask("Book it?", "", "Book");
    assert.equal(words(doc.activeElement), "Book");
  });
  withDocument((doc) => {
    void dialog({ title: "Two ways", actions: [{ label: "Delete", kind: "danger" }, { label: "Save", kind: "primary" }] });
    assert.equal(words(doc.activeElement), "Save");
  });
});

test("a dialog with a field to fill in starts in the field", () => {
  const field = { tag: "input" };
  const danger = { classList: { contains: (name: string) => name === "danger" } };
  const wayOut = { tag: "button" };
  assert.equal(startsOn(field, [danger], wayOut), field);
  assert.equal(startsOn(null, [danger], wayOut), wayOut);
  assert.equal(startsOn(null, [], wayOut), wayOut);
});

test("the page shows the owner's sentence for a refusal when JimmyHQ sent one", () => {
  assert.equal(why({ message: "Call hq.files.upload from the owner's own pick.", owner_message: "Choose it again." }), "Choose it again.");
  assert.equal(why({ message: "The developer sentence." }), "The developer sentence.");
  assert.equal(why({ message: "Kept.", owner_message: 7 }), "Kept.");
  assert.equal(why({ message: "Kept.", owner_message: "" }), "Kept.");
  assert.ok(why(null).length > 0);
});
