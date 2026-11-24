// The Reader page: text from articles is shown as text, never as HTML, and the page
// reaches nothing but its own program.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { clip, lines } from "../../apps/reader/web/lib/text.js";
// @ts-expect-error The page helper is plain JavaScript, supplied to every app's page as _ui.js.
import { h } from "../../sdk/js/ui.js";

type FakeNode = { kind: string; tag?: string; text?: string; attrs: Record<string, string>; children: FakeNode[]; listeners: string[]; className?: string };

function fakeDocument() {
  const element = (tag: string): FakeNode & Record<string, unknown> => {
    const node: FakeNode & Record<string, unknown> = {
      kind: "element", tag, attrs: {}, children: [], listeners: [],
      setAttribute(name: string, value: string) { node.attrs[name] = value; },
      appendChild(child: FakeNode) { node.children.push(child); return child; },
      addEventListener(type: string) { node.listeners.push(type); },
      set textContent(value: string) { node.children = [{ kind: "text", text: value, attrs: {}, children: [], listeners: [] }]; },
    };
    return node;
  };
  return { createElement: element, createTextNode: (text: string) => ({ kind: "text", text, attrs: {}, children: [], listeners: [] }) };
}

const HOSTILE = '<img src=x onerror="alert(1)"><script>steal()</script>&amp;';

test("a hostile title, why-line or sentence becomes one text node, never markup", () => {
  h.document = fakeDocument();
  try {
    for (const tag of ["h2", "p", "li"]) {
      const el = h(tag, {}, HOSTILE) as unknown as FakeNode;
      assert.equal(el.children.length, 1);
      assert.equal(el.children[0]?.kind, "text");
      assert.equal(el.children[0]?.text, HOSTILE);
    }
  } finally {
    h.document = undefined;
  }
});

test("an attribute from an article that could run script or load from outside is left out", () => {
  h.document = fakeDocument();
  try {
    const el = h("p", { onclick: "steal()", href: "javascript:x()", src: "https://tracker.example.com/x.gif", title: HOSTILE }) as unknown as FakeNode;
    assert.deepEqual(Object.keys(el.attrs), ["title"]);
    assert.deepEqual(el.listeners, []);
  } finally {
    h.document = undefined;
  }
});

test("long text is cut on a whole character and says so", () => {
  assert.equal(clip("abcdef", 4), "abc…");
  assert.equal(clip("😀😀😀", 2), "😀…");
  assert.equal(clip(null, 3), "");
  assert.deepEqual(lines("one\r\n\n  two \rthree"), ["one", "two", "three"]);
});

test("no file of the page writes HTML from a string, reaches the network, or uses the browser's dialogs", () => {
  const root = new URL("../../apps/reader/web/", import.meta.url);
  const files = readdirSync(root, { recursive: true }).map(String).filter((f) => f.endsWith(".js") || f.endsWith(".html"));
  assert.ok(files.length >= 10);
  for (const file of files) {
    const text = readFileSync(new URL(file, root), "utf8");
    for (const banned of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "alert(", "confirm(", "prompt(", "fetch(", "XMLHttpRequest", "WebSocket", "http://", "eval(", "<form"]) {
      assert.ok(!text.includes(banned), `${file} uses ${banned}`);
    }
  }
});

test("the page is one page: nothing in it navigates the frame or loads it again", () => {
  const root = new URL("../../apps/reader/web/", import.meta.url);
  for (const file of readdirSync(root, { recursive: true }).map(String).filter((f) => f.endsWith(".js"))) {
    const text = readFileSync(new URL(file, root), "utf8");
    for (const banned of ["location.", "window.open", "<a ", "\"a\", {"]) assert.ok(!text.includes(banned), `${file} uses ${banned}`);
  }
});
