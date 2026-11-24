// The Reader page: the Articles view's filters.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { countsByWhat, filterArticles, fold, matchesQuery, stateWords, WHAT } from "../../apps/reader/web/lib/filters.js";

const rows = [
  { id: 1, topic_id: 1, topic: "Garden soil", state: "new", title: "Soil that holds water", host: "news.example.com", why: "A field trial.", excerpt: "", url: "https://news.example.com/a", mined: 0, offered: 0 },
  { id: 2, topic_id: 1, topic: "Garden soil", state: "kept", title: "Mulch depth", host: "blog.example.com", why: "Numbers.", excerpt: "", url: "https://blog.example.com/b", mined: 2, offered: 0 },
  { id: 3, topic_id: 2, topic: "Bees", state: "skipped", title: "Café bees", host: "garden.example.com", why: "Weak.", excerpt: "", url: "https://garden.example.com/c", mined: 0, offered: 0 },
  { id: 4, topic_id: 2, topic: "Bees", state: "sent", title: "Stems in winter", host: "garden.example.com", why: "Good.", excerpt: "", url: "https://garden.example.com/d", mined: 0, offered: 1 },
  { id: 5, topic_id: 2, topic: "Bees", state: "passed", title: "Not picked", host: "x.example.com", why: "", excerpt: "", url: "https://x.example.com/e", mined: 0, offered: 0 },
  { id: 6, topic_id: null, topic: "", state: "dropped", title: "", host: "walled.example.com", why: "", excerpt: "", url: "https://walled.example.com/f", mined: 0, offered: 0 },
];
const ids = (list: { id: number }[]) => list.map((a) => a.id);

test("everything suggested leaves out what Reader passed over or dropped", () => {
  assert.deepEqual(ids(filterArticles(rows, {})), [1, 2, 3, 4]);
  assert.deepEqual(ids(filterArticles(rows, { what: "passed" })), [5, 6]);
});

test("each filter of what happened shows only those articles", () => {
  const got = Object.fromEntries(WHAT.map(([id]: [string]) => [id, ids(filterArticles(rows, { what: id }))]));
  assert.deepEqual(got, { all: [1, 2, 3, 4], waiting: [1], kept: [2], later: [], skipped: [3], sent: [4], mined: [2], offered: [4], passed: [5, 6] });
});

test("the topic filter takes the select's string value", () => {
  assert.deepEqual(ids(filterArticles(rows, { topic: "2" })), [3, 4]);
  assert.deepEqual(ids(filterArticles(rows, { topic: "" })), [1, 2, 3, 4]);
  assert.deepEqual(countsByWhat(rows, "1").kept, 1);
});

test("search words must all be found, in any case and without accents", () => {
  assert.deepEqual(ids(filterArticles(rows, { query: "cafe" })), [3]);
  assert.deepEqual(ids(filterArticles(rows, { query: "SOIL trial" })), [1]);
  assert.deepEqual(ids(filterArticles(rows, { query: "garden.example.com winter" })), [4]);
  assert.deepEqual(ids(filterArticles(rows, { query: "   " })), [1, 2, 3, 4]);
  assert.equal(fold("Ça Été"), "ca ete");
});

test("a hostile string is searched as words and matches nothing it should not", () => {
  const hostile = { ...rows[0], title: '<img src=x onerror="alert(1)">' };
  assert.equal(matchesQuery(hostile, "onerror"), true);
  assert.equal(matchesQuery(hostile, ".*"), false);
  assert.deepEqual(filterArticles(null, {}), []);
});

test("every state has words the owner reads", () => {
  assert.equal(stateWords({ state: "later" }), "Read later");
  assert.equal(stateWords({ state: "sent" }), "Sent on");
  assert.equal(stateWords({ state: "odd" }), "odd");
});
