import assert from "node:assert/strict";
import { test } from "node:test";
import { draftFromRule, fieldList, hasErrors, newDraft, preflightBody, refusedLevels, ruleBody, sameAsSaved, slotsLeft, validateDraft } from "../src/modules/hands/lib/rule.ts";
import { action, rule } from "./hands.fixtures.ts";

test("a new rule starts at the server's default level with its default argument modes", () => {
  const draft = newDraft(action({ default_level: "ask" }));
  assert.equal(draft.level, "ask");
  assert.equal(draft.args.to!.mode, "open");
  assert.equal(draft.args.subject!.mode, "hidden");
  assert.equal(draft.effect, "send");
  assert.equal(draft.showAddresses, false);
});

test("an agent's own inbox starts with addresses shown", () => {
  assert.equal(newDraft(action(), true).showAddresses, true);
});

test("the rule request is whole, pins the version and leaves the effect alone unless it was corrected", () => {
  const a = action();
  const draft = newDraft(a);
  draft.perDay = "5";
  draft.recipients = "owner";
  draft.fields = "id\n subject \n";
  const body = ruleBody("ada", "mail", a, draft)!;
  assert.equal(body.version, "v1");
  assert.equal(body.effect, undefined);
  assert.deepEqual(body.limits, { per_day: 5, recipients: "owner", max_amount: null });
  assert.deepEqual(body.fields, ["id", "subject"]);
  assert.equal(body.action, "messages.send");
  draft.effect = "update";
  assert.equal(ruleBody("ada", "mail", a, draft)!.effect, "update");
});

test("there is no request while an argument is wrong", () => {
  const a = action();
  const draft = newDraft(a);
  draft.args.to = { mode: "hidden", text: "" };
  assert.equal(ruleBody("ada", "mail", a, draft), null);
});

test("limits are checked the way the server checks them", () => {
  const a = action();
  const draft = newDraft(a);
  draft.perDay = "0";
  assert.match(validateDraft(a, draft).perDay!, /1 to 100000/);
  draft.perDay = "2.5";
  assert.ok(validateDraft(a, draft).perDay);
  draft.perDay = "100000";
  assert.equal(validateDraft(a, draft).perDay, undefined);
  draft.maxAmount = "5";
  assert.match(validateDraft(a, draft).maxAmount!, /does not carry an amount/);
  draft.maxAmount = "-1";
  assert.match(validateDraft(action({ limits_offered: { per_day: true, recipients: false, max_amount: true }, amount: "total" }), draft).maxAmount!, /0 or more/);
  draft.recipients = "owner";
  assert.match(validateDraft(action({ limits_offered: { per_day: true, recipients: false, max_amount: false } }), draft).recipients!, /does not say who receives/);
});

test("a clean form has no errors and a wrong one is reported", () => {
  const a = action();
  assert.equal(hasErrors(validateDraft(a, newDraft(a))), false);
  const draft = newDraft(a);
  draft.args.attachments = { mode: "open", text: "" };
  assert.equal(hasErrors(validateDraft(a, draft)), true);
});

test("too many fields to show are refused", () => {
  const a = action();
  const draft = newDraft(a);
  draft.fields = Array.from({ length: 51 }, (_, i) => `f${i}`).join("\n");
  assert.ok(validateDraft(a, draft).fields);
  assert.deepEqual(fieldList("a\n\n b"), ["a", "b"]);
});

test("a saved rule fills the form and saving it unchanged is recognised", () => {
  const a = action();
  const saved = rule({ limits: { per_day: 3, recipients: "known", max_amount: null }, fields: ["id"], show_addresses: true });
  const draft = draftFromRule(saved, a);
  assert.equal(draft.perDay, "3");
  assert.equal(draft.recipients, "known");
  assert.equal(sameAsSaved(saved, a, draft), true);
  draft.perDay = "4";
  assert.equal(sameAsSaved(saved, a, draft), false);
});

test("a rule pinned to an older version is not the same as the action as it is now", () => {
  const a = action({ version: "v2" });
  const saved = rule({ version: "v1" });
  assert.equal(sameAsSaved(saved, a, draftFromRule(saved, a)), false);
});

test("choosing alone for a changing action sets nothing but the level: there is no extra step", () => {
  const send = action();
  const body = ruleBody("ada", "mail", send, { ...newDraft(send), level: "alone" });
  assert.equal(body?.level, "alone");
  assert.equal(body?.confirmed, false);
  // No limit has to be set first.
  assert.equal(hasErrors(validateDraft(send, { ...newDraft(send), level: "alone" })), false);
});

test("the room left for an agent's actions counts down to nothing", () => {
  assert.equal(slotsLeft(3, 12), 9);
  assert.equal(slotsLeft(13, 12), 0);
  assert.equal(slotsLeft(3, undefined), null);
});

test("an untrusted reading starts at the level the server offers, which is not alone", () => {
  const reading = action({ effect: "read", effect_from: "provider", effect_trusted: false, default_level: "ask", level_note: "The provider itself says this action only reads." });
  assert.equal(newDraft(reading).level, "ask");
  assert.equal(newDraft(action({ effect: "read", effect_from: "adapter", effect_trusted: true, default_level: "alone" })).level, "alone");
});

test("the question asked before saving carries the effect only when corrected, and only limits that are valid", () => {
  const send = action();
  const draft = { ...newDraft(send), perDay: "3", recipients: "owner" as const, confirmed: true };
  assert.deepEqual(preflightBody("ada", "mail", send, draft), {
    agent: "ada",
    account: "mail",
    action: "messages.send",
    confirmed: true,
    limits: { per_day: 3, recipients: "owner", max_amount: null },
  });
  assert.equal(preflightBody("ada", "mail", send, { ...draft, effect: "read" }).effect, "read");
  // A half-typed number is left out, so the answer is about the level and not about the typing.
  assert.equal(preflightBody("ada", "mail", send, { ...draft, perDay: "lots" }).limits?.per_day, null);
  assert.equal(preflightBody("ada", "mail", send, { ...draft, maxAmount: "9" }).limits?.max_amount, null);
});

test("each level the server will not allow carries its reason, and an allowed one carries none", () => {
  const levels = { read: { allowed: true, reason: null }, ask: { allowed: true, reason: null }, alone: { allowed: false, reason: "Not available yet." } };
  assert.deepEqual(refusedLevels({ levels }), { alone: "Not available yet." });
  assert.deepEqual(refusedLevels(undefined), {});
  assert.match(refusedLevels({ levels: { ...levels, ask: { allowed: false, reason: null } } }).ask!, /cannot be chosen/);
});
