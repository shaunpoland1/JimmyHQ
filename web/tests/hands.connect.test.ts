import assert from "node:assert/strict";
import { test } from "node:test";
import type { Account, ConnectAnswer } from "../src/modules/hands/api.ts";
import {
  MAX_FAILURES,
  START,
  TIMEOUT_MS,
  accountNameProblem,
  accountSlug,
  connectReducer,
  keyProblem,
  pollDelay,
  providerIdProblem,
  providerSlug,
  safeUrl,
  secretNameProblem,
  shouldCheck,
  waitingLine,
} from "../src/modules/hands/lib/connect.ts";
import type { ConnectEvent, ConnectState } from "../src/modules/hands/lib/connect.ts";

const account = { account: "mail", label: "Mail" } as Account;
const redirect: ConnectAnswer = { status: "redirect", url: "https://provider.test/consent", account: null };
const run = (events: ConnectEvent[], from: ConnectState = START) => events.reduce(connectReducer, from);
const waiting = (now = 1000) => run([{ type: "start" }, { type: "answer", answer: redirect, now }]);

test("a ready answer finishes at once with the account", () => {
  const state = run([{ type: "start" }, { type: "answer", answer: { status: "ready", url: null, account }, now: 0 }]);
  assert.deepEqual(state, { phase: "ready", account });
});

test("an answer that wants a key asks for it before anything else", () => {
  assert.equal(run([{ type: "start" }, { type: "answer", answer: { status: "needs_key", url: null, account: null }, now: 0 }]).phase, "needs_key");
});

test("a redirect waits for the owner and remembers the page to open", () => {
  const state = waiting();
  assert.equal(state.phase, "waiting");
  if (state.phase === "waiting") {
    assert.equal(state.url, "https://provider.test/consent");
    assert.equal(state.polls, 0);
  }
});

test("a redirect to anything but a web page is refused", () => {
  const bad = run([{ type: "start" }, { type: "answer", answer: { status: "redirect", url: "javascript:alert(1)", account: null }, now: 0 }]);
  assert.equal(bad.phase, "failed");
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.equal(safeUrl("not a url"), null);
  assert.equal(safeUrl("https://a.test/x"), "https://a.test/x");
});

test("each check that says waiting counts and keeps waiting", () => {
  const state = run([{ type: "checking" }, { type: "checked", answer: { status: "waiting", url: null, account: null } }], waiting());
  assert.equal(state.phase === "waiting" && state.polls, 1);
  assert.equal(state.phase === "waiting" && state.checking, false);
});

test("a check that finds the account ends the wait", () => {
  const state = run([{ type: "checking" }, { type: "checked", answer: { status: "ready", url: null, account } }], waiting());
  assert.deepEqual(state, { phase: "ready", account });
});

test("a few failed checks in a row stop the wait, and one good check forgives the earlier ones", () => {
  let state = waiting();
  for (let i = 0; i < MAX_FAILURES - 1; i += 1) state = run([{ type: "checking" }, { type: "check_failed", error: "no" }], state);
  assert.equal(state.phase, "waiting");
  state = run([{ type: "checking" }, { type: "checked", answer: { status: "waiting", url: null, account: null } }], state);
  assert.equal(state.phase === "waiting" && state.failures, 0);
  for (let i = 0; i < MAX_FAILURES; i += 1) state = run([{ type: "checking" }, { type: "check_failed", error: "no" }], state);
  assert.deepEqual(state, { phase: "failed", error: "no" });
});

test("cancel ends the wait and a late answer cannot bring it back", () => {
  const state = run([{ type: "cancel" }, { type: "checked", answer: { status: "ready", url: null, account } }], waiting());
  assert.equal(state.phase, "cancelled");
});

test("the wait times out after ten minutes and not before", () => {
  const start = waiting(1000);
  assert.equal(run([{ type: "tick", now: 1000 + TIMEOUT_MS - 1 }], start).phase, "waiting");
  assert.equal(run([{ type: "tick", now: 1000 + TIMEOUT_MS }], start).phase, "timeout");
});

test("after a timeout, a cancel or a failure the owner can start again", () => {
  const timedOut = run([{ type: "tick", now: 1000 + TIMEOUT_MS }], waiting(1000));
  assert.equal(run([{ type: "again" }], timedOut).phase, "form");
  assert.equal(run([{ type: "start" }], timedOut).phase, "starting");
});

test("a start that fails says why and leaves the form to try again", () => {
  const state = run([{ type: "start" }, { type: "start_failed", error: "There is already an account called mail." }]);
  assert.deepEqual(state, { phase: "failed", error: "There is already an account called mail." });
  assert.equal(run([{ type: "start" }], state).phase, "starting");
});

test("the screen asks again only while waiting, not mid-check and not after the deadline", () => {
  const state = waiting(0);
  assert.equal(shouldCheck(state, 5000), true);
  assert.equal(shouldCheck({ ...state, checking: true } as ConnectState, 5000), false);
  assert.equal(shouldCheck(state, TIMEOUT_MS + 1), false);
  assert.equal(shouldCheck(START, 0), false);
});

test("checks come quickly at first and then ease off to a ceiling", () => {
  assert.equal(pollDelay(0), 2000);
  assert.ok(pollDelay(3) > pollDelay(0));
  assert.equal(pollDelay(100), 6000);
});

test("the waiting line says how long and how many looks", () => {
  const state = waiting(0);
  if (state.phase !== "waiting") throw new Error("expected waiting");
  assert.match(waitingLine({ ...state, polls: 1 }, 30_000), /Waiting for you to finish there\. Looked 1 time in 30 s\./);
  assert.match(waitingLine({ ...state, polls: 5, lastError: "Cannot reach the server." }, 130_000), /2 min.*last check did not work/);
});

test("short names are made from words and checked like the server checks them", () => {
  assert.equal(accountSlug("Work mail!"), "work_mail");
  assert.equal(accountSlug("42 things"), "a_42_things");
  assert.equal(accountSlug("   "), "");
  assert.equal(accountSlug("x".repeat(40)).length, 24);
  assert.equal(accountNameProblem("mail", []), null);
  assert.match(accountNameProblem("Mail", [])!, /lower-case/);
  assert.match(accountNameProblem("mail", ["mail"])!, /already an account/);
  assert.match(accountNameProblem("", [])!, /short name/);
  assert.equal(providerSlug("My Mail Box"), "my-mail-box");
  assert.match(providerIdProblem("x_", ["x_"])!, /already a provider/);
  assert.equal(providerIdProblem("", []), null);
});

test("a key must be plain visible characters and a secret's name must be new", () => {
  assert.equal(keyProblem("sk-abc123"), null);
  assert.match(keyProblem("has space")!, /spaces/);
  assert.match(keyProblem("   ")!, /first/);
  assert.match(secretNameProblem("key", [])!, /own key/);
  assert.match(secretNameProblem("signing", ["signing"])!, /saved already/);
  assert.equal(secretNameProblem("signing", []), null);
});
