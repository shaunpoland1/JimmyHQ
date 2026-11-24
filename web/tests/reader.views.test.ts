// The Reader page: each view draws from what the program answers, with no browser,
// and an article's words reach the page only as text.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page helper is plain JavaScript, supplied to every app's page as _ui.js.
import { h } from "../../sdk/js/ui.js";
import "./app_pages.ts";  // the views import "../_ui.js", as JimmyHQ supplies it

// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
const articles = await import("../../apps/reader/web/views/articles.js");
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
const offered = await import("../../apps/reader/web/views/offered.js");
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
const topics = await import("../../apps/reader/web/views/topics.js");
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
const triage = await import("../../apps/reader/web/views/triage.js");

type Node = { kind: string; tag?: string; text?: string; children: Node[]; attrs: Record<string, string>; [k: string]: unknown };

function fakeDocument() {
  const element = (tag: string): Node => {
    const node: Node = {
      kind: "element", tag, children: [], attrs: {}, value: "", checked: false, disabled: false, selected: false, open: false,
      setAttribute(name: string, value: string) { node.attrs[name] = value; },
      appendChild(child: Node) { node.children.push(child); return child; },
      replaceChildren(...kids: Node[]) { node.children = kids; },
      addEventListener() {},
      set textContent(value: string) { node.children = [{ kind: "text", text: value, children: [], attrs: {} }]; },
    };
    return node;
  };
  return { createElement: element, createTextNode: (text: string) => ({ kind: "text", text, children: [], attrs: {} }) };
}

function texts(node: Node, out: string[] = []): string[] {
  if (node.kind === "text") out.push(String(node.text));
  for (const child of node.children ?? []) texts(child, out);
  return out;
}

function tags(node: Node, out: string[] = []): string[] {
  if (node.tag) out.push(node.tag);
  for (const child of node.children ?? []) tags(child, out);
  return out;
}

const HOSTILE = '<script>steal()</script><img src=x onerror="alert(1)">';
const article = {
  id: 7, url: "https://news.example.com/a", final_url: "https://news.example.com/a", host: "news.example.com",
  title: HOSTILE, topic_id: 1, topic: "Garden soil", why: `Garden soil. Ranker: ${HOSTILE}`, by: "plain", state: "new",
  said: "", mined: 0, offered: 0, note: "", created: 1, suggested: 1, decided: null, text_deleted: null, audience: "you",
  excerpt: HOSTILE, has_text: true, on_bench: false,
};
const topic = {
  id: 1, name: "Garden soil", phrasings: ["soil water"], audience: "", outcomes: ["bench"], researcher: 1, paused: 0,
  created: 1, covered_by: "plain",
  health: { found: 3, kept: 1, skipped: 1, later: 0, waiting: 1, decided: 2, last_found_ms: 1, quiet_days: 20, quiet: true },
};

function context(more: Record<string, unknown> = {}) {
  return {
    state: {
      view: "triage", at: 0, full: null, opened: null, editing: null, filters: { topic: "", what: "all", query: "" },
      data: { topics: [topic], triage: [article], hint: "", mining_on: false, researcher: { held: false, label: "", late: null }, limits: { default_outcome: "list" } },
      articles: [article], offered: [{ offer: 1, article: 7, url: article.url, title: HOSTILE, topic: "Garden soil", why: HOSTILE, text_ref: "reader:article:7", when_ms: 1 }],
      ...more,
    },
    zone: "UTC", call: async () => ({}), render() {}, go() {}, refresh: async () => {},
  };
}

function drawn(view: { render: (ctx: unknown) => Node }, ctx = context()) {
  h.document = fakeDocument();
  try {
    return view.render(ctx);
  } finally {
    h.document = undefined;
  }
}

test("triage shows the article's why, source and excerpt, and hostile words stay text", () => {
  const out = drawn(triage);
  const words = texts(out);
  assert.ok(words.includes(HOSTILE));
  assert.ok(words.some((w) => w.startsWith("Garden soil. Ranker:")));
  assert.ok(words.includes("news.example.com · Garden soil"));
  assert.ok(!tags(out).includes("script") && !tags(out).includes("img"));
});

test("triage with nothing waiting says so", () => {
  const ctx = context();
  ctx.state.data.triage = [];
  assert.ok(texts(drawn(triage, ctx)).includes("Nothing is waiting for you."));
});

test("the articles, topics and offered views draw, and offer a pause for a quiet topic", () => {
  assert.ok(texts(drawn(articles)).includes(HOSTILE));
  const topicWords = texts(drawn(topics)).join(" | ");
  assert.ok(topicWords.includes("Quiet for 20 days. Pause it, or try other phrasings?"));
  assert.ok(topicWords.includes("Found 3 · kept 1 · skipped 1 · 1 waiting"));
  assert.ok(texts(drawn(offered)).includes(HOSTILE));
});

test("a first run shows the sentence of why and the form for a topic", () => {
  const ctx = context({ editing: "new" });
  ctx.state.data = { ...ctx.state.data, topics: [], hint: "Reader looks for articles on topics you choose, so it has nothing to look for yet. Add your first topic." };
  const words = texts(drawn(topics, ctx));
  assert.ok(words.some((w) => w.startsWith("Reader looks for articles on topics you choose")));
  assert.ok(words.includes("A new topic") && words.includes("Save"));
});

test("with no researcher the topics screen says where to give the role", () => {
  const ctx = context({});
  const words = texts(drawn(topics, ctx));
  assert.ok(words.some((w) => w.includes("on the Apps screen: open Reader, then Roles.")));
});

function buttons(node: Node, out: string[] = []): string[] {
  if (node.tag === "button") out.push(texts(node).join(""));
  for (const child of node.children ?? []) buttons(child, out);
  return out;
}

test("triage shows each choice's letter, and no letters once the keys are switched off", () => {
  const on = drawn(triage);
  assert.ok(tags(on).includes("kbd"));
  assert.ok(texts(on).some((w) => w.startsWith("y keep · l read later · x skip")));
  assert.ok(texts(on).some((w) => w.includes("switch the letter keys off in Reader's settings")));
  const ctx = context();
  (ctx.state.data as Record<string, unknown>).keys_on = false;
  const off = drawn(triage, ctx);
  assert.ok(!tags(off).includes("kbd"));
  assert.ok(texts(off).some((w) => w.startsWith("The letter keys are off")));
  assert.ok(buttons(off).includes("Skip"));
});

test("triage says when the article on show is already offered by its topic", () => {
  const ctx = context();
  ctx.state.data.triage = [{ ...article, offered: 1 }];
  assert.ok(texts(drawn(triage, ctx)).some((w) => w.startsWith("Offered to other apps already, as its topic says.")));
  assert.ok(!texts(drawn(triage)).some((w) => w.startsWith("Offered to other apps already")));
});

test("every offered article has a button that takes it back, in Offered and in Articles", () => {
  assert.ok(buttons(drawn(offered)).includes("Take it back"));
  const sent = { ...article, state: "sent", offered: 1, sentences: [], facts: [] };
  const open = drawn(articles, context({ articles: [sent], opened: sent }));
  assert.ok(buttons(open).includes("Take it back"));
  const plain = { ...article, state: "kept", offered: 0, sentences: [], facts: [] };
  assert.ok(!buttons(drawn(articles, context({ articles: [plain], opened: plain }))).includes("Take it back"));
});
