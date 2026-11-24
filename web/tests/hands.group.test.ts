import assert from "node:assert/strict";
import { test } from "node:test";
import type { Account, Provider, Run } from "../src/modules/hands/api.ts";
import { alreadyOn, countOn, effectsPresent, filterActions, groupByAccount, needReview, runCard, runCardWords, runLabel, runsOfAgent, usableAccounts } from "../src/modules/hands/lib/group.ts";
import { action, rule } from "./hands.fixtures.ts";

const acct = (over: Partial<Account>): Account => ({ account: "mail", provider_id: "mailbox", label: "Mail", app: null, own_inbox: false, provider_label: "Mailbox", connected: true, receives: false, ...over });
const run = (over: Partial<Run>): Run => ({ id: "r1", agent: "ada", account: "mail", provider_id: "mailbox", action: "messages.send", tool: "mail_messages_send", effect: "send", level: "ask", status: "done", started_ms: 1, done_ms: 2, error_kind: null, card_id: null, unknown_card: null, ...over });

test("an agent's rules are grouped under their account, in name order, with the account's label", () => {
  const rules = [rule({ account: "work", action: "b", label: "B" }), rule({ account: "mail", action: "z", label: "Zed" }), rule({ account: "mail", action: "a", label: "Alpha" })];
  const groups = groupByAccount(rules, [acct({ account: "mail", label: "Home mail" }), acct({ account: "work", label: "Work" })]);
  assert.deepEqual(groups.map((g) => g.account), ["mail", "work"]);
  assert.equal(groups[0]!.label, "Home mail");
  assert.deepEqual(groups[0]!.rules.map((r) => r.label), ["Alpha", "Zed"]);
});

test("a rule whose account has gone is still shown, marked as not connected", () => {
  const groups = groupByAccount([rule({ account: "old" })], [acct({})]);
  assert.equal(groups[0]!.present, false);
  assert.equal(groups[0]!.label, "old");
  assert.equal(groupByAccount([rule({ account: "old" })], undefined)[0]!.present, true);
});

test("only connected accounts of a provider that is on can be given actions", () => {
  const providers = [{ id: "mailbox", enabled: true }, { id: "cal", enabled: false }] as Provider[];
  const accounts = [acct({}), acct({ account: "c", provider_id: "cal" }), acct({ account: "lost", connected: false })];
  assert.deepEqual(usableAccounts(accounts, providers).map((a) => a.account), ["mail"]);
});

test("searching the actions matches every word, in the label, the description or the kind of effect", () => {
  const list = [action({ name: "a", label: "List messages", description: "Reads the inbox", effect: "read" }), action({ name: "b", label: "Send a message", description: "Sends one", effect: "send" })];
  assert.deepEqual(filterActions(list, "").map((a) => a.name), ["a", "b"].sort((x, y) => list.find((a) => a.name === x)!.label.localeCompare(list.find((a) => a.name === y)!.label)));
  assert.deepEqual(filterActions(list, "inbox").map((a) => a.name), ["a"]);
  assert.deepEqual(filterActions(list, "message sends").map((a) => a.name), ["b"]);
  assert.deepEqual(filterActions(list, "", "read").map((a) => a.name), ["a"]);
  assert.deepEqual(filterActions(list, "nothing like it"), []);
  assert.deepEqual(effectsPresent(list), ["read", "send"]);
});

test("an action already switched on for the agent is marked so it is not added twice", () => {
  const taken = alreadyOn([rule({ account: "mail", action: "messages.send" }), rule({ account: "cal", action: "x" })], "mail");
  assert.deepEqual([...taken], ["messages.send"]);
});

test("rules that changed upstream are picked out and not counted as on", () => {
  const rules = [rule({ state: "on" }), rule({ state: "changed", action: "x" }), rule({ state: "on", level: "read", effect: "send", action: "y" })];
  assert.equal(needReview(rules).length, 1);
  assert.equal(countOn(rules), 1);
});

test("an agent's runs are its own and newest first", () => {
  const runs = [run({ id: "a", started_ms: 1 }), run({ id: "b", started_ms: 3 }), run({ id: "c", agent: "owner", started_ms: 5 })];
  assert.deepEqual(runsOfAgent(runs, "ada").map((r) => r.id), ["b", "a"]);
});

test("a run is named by its rule's label, or by its tool when the rule is gone", () => {
  assert.equal(runLabel(run({}), [rule({ label: "Send a message" })]), "Send a message");
  assert.equal(runLabel(run({}), []), "mail_messages_send");
});

test("a run links to the card that says what was done, else the unknown outcome, else the one that asked", () => {
  assert.equal(runCard(run({ card_id: "c1" })), "c1");
  assert.equal(runCard(run({ unknown_card: "c2" })), "c2");
  assert.equal(runCard(run({ card_id: "c1", done_card: "c3", card: "c3" })), "c3");
  assert.equal(runCard(run({ card_id: "c1", done_card: "c3" })), "c3");
  assert.equal(runCard(run({})), null);
  assert.equal(runCardWords(run({ done_card: "c3" })), "See what was done on the Bench");
  assert.equal(runCardWords(run({ unknown_card: "c2" })), "See the note on the Bench");
  assert.equal(runCardWords(run({ card_id: "c1" })), "See the card on the Bench");
});
