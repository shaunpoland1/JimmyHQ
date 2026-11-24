import assert from "node:assert/strict";
import { test } from "node:test";
import type { BrowserState } from "../src/modules/village/api.ts";
import {
  IDLE_MS,
  LEVELS,
  LEVEL_HELP,
  LEVEL_LABELS,
  WATCH_MS,
  badHost,
  frontTab,
  parseHosts,
  pictureUrl,
  refreshEvery,
  refusalWords,
  sameHosts,
  standing,
  useLine,
} from "../src/modules/village/lib/browser.ts";
import { TABS, TAB_LABELS, parseVillageRoute, tabAfterKey, villageSub } from "../src/modules/village/lib/route.ts";

const state = (over: Partial<BrowserState> = {}): BrowserState => ({
  agent_id: "agent-a",
  held: true,
  ready: true,
  running: false,
  tabs: [],
  submit_level: "approve",
  allow_hosts: [],
  ...over,
});

test("the browser has its own tab on the agent page, before the activity log", () => {
  assert.equal(TAB_LABELS.browser, "Browser");
  assert.equal(TABS.indexOf("browser"), TABS.indexOf("activity") - 1);
  assert.deepEqual(parseVillageRoute("agent-a/browser"), { agent: "agent-a", tab: "browser" });
  assert.equal(villageSub({ agent: "agent-a", tab: "browser" }), "agent-a/browser");
  assert.equal(tabAfterKey("ArrowRight", "integrations"), "browser");
  assert.equal(tabAfterKey("ArrowRight", "browser"), "activity");
});

test("the three levels are named the way the owner chooses them, cautious one in the middle", () => {
  assert.deepEqual([...LEVELS], ["read", "approve", "alone"]);
  assert.equal(LEVEL_LABELS.read, "Read only");
  assert.equal(LEVEL_LABELS.approve, "Ask me each time");
  assert.equal(LEVEL_LABELS.alone, "Alone");
  assert.match(LEVEL_HELP.approve, /exactly what would be sent/);
  assert.match(LEVEL_HELP.approve, /only if the page has not changed/);
  assert.match(LEVEL_HELP.read, /never sent/);
  assert.match(LEVEL_HELP.alone, /without asking/);
});

test("the tab says plainly where the browser stands", () => {
  assert.match(standing(state({ held: false }), "Ada"), /Ada has no browser\. Give it "A browser of its own"/);
  assert.match(standing(state({ ready: false }), "Ada"), /none is installed on this machine/);
  assert.match(standing(state(), "Ada"), /browser is closed\. It opens when Ada next opens a page/);
  const one = [{ url: "https://example.com/", title: "Example", active: true }];
  assert.equal(standing(state({ running: true, tabs: one }), "Ada"), "Ada's browser is open with 1 tab.");
  assert.equal(standing(state({ running: true, tabs: [...one, ...one] }), "Ada"), "Ada's browser is open with 2 tabs.");
});

test("an open browser is read again often and a closed one seldom", () => {
  assert.equal(refreshEvery(state({ running: true })), WATCH_MS);
  assert.equal(refreshEvery(state()), IDLE_MS);
  assert.equal(refreshEvery(undefined), IDLE_MS);
  assert.ok(WATCH_MS >= 2_000, "watching must stay gentle on the server");
});

test("today's use is shown against its limit, and a spent limit is said", () => {
  assert.deepEqual(useLine({ used: 12, cap: 100 }), { text: "12 of 100 today", spent: false });
  assert.deepEqual(useLine({ used: 100, cap: 100 }), { text: "100 of 100 today", spent: true });
  assert.deepEqual(useLine({ used: 0, cap: 0 }), { text: "0 of 0 today", spent: true });
  assert.equal(useLine(undefined), null);
});

test("each new picture has a new address, and no picture has none", () => {
  assert.equal(pictureUrl("agent-a", 1700000000000), "/api/village/agents/agent-a/browser/picture?t=1700000000000");
  assert.notEqual(pictureUrl("agent-a", 1), pictureUrl("agent-a", 2));
  assert.equal(pictureUrl("agent-a", null), null);
  assert.equal(pictureUrl("agent-a", undefined), null);
});

test("the tab in front is the one shown, falling back to the first", () => {
  const tabs = [
    { url: "https://example.com/a", title: "A", active: false },
    { url: "https://example.com/b", title: "B", active: true },
  ];
  assert.equal(frontTab(tabs)?.title, "B");
  assert.equal(frontTab([{ ...tabs[0]! }])?.title, "A");
  assert.equal(frontTab([]), null);
});

test("an allow-list is read one site to a line, tidied, with no site twice", () => {
  assert.deepEqual(parseHosts("Example.com\n *.example.org ,example.com\n\n.docs.example.com."), ["example.com", "*.example.org", "docs.example.com"]);
  assert.deepEqual(parseHosts("  \n"), []);
  assert.ok(sameHosts(["a.example", "b.example"], ["a.example", "b.example"]));
  assert.ok(!sameHosts(["a.example"], ["a.example", "b.example"]));
  assert.ok(!sameHosts(["b.example", "a.example"], ["a.example", "b.example"]));
});

test("an entry that is not a site's name on its own is caught before it is sent", () => {
  assert.equal(badHost(["example.com", "*.example.org"]), null);
  assert.equal(badHost(["https://example.com"]), "https://example.com");
  assert.equal(badHost(["example.com/login"]), "example.com/login");
  assert.equal(badHost(["example.com:8080"]), "example.com:8080");
  assert.equal(badHost(["*"]), "*");
});

test("a refusal by the fence is said in a few plain words", () => {
  assert.equal(refusalWords({ host: "pay.example.com", rule: "browser_hard_deny" }), "pay.example.com: on your deny list");
  assert.equal(refusalWords({ host: "router.example", rule: "browser_public_address" }), "router.example: on this machine or a private network");
  assert.equal(refusalWords({ host: "x.example", rule: "browser_allow_list" }), "x.example: not on this agent's allow-list");
  assert.equal(refusalWords({ host: "", rule: "something_new" }), "an address: refused");
});
