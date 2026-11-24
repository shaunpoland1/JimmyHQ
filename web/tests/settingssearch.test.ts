import assert from "node:assert/strict";
import { test } from "node:test";
import type { NavGroup } from "../src/lib/settingsNav.ts";
import { buildSearchIndex, excerpt, markPieces, matchRanges, moveActive, queryWords, resultsAnnouncement, searchSettings, snippetFor } from "../src/lib/settingsSearch.ts";

const part = (id: string, label: string, description = "") => ({ id, label, description });
const groups: NavGroup[] = [
  { id: "you", label: "You", pages: [{ ...part("profile", "Profile", "Who you are and how your days and weeks run."), parts: [] }, { ...part("appearance", "Appearance", "How JimmyHQ looks."), parts: [] }] },
  { id: "models", label: "Models", pages: [{ ...part("models", "Models and roles", "The providers JimmyHQ may use."), parts: [] }] },
  {
    id: "work",
    label: "Your work",
    pages: [{ ...part("memory", "Memory", "Your notes and how they are searched."), parts: [part("memory_search", "Search", "How a search finds passages."), part("memory_empty", "Nothing here")] }],
  },
  { id: "agents", label: "Agents", pages: [{ ...part("door", "Other AI apps", "Others reaching in, using the Model Context Protocol (MCP)."), parts: [] }, { ...part("village", "Village", "Your agents."), parts: [part("village_turn", "Each turn")] }] },
];
const settings = [
  { key: "owner.timezone", section: "profile", label: "Time zone", help: "Where you are, so days fall at the right time." },
  { key: "appearance.theme", section: "appearance", label: "Theme", help: "Light, dark, or your system's.", choices: [{ value: "system", label: "Match my system" }, { value: "dark", label: "Dark" }] },
  { key: "models.read_timeout_s", section: "models", label: "Give up waiting for an answer after (seconds)", help: "How long a model may take.", advanced: true },
  { key: "memory.embed_timeout_s", section: "memory_search", label: "Give up on meaning search after (seconds)", help: "If the embeddings model has not answered by then, the search uses keywords alone.", advanced: true },
  { key: "memory.related_max", section: "memory_search", label: "Related passages shown in a search", help: "At most this many passages that only seem related in meaning." },
  { key: "door.enabled", section: "door", label: "Let other AI apps connect", help: "This opens the door other AI apps come in by." },
  { key: "village.headcount_cap", section: "village", label: "Most agents at once", help: "The village will not hold more agents than this." },
  { key: "village.heartbeat_min_s", section: "village_turn", label: "Shortest pause between turns (seconds)", help: "A turn is one look, decide, act by an agent." },
];
const index = buildSearchIndex(groups, settings, { models: ["API key", "providers"], door: ["tokens"] });
const found = (query: string) => searchSettings(index, query).flat.map((result) => result.entry.id);

test("the index lists every page, every part that has settings, and every setting, in screen order", () => {
  assert.deepEqual(
    index.map((entry) => entry.id),
    [
      "page:profile", "setting:owner.timezone", "page:appearance", "setting:appearance.theme", "page:models", "setting:models.read_timeout_s",
      "page:memory", "part:memory_search", "setting:memory.embed_timeout_s", "setting:memory.related_max",
      "page:door", "setting:door.enabled", "page:village", "setting:village.headcount_cap", "part:village_turn", "setting:village.heartbeat_min_s",
    ],
  );
  const zone = index.find((entry) => entry.id === "setting:owner.timezone");
  assert.equal(zone?.href, "#/settings/profile/owner.timezone");
  assert.equal(index.find((entry) => entry.id === "part:memory_search")?.href, "#/settings/memory_search");
  const timeout = index.find((entry) => entry.id === "setting:memory.embed_timeout_s");
  assert.deepEqual([timeout?.pageLabel, timeout?.partLabel, timeout?.groupLabel, timeout?.advanced], ["Memory", "Search", "Your work", true]);
});

test("a query is its words, lower case, each once, and an empty one finds nothing", () => {
  assert.deepEqual(queryWords("  Time   ZONE time "), ["time", "zone"]);
  assert.deepEqual(searchSettings(index, "   "), { groups: [], flat: [], total: 0 });
});

test("every word must match, whatever its case, in the label, the help, the key or a choice", () => {
  assert.deepEqual(found("TIME zone"), ["setting:owner.timezone"]);
  assert.deepEqual(found("time banana"), []);
  assert.deepEqual(found("embeddings"), ["setting:memory.embed_timeout_s"]); // help
  assert.deepEqual(found("headcount_cap"), ["setting:village.headcount_cap"]); // key
  assert.deepEqual(found("headcount cap"), ["setting:village.headcount_cap"]); // key, as words
  assert.deepEqual(found("match my system"), ["setting:appearance.theme"]); // a choice
});

test("a word may match where a setting lives, so a page name narrows the others", () => {
  assert.deepEqual(found("memory seconds"), ["setting:memory.embed_timeout_s"]);
  assert.deepEqual(found("agents pause"), ["setting:village.heartbeat_min_s"]); // the group's name
  assert.deepEqual(found("each turn pause"), ["setting:village.heartbeat_min_s"]); // the sub-heading
});

test("a page's name finds the page, not every setting on it", () => {
  // The second says "village" in its help. The others only carry it at the front of their key.
  assert.deepEqual(found("village"), ["page:village", "setting:village.headcount_cap"]);
  assert.deepEqual(found("village pause"), ["setting:village.heartbeat_min_s"]);
  assert.deepEqual(found("appearance"), ["page:appearance"]);
});

test("a page drawn by hand is found by its name, its description and its keywords", () => {
  assert.deepEqual(found("roles"), ["page:models"]);
  assert.deepEqual(found("providers"), ["page:models"]);
  assert.deepEqual(found("api key"), ["page:models"]);
  assert.equal(searchSettings(index, "api key").flat[0]?.snippet?.from, "keyword");
});

test("mcp finds the page for other AI apps through its description", () => {
  assert.deepEqual(found("mcp"), ["page:door"]);
  assert.deepEqual(found("tokens"), ["page:door"]);
});

test("a few everyday other words find the same thing", () => {
  assert.deepEqual(found("timezone"), ["setting:owner.timezone"]);
  assert.deepEqual(found("colour"), ["setting:appearance.theme"]);
  assert.deepEqual(found("beats"), ["setting:village.heartbeat_min_s"]);
  assert.ok(found("embed").includes("setting:memory.embed_timeout_s"));
});

test("a match in the label comes before one in the help, and a plain setting before an advanced one", () => {
  // "search": the Memory part named Search, then labels that say it, then help lines.
  const order = found("search");
  assert.equal(order[0], "part:memory_search");
  assert.ok(order.indexOf("setting:memory.related_max") < order.indexOf("page:memory"));
  // Both say "give up" in the label; the advanced flag costs the same for each, so screen order decides.
  assert.deepEqual(found("give up"), ["setting:models.read_timeout_s", "setting:memory.embed_timeout_s"]);
  const seconds = searchSettings(index, "seconds").flat;
  assert.ok(seconds.findIndex((r) => r.entry.id === "setting:village.heartbeat_min_s") < seconds.findIndex((r) => r.entry.id === "setting:memory.embed_timeout_s"));
});

test("results are grouped by page, the page with the best match first", () => {
  const outcome = searchSettings(index, "seconds");
  assert.deepEqual(outcome.groups.map((group) => [group.pageLabel, group.groupLabel, group.results.length]), [
    ["Village", "Agents", 1],
    ["Models and roles", "Models", 1],
    ["Memory", "Your work", 1],
  ]);
  assert.equal(outcome.total, 3);
  assert.deepEqual(outcome.flat, outcome.groups.flatMap((group) => group.results));
});

test("only the best results are kept past the limit, and the total still counts them all", () => {
  const outcome = searchSettings(index, "seconds", 2);
  assert.equal(outcome.flat.length, 2);
  assert.equal(outcome.total, 3);
  assert.equal(resultsAnnouncement("seconds", outcome), "3 results. The best 2 are shown.");
});

test("the matched letters are marked, in order and without overlap", () => {
  assert.deepEqual(matchRanges("Time zone", ["zone", "time"]), [[0, 4], [5, 9]]);
  assert.deepEqual(matchRanges("Time zone", ["time", "tim"]), [[0, 4]]);
  assert.deepEqual(matchRanges("Time zone", ["timezone"]), [[0, 9]]); // through "time zone"
  assert.deepEqual(markPieces("Time zone", [[5, 9]]), [{ text: "Time ", marked: false }, { text: "zone", marked: true }]);
  assert.deepEqual(markPieces("abc", []), [{ text: "abc", marked: false }]);
  assert.deepEqual(markPieces("abc", [[2, 9]]), [{ text: "abc", marked: false }]);
});

test("a result shows why it matched when its label alone does not", () => {
  const by = (query: string, id: string) => searchSettings(index, query).flat.find((result) => result.entry.id === id);
  assert.equal(by("time zone", "setting:owner.timezone")?.snippet, null);
  const help = by("embeddings", "setting:memory.embed_timeout_s")?.snippet;
  assert.equal(help?.from, "help");
  assert.equal(help?.text.slice(help.ranges[0]![0], help.ranges[0]![1]), "embeddings");
  assert.deepEqual(by("dark", "setting:appearance.theme")?.snippet, { from: "help", text: "Light, dark, or your system's.", ranges: [[7, 11]] });
  assert.deepEqual(by("match", "setting:appearance.theme")?.snippet, { from: "choice", text: "Match my system", ranges: [[0, 5]] });
  assert.deepEqual(by("headcount cap", "setting:village.headcount_cap")?.snippet, { from: "key", text: "village.headcount_cap", ranges: [[8, 17], [18, 21]] });
  // A page always shows what is on it.
  assert.equal(by("appearance", "page:appearance")?.snippet?.text, "How JimmyHQ looks.");
  assert.equal(snippetFor(index[0]!, ["profile"])?.from, "help");
});

test("a long help line is cut around its match and the marks move with it", () => {
  const text = `${"lorem ipsum ".repeat(12)}the needle sits here ${"dolor sit ".repeat(12)}`.trim();
  const cut = excerpt(text, ["needle"], 60);
  assert.ok(cut.text.startsWith("…") && cut.text.endsWith("…") && cut.text.length <= 62);
  assert.equal(cut.text.slice(cut.ranges[0]![0], cut.ranges[0]![1]), "needle");
  assert.deepEqual(excerpt("short", ["or"]), { text: "short", ranges: [[2, 4]] });
});

test("the arrow keys move through the results and stop at the ends", () => {
  assert.equal(moveActive(0, 3, "ArrowDown"), 1);
  assert.equal(moveActive(2, 3, "ArrowDown"), 2);
  assert.equal(moveActive(0, 3, "ArrowUp"), 0);
  assert.equal(moveActive(1, 3, "End"), 2);
  assert.equal(moveActive(2, 3, "Home"), 0);
  assert.equal(moveActive(0, 0, "ArrowDown"), -1);
});

test("a screen reader is told how many results there are", () => {
  assert.equal(resultsAnnouncement("", searchSettings(index, "")), "");
  assert.equal(resultsAnnouncement("zzz", searchSettings(index, "zzz")), "No settings match.");
  assert.equal(resultsAnnouncement("time zone", searchSettings(index, "time zone")), "1 result.");
  assert.equal(resultsAnnouncement("seconds", searchSettings(index, "seconds")), "3 results.");
});
