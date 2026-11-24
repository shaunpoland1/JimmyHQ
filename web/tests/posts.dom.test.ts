// The Posts page: text from articles and models is shown as text, never as HTML.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page helper is plain JavaScript, supplied to every app's page as _ui.js.
import { h } from "../../sdk/js/ui.js";

type FakeNode = { kind: string; tag?: string; text?: string; attrs: Record<string, string>; children: FakeNode[]; listeners: string[]; className?: string };

/** A document just big enough to see what h() makes: elements, attributes and text nodes. */
function fakeDocument() {
  const element = (tag: string): FakeNode & Record<string, unknown> => {
    const node: FakeNode & Record<string, unknown> = {
      kind: "element", tag, attrs: {}, children: [], listeners: [],
      setAttribute(name: string, value: string) { node.attrs[name] = value; },
      appendChild(child: FakeNode) { node.children.push(child); return child; },
      addEventListener(type: string) { node.listeners.push(type); },
      set textContent(value: string) { node.children = [{ kind: "text", text: value, attrs: {}, children: [], listeners: [] }]; },
    };
    if (tag === "textarea" || tag === "input") node.value = "";
    return node;
  };
  return { createElement: element, createTextNode: (text: string) => ({ kind: "text", text, attrs: {}, children: [], listeners: [] }) };
}

const HOSTILE = '<img src=x onerror="alert(1)"><script>steal()</script>&amp;';

test("a hostile string from an article becomes one text node, never markup", () => {
  h.document = fakeDocument();
  try {
    const el = h("p", { class: "body" }, HOSTILE) as unknown as FakeNode;
    assert.equal(el.children.length, 1);
    assert.equal(el.children[0]?.kind, "text");
    assert.equal(el.children[0]?.text, HOSTILE);
    const titled = h("h2", { text: HOSTILE }) as unknown as FakeNode;
    assert.equal(titled.children[0]?.kind, "text");
  } finally {
    h.document = undefined;
  }
});

test("an attribute that could run script or load from outside is left out", () => {
  h.document = fakeDocument();
  try {
    const el = h("img", { onerror: "alert(1)", src: "https://tracker.example.com/x.gif", srcdoc: HOSTILE, style: "x", href: "javascript:x()", alt: HOSTILE }) as unknown as FakeNode;
    assert.deepEqual(Object.keys(el.attrs), ["alt"]);
    assert.equal(el.attrs.alt, HOSTILE); // an attribute value is never parsed as markup
    assert.deepEqual(el.listeners, []);
    const pic = h("img", { src: "blob:abc" }) as unknown as FakeNode;
    assert.equal(pic.attrs.src, "blob:abc");
    const button = h("button", { onclick: () => {} }) as unknown as FakeNode;
    assert.deepEqual(button.listeners, ["click"]);
  } finally {
    h.document = undefined;
  }
});

test("the page helper every app's page is given writes no HTML from a string, reaches no network and uses no browser dialog", () => {
  const text = readFileSync(new URL("../../sdk/js/ui.js", import.meta.url), "utf8");
  for (const banned of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "alert(", "confirm(", "prompt(", "fetch(", "XMLHttpRequest", "WebSocket", "http://", "eval(", "location."]) {
    assert.ok(!text.includes(banned), `ui.js uses ${banned}`);
  }
});

test("no file of the page writes HTML from a string, reaches the network, or uses the browser's dialogs", () => {
  const root = new URL("../../apps/posts/web/", import.meta.url);
  const files = readdirSync(root, { recursive: true }).map(String).filter((f) => f.endsWith(".js") || f.endsWith(".html"));
  assert.ok(files.length >= 10);
  for (const file of files) {
    const text = readFileSync(new URL(file, root), "utf8");
    for (const banned of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "alert(", "confirm(", "prompt(", "fetch(", "XMLHttpRequest", "WebSocket", "http://", "eval("]) {
      assert.ok(!text.includes(banned), `${file} uses ${banned}`);
    }
  }
});
