import assert from "node:assert/strict";
import { test } from "node:test";
import {
  HIDDEN,
  capText,
  fieldLabel,
  formatLocalTime,
  formatRelative,
  hoverHint,
  isLongText,
  kindInWords,
  payloadRows,
  redactedJson,
  shortId,
} from "../src/lib/tapeDetail.ts";

const UTC = "UTC";

test("a kind is read as plain words", () => {
  assert.equal(kindInWords("chat.message.sent"), "Chat message sent");
  assert.equal(kindInWords("tape.erased"), "Tape erased");
});

test("a field name becomes a plain label", () => {
  assert.equal(fieldLabel("sent_at"), "Sent at");
  assert.equal(fieldLabel("userId"), "User ID");
  assert.equal(fieldLabel("model-name"), "Model name");
});

test("a time in a payload is shown as a local time, not a number", () => {
  const rows = payloadRows({ sent_at: 1_700_000_000_000 }, "en-US", UTC);
  assert.equal(rows[0]!.type, "time");
  assert.equal(rows[0]!.value, formatLocalTime(1_700_000_000_000, "en-US", UTC));
});

test("a plain number that is not a time stays a number", () => {
  const rows = payloadRows({ count: 1_700_000_000_000, size: 42 }, "en-US", UTC);
  assert.equal(rows[0]!.type, "text");
  assert.equal(rows[1]!.value, "42");
});

test("a long id is shown short and keeps the full value for copying", () => {
  const id = "01HZX3K9T5Q8M2N4P6R7S8T9V0";
  const rows = payloadRows({ job_id: id });
  assert.equal(rows[0]!.type, "id");
  assert.equal(rows[0]!.value, shortId(id));
  assert.equal(rows[0]!.full, id);
});

test("a value that looks like a secret is shown as hidden", () => {
  const rows = payloadRows({ api_key: "abc", note: "token [removed]", nested: { password: "x" } });
  assert.equal(rows[0]!.value, HIDDEN);
  assert.equal(rows[1]!.value, HIDDEN);
  assert.equal(rows[3]!.value, HIDDEN);
  assert.ok(!JSON.stringify(rows).includes('"abc"'));
});

test("nested values are indented under their group", () => {
  const rows = payloadRows({ outer: { inner: "x", list: ["a", "b"] } });
  assert.deepEqual(rows.map((r) => [r.label, r.depth, r.type]), [
    ["Outer", 0, "group"],
    ["Inner", 1, "text"],
    ["List", 1, "group"],
    ["Item 1", 2, "text"],
    ["Item 2", 2, "text"],
  ]);
});

test("booleans, nulls and empty values read as words", () => {
  const rows = payloadRows({ ok: true, none: null, text: "", list: [] });
  assert.deepEqual(rows.map((r) => r.value), ["yes", "none", "empty", "empty"]);
});

test("a payload of the wrong shape gives no rows and does not throw", () => {
  assert.deepEqual(payloadRows(null), []);
  assert.deepEqual(payloadRows("text"), []);
  assert.deepEqual(payloadRows([1, 2]), []);
});

test("the raw view hides secrets at every depth", () => {
  const json = redactedJson({ token: "abc123", deep: { list: [{ secret: "s" }, "ok [removed]"] }, fine: "yes" });
  assert.ok(!json.includes("abc123"));
  assert.ok(!json.includes('"s"'));
  assert.ok(json.includes('"fine": "yes"'));
});

test("long text is capped and short text is left alone", () => {
  assert.equal(isLongText("x".repeat(201)), true);
  assert.equal(capText("short"), "short");
  assert.ok(capText("x".repeat(500)).length < 210);
});

test("relative time says how long ago in words", () => {
  const now = 1_700_000_000_000;
  assert.equal(formatRelative(now - 1000, now, "en-US"), "just now");
  assert.equal(formatRelative(now - 5 * 60_000, now, "en-US"), "5 minutes ago");
  assert.equal(formatRelative(now - 2 * 86_400_000, now, "en-US"), "2 days ago");
});

test("the hover hint names the time, the kind in words, the actor and says to click", () => {
  const hint = hoverHint({ seq: 7, ts: 1_700_000_000_000, kind: "chat.message.sent", actor: "owner", subject: null }, "en-US", UTC);
  assert.ok(hint.startsWith("#7 · "));
  assert.ok(hint.includes("Chat message sent by owner"));
  assert.ok(hint.endsWith("Click for details."));
});

test("a count of tokens is a number to read, not a secret to hide", () => {
  const rows = payloadRows({ input_tokens: 1200, output_tokens: 80, token: "abc" });
  assert.deepEqual(rows.map((r) => r.value), ["1200", "80", HIDDEN]);
  const raw = redactedJson({ input_tokens: 1200, token: "abc" });
  assert.ok(raw.includes("1200") && !raw.includes("abc"));
});
