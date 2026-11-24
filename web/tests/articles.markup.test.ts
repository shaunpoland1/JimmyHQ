// The Articles page (apps/articles/web): the one safe renderer for an article's markup.
// Whatever a model or the owner wrote, only the allowed tags come out, no attribute but a
// link to a source by number, and every word as text.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { build, decode, parse, parseMark, plainText, splitMarks, tagsOf } from "../../apps/articles/web/lib/markup.js";
// @ts-expect-error The page helper is plain JavaScript, supplied to every app's page as _ui.js.
import { h } from "../../sdk/js/ui.js";

type Node = { tag?: string; text?: string; cite?: number[][]; source?: number; children?: Node[] };
const ALLOWED = new Set(["p", "h2", "h3", "ul", "ol", "li", "strong", "em", "blockquote", "a", "br"]);

const HOSTILE = [
  '<img src=x onerror="alert(1)">',
  "<script>steal(document.cookie)</script>",
  '<a href="javascript:alert(1)">click</a>',
  '<a href="https://evil.example.com/x">a made-up link</a>',
  '<p onclick="run()" style="color:red">styled</p>',
  "<iframe src=\"https://evil.example.com\"></iframe>",
  "<svg><script>alert(1)</script></svg>",
  "<style>body{display:none}</style>",
  '<p>"><img src=x onerror=alert(1)></p>',
  "<!-- <script>alert(1)</script> -->",
  "&lt;script&gt;alert(1)&lt;/script&gt;",
  "<scr<script>ipt>alert(1)</script>",
  '<a href="S2" onmouseover="x()">ok</a>',
  "<math><mi xlink:href=\"javascript:alert(1)\">x</mi></math>",
  "<form action=\"https://evil.example.com\"><input name=q></form>",
  "<p>unclosed <strong>bold <em>both",
  "</p></strong>stray closers",
  "<",
];

function walk(nodes: Node[], fn: (n: Node) => void) {
  for (const n of nodes) {
    fn(n);
    if (n.children) walk(n.children, fn);
  }
}

test("a hostile string never becomes a tag outside the allowed set, an attribute, or a link elsewhere", () => {
  for (const hostile of HOSTILE) {
    const { nodes } = parse(`<p>Before</p>${hostile}<p>After</p>`, new Set([1, 2]));
    for (const tag of tagsOf(nodes)) assert.ok(ALLOWED.has(tag as string), `${hostile} made <${tag}>`);
    walk(nodes, (n) => {
      assert.deepEqual(Object.keys(n).filter((k) => !["tag", "text", "cite", "source", "children"].includes(k)), []);
      if (n.tag === "a") assert.ok(n.source === 1 || n.source === 2, `${hostile} kept a link to ${n.source}`);
    });
    assert.ok(!plainText(nodes).includes("steal"), `${hostile} kept a script's words`);
  }
});

test("what was taken out is said, and the words of a removed tag are kept", () => {
  const { nodes, removed } = parse('<p>See <a href="https://evil.example.com">this page</a> and <span>here</span>.</p><p class="x">More.</p><script>x()</script>', new Set([1]));
  assert.equal(plainText(nodes), "See this page and here.\nMore.\n");
  assert.ok(removed.some((r: string) => r.startsWith("a link to https://evil.example.com")));
  assert.ok(removed.includes("<span> tags (their words were kept)"));
  assert.ok(removed.includes("a <script> and what was in it"));
  assert.ok(removed.includes('the "class" attribute'));
});

test("a link to a source the article has stays, and one to a source it lacks is unwrapped", () => {
  const { nodes } = parse('<p><a href="S2">the survey</a> and <a href="S9">another</a></p>', new Set([1, 2]));
  const p = nodes[0] as Node;
  assert.equal(p.children?.[0]?.tag, "a");
  assert.equal(p.children?.[0]?.source, 2);
  assert.ok(!p.children?.slice(1).some((n) => n.tag === "a"));
  assert.equal(plainText(nodes), "the survey and another\n");
});

test("entities are read as characters, never as markup", () => {
  assert.equal(decode("&lt;b&gt; &amp; &#39;x&#x27; &quot;"), "<b> & 'x' \"");
  assert.equal(decode("&#0; &#xD800; &bogus;"), "� � &bogus;");
  const { nodes } = parse("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>");
  assert.deepEqual(tagsOf(nodes), new Set(["p"]));
  assert.equal(plainText(nodes), "<script>alert(1)</script>\n");
});

test("a citation mark is read as the sentences it cites, as the program reads it", () => {
  assert.deepEqual(parseMark("S2:5"), [[2, 5]]);
  assert.deepEqual(parseMark("S2:5,7"), [[2, 5], [2, 7]]);
  assert.deepEqual(parseMark("S2:5-7"), [[2, 5], [2, 6], [2, 7]]);
  assert.deepEqual(parseMark("S1:3; S4:2"), [[1, 3], [4, 2]]);
  assert.deepEqual(parseMark("S1:1-40"), [[1, 1], [1, 40]]); // a range too wide cites only its ends
  assert.deepEqual(splitMarks("Plots [S1:1] grew [S2:2, 3]."), [
    { text: "Plots " }, { cite: [[1, 1]], text: "[S1:1]" }, { text: " grew " }, { cite: [[2, 2], [2, 3]], text: "[S2:2, 3]" }, { text: "." }]);
});

test("unclosed and stray tags are closed or ignored, and nothing is lost", () => {
  const { nodes } = parse("<p>one <strong>two <em>three</p><p>four</em></strong></p>");
  assert.equal(plainText(nodes), "one two three\nfour\n");
});

type Fake = { kind: string; tag?: string; text?: string; attrs: Record<string, string>; children: Fake[]; listeners: string[] };
function fakeDocument() {
  const element = (tag: string) => {
    const node: Fake & Record<string, unknown> = {
      kind: "element", tag, attrs: {}, children: [], listeners: [],
      setAttribute(name: string, value: string) { node.attrs[name] = value; },
      appendChild(child: Fake) { node.children.push(child); return child; },
      addEventListener(type: string) { node.listeners.push(type); },
    };
    return node;
  };
  return { createElement: element, createTextNode: (text: string) => ({ kind: "text", text, attrs: {}, children: [], listeners: [] }) };
}

test("the tree becomes elements with text nodes only, and no link that goes anywhere", () => {
  h.document = fakeDocument();
  try {
    const { nodes } = parse('<h2>Hi &lt;b&gt;</h2><p><a href="S1">source</a> says 18% [S1:1].</p><img src=x onerror=alert(1)>', new Set([1]));
    const made = build(nodes, h, { onCite: () => {}, onSource: () => {} }) as Fake[];
    const tags = new Set<string>();
    const visit = (n: Fake) => {
      if (n.kind === "element") {
        tags.add(n.tag as string);
        for (const key of Object.keys(n.attrs)) assert.ok(["type", "aria-label"].includes(key), `made the attribute ${key}`);
        n.children.forEach(visit);
      }
    };
    made.forEach((n) => (typeof n === "string" ? null : visit(n)));
    assert.deepEqual([...tags].sort(), ["button", "h2", "p", "span"]);
    assert.equal((made[0] as Fake).children[0]?.text, "Hi <b>");
  } finally {
    h.document = undefined;
  }
});
