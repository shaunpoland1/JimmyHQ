import assert from "node:assert/strict";
import { test } from "node:test";
import type { ProviderType } from "../src/modules/hands/api.ts";
import { staleAfter } from "../src/modules/hands/lib/live.ts";
import { noteRefusal, remembered } from "../src/modules/hands/lib/refusals.ts";
import {
  EFFECT_LABELS,
  LEVEL_LABELS,
  TOKENS_AT_PROVIDER,
  cautiousAloneNote,
  forgetMessage,
  healthTone,
  levelExplanation,
  limitsLine,
  redirectSentence,
  refusalIsAboutLevel,
  removeBlockedMessage,
  removeKeyQuestion,
  removeSecretQuestion,
  ruleStateAdvice,
  ruleStateLine,
  runProblem,
  runStatus,
  secretsSentence,
  suggestionLine,
  sourceSentence,
  typeAbout,
} from "../src/modules/hands/lib/wording.ts";
import { providerType, rule } from "./hands.fixtures.ts";

test("every kind of effect has plain words", () => {
  for (const effect of ["read", "send", "create", "update", "delete", "spend", "access", "change"] as const) {
    assert.ok(EFFECT_LABELS[effect].length > 0);
  }
});

test("a guess is called a guess and a provider's answer is called a hint", () => {
  assert.match(sourceSentence("guess"), /guess/);
  assert.match(sourceSentence("provider"), /hint/);
  assert.match(sourceSentence("owner"), /You set/);
  assert.match(sourceSentence("adapter"), /connector/);
});

test("ask me each time says a Bench card shows exactly what will be sent", () => {
  assert.match(levelExplanation("ask", "send"), /Bench/);
  assert.match(levelExplanation("ask", "send"), /exactly what would be sent/);
  assert.match(levelExplanation("ask", "send"), /precisely that/);
});

test("read only on a changing action says the agent is not offered it", () => {
  assert.match(levelExplanation("read", "send"), /not offered/);
  assert.match(levelExplanation("read", "read"), /look things up/);
});

test("alone on a changing action says limits are optional and what waits outside one", () => {
  assert.match(levelExplanation("alone", "send"), /optional/);
  assert.match(levelExplanation("alone", "send"), /waits for your yes/);
  assert.equal(LEVEL_LABELS.alone, "Alone");
});

test("alone on a cautious kind carries one calm sentence, and a reading or another kind carries none", () => {
  const send = cautiousAloneNote("send", true) ?? "";
  assert.match(send, /sends something to someone/);
  assert.match(send, /without a question first/);
  assert.match(send, /Undo/);
  assert.doesNotMatch(send, /sure|warning|danger|!/i);
  assert.match(cautiousAloneNote("spend", true) ?? "", /spends money/);
  assert.match(cautiousAloneNote("create", true) ?? "", /changes something outside/);
  assert.equal(cautiousAloneNote("send", false), null);
  assert.equal(cautiousAloneNote("send", undefined), null);
  assert.equal(cautiousAloneNote("read", true), null);
});

test("the suggestion appears only when the server suggests it, and says nothing changes by itself", () => {
  const line = suggestionLine({ suggest_alone: true, approvals_in_a_row: 10, level: "ask" }, "Ada") ?? "";
  assert.match(line, /last 10 of these in a row/);
  assert.match(line, /Nothing changes unless you do/);
  assert.equal(suggestionLine({ suggest_alone: false, approvals_in_a_row: 10, level: "ask" }, "Ada"), null);
  assert.equal(suggestionLine({ suggest_alone: true, approvals_in_a_row: 10, level: "alone" }, "Ada"), null);
  assert.equal(suggestionLine({ level: "ask" }, "Ada"), null);
});

test("a refusal about the level is told apart from one about something else", () => {
  assert.equal(refusalIsAboutLevel('Nothing was changed: "Alone" is not available yet for an action that does more than read. Set it to "Ask me each time" instead.'), true);
  assert.equal(refusalIsAboutLevel("The daily limit must be a whole number from 1 to 100000."), false);
  assert.equal(refusalIsAboutLevel("This action changed at its provider since you looked at it."), false);
});

test("a refused level is remembered for that kind of action and not for another", () => {
  const memory = new Map<string, string>();
  const sentence = 'Nothing was changed: "Alone" is not available yet.';
  assert.equal(noteRefusal(memory, "send", "alone", sentence), true);
  assert.equal(remembered(memory, "send", "alone"), sentence);
  assert.equal(remembered(memory, "delete", "alone"), null);
  assert.equal(remembered(memory, "send", "ask"), null);
  assert.equal(noteRefusal(memory, "send", "ask", "The daily limit must be a whole number."), false);
  assert.equal(remembered(memory, "send", "ask"), null);
});

test("a provider's health is told in words, with a missing key and a missing connector first", () => {
  const base = { enabled: true, type_known: true, needs_key: true, has_key: true, health: { ok: true, checked_ms: 1, error: null } };
  assert.equal(healthTone(base).text, "Reachable");
  assert.equal(healthTone({ ...base, health: { ok: false, checked_ms: 1, error: "x" } }).tone, "bad");
  assert.equal(healthTone({ ...base, health: { ok: null, checked_ms: null, error: null } }).text, "Not tested");
  assert.equal(healthTone({ ...base, has_key: false }).text, "No key yet");
  assert.equal(healthTone({ ...base, enabled: false }).text, "Switched off");
  assert.equal(healthTone({ ...base, type_known: false }).text, "No connector installed");
});

test("a provider type is described in the server's words, or honestly when there are none", () => {
  const type: ProviderType = providerType({ note: "Reads and sends mail through a hosted service." });
  assert.equal(typeAbout(type), "Reads and sends mail through a hosted service.");
  assert.equal(typeAbout({ ...type, description: "A hosted mailbox." }), "A hosted mailbox. Reads and sends mail through a hosted service.");
  assert.match(typeAbout({ ...type, note: "" }), /Mailbox lets your agents use an outside service/);
  assert.match(typeAbout(undefined), /no connector/);
});

test("the secrets sentence says where a key lives and that some providers hold the sign-in", () => {
  const type: ProviderType = providerType({ type: "t", label: "T" });
  assert.match(secretsSentence(type), /secret store.*never shown again/);
  assert.match(secretsSentence(type), /provider.*hold the sign-in/i);
  assert.doesNotMatch(secretsSentence({ ...type, needs_key: false }), /secret store/);
});

test("where the sign-in tokens live is said in the type's own words when it gives them", () => {
  const hosted = providerType({ tokens: "provider", tokens_note: "The service keeps the sign-in tokens. JimmyHQ keeps only its key." });
  assert.match(secretsSentence(hosted), /^Its key is saved.*The service keeps the sign-in tokens\./);
  assert.match(redirectSentence(hosted), /The service keeps the sign-in tokens.*cancel any time/);
  assert.equal(redirectSentence(providerType()), TOKENS_AT_PROVIDER);
  assert.equal(redirectSentence(providerType({ tokens: "here", tokens_note: "Kept on this computer." })), TOKENS_AT_PROVIDER);
});

test("a rule changed upstream says it is off until reviewed", () => {
  assert.equal(ruleStateLine("changed").text, "Changed upstream: review needed");
  assert.match(ruleStateAdvice({ state: "changed", reason: null })!, /not offered it/);
  assert.equal(ruleStateAdvice({ state: "on", reason: null }), null);
  assert.equal(ruleStateAdvice({ state: "provider_off", reason: "Mail is switched off." }), "Mail is switched off.");
});

test("a run says how it ended, and an unknown outcome is never retried", () => {
  assert.equal(runStatus("done").text, "Done");
  assert.equal(runStatus("failed").text, "Failed");
  assert.equal(runStatus("unknown").text, "Outcome unknown");
  assert.match(runProblem({ status: "unknown", error_kind: "timeout" })!, /never tried again/);
  assert.equal(runProblem({ status: "done", error_kind: null }), null);
  assert.equal(runProblem({ status: "failed", error_kind: "provider" }), "The provider said no.");
  assert.match(runProblem({ status: "failed", error_kind: "odd_thing" })!, /odd thing/);
});

test("limits are summed up in one line, or not at all", () => {
  assert.equal(limitsLine(rule()), null);
  assert.equal(limitsLine(rule({ limits: { per_day: 5, recipients: "owner", max_amount: 20 } }), "total"), "5 a day, only to you, up to 20 (total)");
  assert.equal(limitsLine(rule({ limits: { per_day: null, recipients: "known", max_amount: null } })), "only to people you know");
});

test("forgetting an account says what stops working and that sign-in at the provider stays", () => {
  const one = { action: "a", tool: "mail_a", label: "A", level: "ask" as const, effect: "send" as const };
  const uses = { agents: [{ agent: "ada", rules: [one] }, { agent: "bo", rules: [one] }], rules: 2, waiting_requests: 1 };
  const text = forgetMessage("Work mail", "Mailbox", uses);
  assert.match(text, /2 switched-on actions for 2 agents \(ada, bo\) stop working/);
  assert.match(text, /1 request waiting on the Bench/);
  assert.match(text, /does not sign you out at Mailbox/);
  const none = forgetMessage("Work mail", "Mailbox", { agents: [], rules: 0, waiting_requests: 0 });
  assert.match(none, /nothing stops working/);
  assert.match(none, /Nothing of it is waiting on the Bench/);
  assert.match(forgetMessage("Work mail", null, undefined), /Any action an agent has switched on.*Bench.*at the provider/);
  assert.match(forgetMessage("W", "M", { agents: [{ agent: "ada", rules: [one] }], rules: 1, waiting_requests: 3 }), /1 switched-on action for 1 agent \(ada\) stops working.*3 requests waiting/);
});


test("removing a provider that still has accounts names them", () => {
  assert.match(removeBlockedMessage("Mailbox", ["mail", "work"]), /accounts connected \(mail, work\)/);
  assert.match(removeBlockedMessage("Mailbox", ["mail"]), /an account connected \(mail\)/);
});

test("a live event makes only the data it touches stale", () => {
  assert.deepEqual(staleAfter("hands.rule_set"), ["rules"]);
  assert.deepEqual(staleAfter("hand.called"), ["runs", "rules"]);
  assert.deepEqual(staleAfter("hands.account_forgotten"), ["accounts", "rules", "actions"]);
  assert.deepEqual(staleAfter("model.called"), []);
});

test("removing a saved key says it cannot be brought back and names the accounts that stop", () => {
  const two = removeKeyQuestion("Mailbox", ["mail", "work"]);
  assert.equal(two.title, "Remove the key for Mailbox?");
  assert.match(two.message, /cannot be brought back/);
  assert.match(two.message, /Its 2 accounts \(mail, work\) stop working until you save another key\./);
  assert.match(removeKeyQuestion("Mailbox", ["mail"]).message, /Its account \(mail\) stops working until you save another key\./);
});

test("removing a key with no accounts, or before they are known, does not name any", () => {
  assert.match(removeKeyQuestion("Mailbox", []).message, /No account is connected to Mailbox yet, and none can be until you save another key\./);
  assert.match(removeKeyQuestion("Mailbox", undefined).message, /Any account connected to Mailbox stops working until you save another key\./);
});

test("removing a secret the record needs says the provider cannot be used without it", () => {
  const asked = removeSecretQuestion("Mailbox", "signing", true, ["mail"]);
  assert.equal(asked.title, 'Remove the secret "signing"?');
  assert.match(asked.message, /Mailbox needs it and cannot be used without it\. Its account \(mail\) stops working until you save it again\./);
});

test("removing a secret the record does not refer to promises nothing about what still works", () => {
  const asked = removeSecretQuestion("Mailbox", "spare", false, ["mail"]);
  assert.match(asked.message, /cannot be brought back/);
  assert.match(asked.message, /does not refer to it\. If anything else still relies on it, that stops working/);
  assert.doesNotMatch(asked.message, /mail\)/);
});
