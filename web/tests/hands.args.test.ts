import assert from "node:assert/strict";
import { test } from "node:test";
import { argsBody, argsDraft, argumentRows, canTry, fixedText, flatType, parseFixed, tryArguments } from "../src/modules/hands/lib/args.ts";
import { action } from "./hands.fixtures.ts";

const rows = argumentRows(action().arguments);
const row = (name: string) => rows.find((r) => r.name === name)!;

test("a plain argument has a flat type and a nested one has none", () => {
  assert.equal(flatType({ type: "string" }), "string");
  assert.equal(flatType({ type: ["string", "null"] }), "string");
  assert.equal(flatType({ enum: ["a", "b"] }), "enum");
  assert.equal(flatType({ enum: [1, 2] }), null);
  assert.equal(flatType({ type: "array", items: { type: "string" } }), "strings");
  assert.equal(flatType({ type: "array", items: { type: "object" } }), null);
  assert.equal(flatType({ type: "object" }), null);
  assert.equal(flatType({}), null);
});

test("required arguments are listed first, then the others by name", () => {
  assert.deepEqual(rows.map((r) => r.name), ["text", "to", "attachments", "copies", "priority", "subject"]);
  assert.equal(row("to").required, true);
  assert.deepEqual(row("priority").options, ["low", "high"]);
});

test("the form starts with required arguments open and the others not used", () => {
  const draft = argsDraft(rows, undefined);
  assert.equal(draft.to!.mode, "open");
  assert.equal(draft.subject!.mode, "hidden");
});

test("a saved fixed value comes back into its text box in the form the owner typed it", () => {
  const draft = argsDraft(rows, { copies: { mode: "fixed", value: ["a@example.com", "b@example.com"] }, subject: { mode: "fixed", value: "Hello" } });
  assert.equal(draft.copies!.text, "a@example.com\nb@example.com");
  assert.equal(draft.subject!.text, "Hello");
  assert.equal(fixedText(row("priority"), "low"), "low");
});

test("a fixed value is checked against the argument's own type", () => {
  assert.deepEqual(parseFixed({ name: "n", kind: "integer", options: [] }, "4"), { ok: true, value: 4 });
  assert.equal(parseFixed({ name: "n", kind: "integer", options: [] }, "4.5").ok, false);
  assert.equal(parseFixed({ name: "n", kind: "number", options: [] }, "abc").ok, false);
  assert.deepEqual(parseFixed({ name: "b", kind: "boolean", options: [] }, "true"), { ok: true, value: true });
  assert.equal(parseFixed({ name: "b", kind: "boolean", options: [] }, "").ok, false);
  assert.equal(parseFixed(row("priority"), "urgent").ok, false);
  assert.deepEqual(parseFixed(row("copies"), "a\n\n b \n"), { ok: true, value: ["a", "b"] });
  assert.equal(parseFixed(row("subject"), "   ").ok, false);
});

test("a structured fixed value is written as JSON", () => {
  assert.deepEqual(parseFixed(row("attachments"), '[{"a":1}]'), { ok: true, value: [{ a: 1 }] });
  assert.equal(parseFixed(row("attachments"), "not json").ok, false);
});

test("the arguments become the request the server expects", () => {
  const draft = argsDraft(rows, undefined);
  draft.subject = { mode: "fixed", text: "Weekly note" };
  const out = argsBody(rows, draft);
  assert.equal(out.ok, true);
  if (out.ok) {
    assert.deepEqual(out.body.subject, { mode: "fixed", value: "Weekly note" });
    assert.deepEqual(out.body.to, { mode: "open", value: null });
    assert.deepEqual(out.body.copies, { mode: "hidden", value: null });
  }
});

test("a required argument cannot be left out and says what to do instead", () => {
  const draft = argsDraft(rows, undefined);
  draft.to = { mode: "hidden", text: "" };
  const out = argsBody(rows, draft);
  assert.equal(out.ok, false);
  if (!out.ok) assert.match(out.errors.to!, /required.*fixed value/);
});

test("an argument the agent could not fill in is refused as open until it is fixed or hidden", () => {
  const draft = argsDraft(rows, undefined);
  draft.attachments = { mode: "open", text: "" };
  const out = argsBody(rows, draft);
  assert.equal(out.ok, false);
  if (!out.ok) assert.match(out.errors.attachments!, /not a plain value/);
});

test("a fixed argument without its value is refused", () => {
  const draft = argsDraft(rows, undefined);
  draft.subject = { mode: "fixed", text: "" };
  const out = argsBody(rows, draft);
  assert.equal(out.ok, false);
});

test("trying a reading needs its required arguments and skips blank optional ones", () => {
  const missing = tryArguments(rows, { subject: "x" });
  assert.equal(missing.ok, false);
  const ok = tryArguments(rows, { to: "me", text: "hi", subject: "" });
  assert.deepEqual(ok, { ok: true, args: { to: "me", text: "hi" } });
});

test("only an action known to just read can be tried", () => {
  assert.equal(canTry({ effect: "read", effect_from: "provider" }), true);
  assert.equal(canTry({ effect: "read", effect_from: "adapter" }), true);
  assert.equal(canTry({ effect: "read", effect_from: "guess" }), false);
  assert.equal(canTry({ effect: "read", effect_from: "owner" }), false);
  assert.equal(canTry({ effect: "send", effect_from: "provider" }), false);
});
