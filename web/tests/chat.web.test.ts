import assert from "node:assert/strict";
import { test } from "node:test";
import type { AssistantMessage, ChatOptions, UserMessage, WebUsed } from "../src/modules/chat/api.ts";
import { interpretReplyEvent, newLiveReply } from "../src/modules/chat/lib/reply.ts";
import {
  applyStep,
  inferWeb,
  isWorking,
  linkBareAddresses,
  readStepEvent,
  resultLines,
  rowLabel,
  searchTarget,
  settleOpen,
  summaryLine,
  webHelp,
  webNote,
  webStart,
  webToggle,
} from "../src/modules/chat/lib/web.ts";
import type { StepRow } from "../src/modules/chat/lib/web.ts";

function run(...events: unknown[]): StepRow[] {
  let rows: StepRow[] = [];
  for (const event of events) {
    const step = readStepEvent(event);
    assert.ok(step, JSON.stringify(event));
    rows = applyStep(rows, step);
  }
  return rows;
}

const SEARCHING = { type: "searching", id: 1, query: "broad beans sowing time" };
const RESULTS = { type: "results", id: 1, results: [{ title: "Growing broad beans", url: "https://example.com/beans" }, { title: "Runner beans", url: "https://example.com/runner" }] };
const READING = { type: "reading", id: 2, url: "https://example.com/beans" };
const READ = { type: "read", id: 2, url: "https://example.com/beans", title: "Growing broad beans", bytes: 48213, truncated: false };

// ---- reading the events ----

test("each of the five step events is read, and anything else is left out", () => {
  for (const event of [SEARCHING, RESULTS, READING, READ, { type: "failed", id: 2, detail: "Nope." }, { type: "failed", id: null, detail: "Off." }]) {
    assert.notEqual(readStepEvent(event), null, JSON.stringify(event));
  }
  for (const bad of [null, "text", [], { type: "searching" }, { type: "searching", id: "1", query: "q" }, { type: "reading", id: 1 }, { type: "failed", id: 1, detail: "" }, { type: "mystery", id: 1 }, { type: "results", id: 1, results: "x" }]) {
    assert.equal(readStepEvent(bad), null, JSON.stringify(bad));
  }
});

test("a result with no address is dropped and one with no title keeps its address", () => {
  const step = readStepEvent({ type: "results", id: 1, results: [{ title: "A" }, { url: "https://example.com/x" }] });
  assert.deepEqual(step, { type: "results", id: 1, results: [{ title: "", url: "https://example.com/x" }] });
});

test("a step event on the reply stream is read as a step, and a broken one is ignored", () => {
  const good = interpretReplyEvent({ event: "step", data: JSON.stringify(SEARCHING) });
  assert.deepEqual(good, { type: "step", step: SEARCHING });
  assert.equal(interpretReplyEvent({ event: "step", data: "{not json" }), null);
  assert.equal(interpretReplyEvent({ event: "step", data: JSON.stringify({ type: "mystery" }) }), null);
});

test("a new live reply starts with no steps", () => {
  assert.deepEqual(newLiveReply("hello").steps, []);
});

// ---- the lines ----

test("a search is opened and then settled by its results", () => {
  const open = run(SEARCHING);
  assert.equal(open.length, 1);
  assert.equal(isWorking(open), true);
  const done = run(SEARCHING, RESULTS);
  assert.equal(done.length, 1);
  const row = done[0];
  assert.ok(row && row.kind === "search");
  assert.equal(row.state, "done");
  assert.equal(row.query, "broad beans sowing time");
  assert.equal(row.results.length, 2);
  assert.equal(isWorking(done), false);
});

test("a page is opened by reading and settled by read, with where it was read from after redirects", () => {
  const rows = run(READING, { ...READ, url: "https://example.com/beans/final" });
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.ok(row && row.kind === "page");
  assert.equal(row.url, "https://example.com/beans/final");
  assert.equal(row.state, "done");
});

test("a failure closes the step it names and carries the server's sentence", () => {
  const rows = run(READING, { type: "failed", id: 2, detail: "example.com has no page at that address (HTTP 404). Try another page." });
  assert.equal(rows.length, 1);
  assert.equal(rows[0]?.state, "failed");
  assert.equal(rowLabel(rows[0] as StepRow), "example.com has no page at that address (HTTP 404). Try another page.");
});

test("a failure that closes nothing becomes a line of its own, with or without an id", () => {
  const refused = run({ type: "failed", id: 7, detail: "That address is not one this reply may read." });
  assert.deepEqual(refused, [{ kind: "problem", id: 7, state: "failed", detail: "That address is not one this reply may read." }]);
  const whole = run(SEARCHING, { type: "failed", id: null, detail: "Web lookup is switched off." });
  assert.equal(whole.length, 2);
  assert.equal(whole[0]?.state, "open");
  assert.equal(whole[1]?.kind, "problem");
});

test("several rounds keep every step in the order they arrived", () => {
  const rows = run(SEARCHING, RESULTS, READING, READ, { type: "searching", id: 3, query: "second" });
  assert.deepEqual(rows.map((row) => `${row.kind}:${row.state}`), ["search:done", "page:done", "search:open"]);
});

test("an event the reply sends twice does not make a second line", () => {
  assert.equal(run(SEARCHING, SEARCHING).length, 1);
});

test("a very long run keeps only the newest lines", () => {
  let rows: StepRow[] = [];
  for (let id = 1; id <= 100; id += 1) rows = applyStep(rows, { type: "searching", id, query: `q${id}` });
  assert.equal(rows.length, 60);
  assert.equal(rows[0]?.id, 41);
});

test("stopping marks what was still open as stopped and leaves the rest alone", () => {
  const rows = settleOpen(run(SEARCHING, RESULTS, READING));
  assert.deepEqual(rows.map((row) => row.state), ["done", "stopped"]);
  assert.equal(isWorking(rows), false);
  assert.match(rowLabel(rows[1] as StepRow), /^Stopped while reading example\.com\/beans$/);
});

// ---- what a line says ----

test("an open search says what is being searched for", () => {
  assert.equal(rowLabel(run(SEARCHING)[0] as StepRow), "Searching for “broad beans sowing time”…");
});

test("a search that came back says how many results, and none says none", () => {
  assert.equal(rowLabel(run(SEARCHING, RESULTS)[0] as StepRow), "2 results for “broad beans sowing time”");
  assert.equal(rowLabel(run(SEARCHING, { type: "results", id: 1, results: [] })[0] as StepRow), "No results for “broad beans sowing time”");
  assert.equal(rowLabel(run(SEARCHING, { type: "results", id: 1, results: [RESULTS.results[0]] })[0] as StepRow), "1 result for “broad beans sowing time”");
});

test("a page being read shows its host and path with no scheme or query", () => {
  assert.equal(rowLabel(run({ type: "reading", id: 2, url: "https://example.com/beans?x=1" })[0] as StepRow), "Reading example.com/beans…");
});

test("a page that was read shows its title and its size, and says when it was cut short", () => {
  assert.match(rowLabel(run(READING, READ)[0] as StepRow), /^Read: Growing broad beans \(47 KB\)$/);
  assert.match(rowLabel(run(READING, { ...READ, truncated: true })[0] as StepRow), /^Read: Growing broad beans \(47 KB, cut short\)$/);
});

test("a page read with no title is named by its address", () => {
  assert.equal(rowLabel(run(READING, { ...READ, title: "", bytes: 0 })[0] as StepRow), "Read: example.com/beans");
});

test("the result lines carry a host and a link only for an ordinary web address", () => {
  const lines = resultLines([{ title: "Growing broad beans", url: "https://www.example.com/beans" }, { title: "", url: "https://example.org/a" }, { title: "Odd", url: "javascript:alert(1)" }]);
  assert.deepEqual(lines[0], { title: "Growing broad beans", host: "example.com", href: "https://www.example.com/beans" });
  assert.equal(lines[1]?.title, "example.org");
  assert.equal(lines[2]?.href, null);
});

// ---- the folded line ----

test("with nothing happening there is no summary", () => {
  assert.equal(summaryLine([]), null);
});

test("while a step is open the summary says what is being done", () => {
  assert.equal(summaryLine(run(SEARCHING, RESULTS, READING)), "Reading example.com/beans…");
});

test("once everything is settled the summary counts what was done", () => {
  assert.equal(summaryLine(run(SEARCHING, RESULTS, READING, READ)), "Searched once, read 1 page");
  assert.equal(
    summaryLine(run(SEARCHING, RESULTS, { type: "searching", id: 3, query: "b" }, { type: "results", id: 3, results: [] }, READING, { type: "failed", id: 2, detail: "x." })),
    "Searched 2 times, 1 lookup did not work",
  );
});

test("a stopped lookup says it was stopped", () => {
  assert.equal(summaryLine(settleOpen(run(READING))), "Stopped");
});

// ---- what a finished reply keeps ----

function used(over: Partial<WebUsed> = {}): WebUsed {
  return {
    used: true,
    reason: null,
    searches: 1,
    pages: 1,
    failed: 0,
    sources: [
      { title: "Growing broad beans", url: "https://example.com/beans", read: true },
      { title: "Runner beans", url: "https://example.com/runner", read: false },
    ],
    ...over,
  };
}

test("a reply that did not use the web has no note", () => {
  assert.equal(webNote(null), null);
  assert.equal(webNote(undefined), null);
});

test("a reply that read pages says how many sources it used, pages first", () => {
  const note = webNote(used());
  assert.ok(note && note.kind === "used");
  assert.equal(note.label, "Used 1 source");
  assert.deepEqual(note.read.map((line) => line.host), ["example.com"]);
  assert.equal(note.seen.length, 1);
  assert.equal(note.detail, "1 search, 1 page read");
});

test("several pages read make a plural label", () => {
  const web = used({ pages: 2, sources: [1, 2].map((n) => ({ title: `T${n}`, url: `https://example.com/${n}`, read: true })) });
  const note = webNote(web);
  assert.ok(note && note.kind === "used");
  assert.equal(note.label, "Used 2 sources");
});

test("a reply that only saw search results says so and does not claim it used them", () => {
  const note = webNote(used({ pages: 0, sources: [{ title: "Only", url: "https://example.com/o", read: false }] }));
  assert.ok(note && note.kind === "used");
  assert.equal(note.label, "Looked at 1 search result");
  assert.equal(note.read.length, 0);
});

test("a source's link is made only for an ordinary web address", () => {
  const note = webNote(used({ sources: [{ title: "Bad", url: "javascript:alert(1)", read: true }, { title: "", url: "https://example.com/x", read: true }] }));
  assert.ok(note && note.kind === "used");
  assert.equal(note.read[0]?.href, null);
  assert.equal(note.read[1]?.title, "example.com");
  assert.equal(note.read[1]?.href, "https://example.com/x");
});

test("a reply that looked nothing up shows the server's reason, or says the model chose not to", () => {
  assert.deepEqual(webNote({ used: false, reason: "Web lookup is switched off.", searches: 0, pages: 0, failed: 0, sources: [] }), { kind: "skipped", reason: "Web lookup is switched off." });
  const quiet = webNote({ used: false, reason: null, searches: 0, pages: 0, failed: 0, sources: [] });
  assert.ok(quiet && quiet.kind === "skipped");
  assert.match(quiet.reason, /did not look anything up/);
});

test("a reply whose lookups all failed says so", () => {
  const note = webNote({ used: true, reason: null, searches: 1, pages: 0, failed: 2, sources: [] });
  assert.ok(note && note.kind === "skipped");
  assert.match(note.reason, /2 lookups did not work/);
});

// ---- the switch ----

function message(web: WebUsed | null | undefined, role: "assistant" | "user" = "assistant"): AssistantMessage | UserMessage {
  if (role === "user") return { id: "u", role: "user", created: 1, text: "hi", on_record: true };
  return {
    id: "a", role: "assistant", created: 2, text: "yes", on_record: true, reply_to: "u", state: "complete", error: null, provider_id: null, provider_type: null, model: null, local: null,
    fallback_from: null, fallback_reason: null, input_tokens: null, output_tokens: null, duration_ms: 1, omitted: 0, memory: null, web,
  };
}

test("a conversation reloaded learns the switch from its last reply", () => {
  assert.equal(inferWeb([message(null, "user"), message(used())]), true);
  assert.equal(inferWeb([message(null, "user"), message(null)]), false);
  assert.equal(inferWeb([message(used()), message(null, "user"), message(null)]), false);
});

test("a reply that asked for the web and used none of it still counts as web on", () => {
  assert.equal(inferWeb([message({ used: false, reason: null, searches: 0, pages: 0, failed: 0, sources: [] })]), true);
});

test("a reply from before web lookup existed reads as off", () => {
  assert.equal(inferWeb([message(undefined)]), false);
});

test("with no reply to learn from the switch is not inferred", () => {
  assert.equal(inferWeb([]), null);
  assert.equal(inferWeb([message(null, "user")]), null);
});

const WEB_ON: ChatOptions["web"] = { enabled: true, default: true, search: true, help: "" };
const STATUS_READY = { enabled: true, search_ready: true, search_problem: null, chat_default: true };

test("the switch is disabled while nothing is known", () => {
  assert.deepEqual(webToggle(undefined, undefined), { kind: "loading", disabled: true, reason: null });
});

test("the switch is usable when web lookup is on and a provider is ready", () => {
  assert.equal(webToggle(WEB_ON, STATUS_READY).disabled, false);
  assert.equal(webToggle(WEB_ON, undefined).disabled, false);
});

test("the switch is disabled with a sentence when web lookup is off", () => {
  const toggle = webToggle({ ...WEB_ON, enabled: false }, undefined);
  assert.equal(toggle.kind, "off");
  assert.equal(toggle.reason, "Web lookup is off.");
  assert.equal(webToggle(WEB_ON, { ...STATUS_READY, enabled: false }).kind, "off");
});

test("with no search provider the switch still works for reading pages and shows the server's sentence", () => {
  const problem = "Choose a search provider under Settings, Web lookup.";
  const toggle = webToggle(WEB_ON, { ...STATUS_READY, search_ready: false, search_problem: problem });
  assert.deepEqual(toggle, { kind: "unready", disabled: false, reason: problem });
  assert.equal(webToggle({ ...WEB_ON, search: false }, undefined).kind, "unready");
});

test("the fresher status wins over the composer's options", () => {
  assert.equal(webToggle({ ...WEB_ON, enabled: false }, STATUS_READY).disabled, false);
});

test("a new conversation starts with the switch as the setting says, and only while web lookup is on", () => {
  assert.equal(webStart(WEB_ON, STATUS_READY), true);
  assert.equal(webStart(WEB_ON, { enabled: true, chat_default: false }), false);
  assert.equal(webStart(WEB_ON, { enabled: false, chat_default: true }), false);
  assert.equal(webStart({ ...WEB_ON, default: true }, undefined), true);
  assert.equal(webStart(undefined, undefined), false);
});

test("the help says what leaves the machine: the search words, the pages, and the model that answers", () => {
  const text = webHelp(true, { searchTarget: "Exa", localOnly: false });
  assert.match(text, /search words go to Exa/);
  assert.match(text, /Pages are fetched from this computer/);
  assert.match(text, /hosted, what the pages say is sent to its provider/);
});

test("in a local only conversation the help says page text stays with your own models", () => {
  assert.match(webHelp(true, { searchTarget: "Exa", localOnly: true }), /local only/);
  assert.doesNotMatch(webHelp(true, { searchTarget: "Exa", localOnly: true }), /provider/);
});

test("the help for web off says nothing is searched", () => {
  assert.match(webHelp(false, { searchTarget: "Exa", localOnly: false }), /will not search the web/);
});

test("the search provider is named, and SearXNG is your own server", () => {
  assert.equal(searchTarget("exa", "Exa"), "Exa");
  assert.equal(searchTarget("searxng", "SearXNG (your own server)"), "your own SearXNG server");
  assert.equal(searchTarget("none", "None"), "the search provider you chose");
  assert.equal(searchTarget(undefined, undefined), "the search provider you chose");
});

// ---- addresses in a reply ----

test("a bare address in a reply becomes a link, without the full stop after it", () => {
  assert.equal(linkBareAddresses("See https://example.com/beans."), "See <https://example.com/beans>.");
  assert.equal(linkBareAddresses("Both https://example.com/a, and https://example.org/b!"), "Both <https://example.com/a>, and <https://example.org/b>!");
});

test("an address in a link, a code span or a code block is left alone", () => {
  assert.equal(linkBareAddresses("[beans](https://example.com/beans)"), "[beans](https://example.com/beans)");
  assert.equal(linkBareAddresses("[https://example.com](https://example.com)"), "[https://example.com](https://example.com)");
  assert.equal(linkBareAddresses("run `curl https://example.com/x` now"), "run `curl https://example.com/x` now");
  assert.equal(linkBareAddresses("<https://example.com/a>"), "<https://example.com/a>");
  assert.equal(linkBareAddresses("```\ncurl https://example.com\n```\nthen https://example.com"), "```\ncurl https://example.com\n```\nthen <https://example.com>");
});

test("a bare address in brackets keeps the closing bracket outside the link", () => {
  assert.equal(linkBareAddresses("beans (see https://example.com/beans)"), "beans (see <https://example.com/beans>)");
  assert.equal(linkBareAddresses("(https://example.com/a_(b))"), "(<https://example.com/a_(b)>)");
});

test("an address that is not a web address is not made into a link", () => {
  assert.equal(linkBareAddresses("javascript:alert(1) and ftp://example.com/a"), "javascript:alert(1) and ftp://example.com/a");
  assert.equal(linkBareAddresses("no addresses here"), "no addresses here");
});
