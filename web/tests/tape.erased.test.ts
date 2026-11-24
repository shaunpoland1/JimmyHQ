import assert from "node:assert/strict";
import { test } from "node:test";
import { ERASED_NOTE, describeErasure, describeTapeEvent } from "../src/lib/tapeRow.ts";
import { parseFeedMessage } from "../src/lib/tape.ts";

const base = { kind: "chat.message.sent", payload: {} as Record<string, unknown> };

test("an erased event is shown as erased and says what is and is not kept", () => {
  const view = describeTapeEvent({ ...base, erased: true });
  assert.equal(view.erased, true);
  assert.equal(view.detail, ERASED_NOTE);
  assert.equal(ERASED_NOTE, "Its content was erased at the owner's request. When it happened and what kind of event it was are kept.");
});

test("an event that was not erased is an ordinary row, whether the flag is false or missing", () => {
  assert.deepEqual(describeTapeEvent({ ...base, erased: false }), { erased: false, summary: null, detail: null });
  assert.deepEqual(describeTapeEvent(base), { erased: false, summary: null, detail: null });
});

test("the erasure event is described in plain words from its counts, largest first", () => {
  const payload = { reason: "chat.conversation.deleted", events: 12, kinds: { "chat.conversation.started": 1, "chat.message.answered": 5, "chat.message.sent": 6 } };
  assert.equal(
    describeErasure(payload),
    "Erased the content of 12 events (6 chat.message.sent, 5 chat.message.answered, 1 chat.conversation.started), because of chat.conversation.deleted.",
  );
  assert.equal(describeTapeEvent({ kind: "tape.erased", payload }).summary, describeErasure(payload));
});

test("one erased event is not called events", () => {
  assert.equal(describeErasure({ events: 1, kinds: { "setting.updated": 1 } }), "Erased the content of 1 event (1 setting.updated).");
});

test("an erasure event with a payload of the wrong shape still reads as a sentence", () => {
  assert.equal(describeErasure({}), "The content of some events was erased.");
  assert.equal(describeErasure({ events: "many", kinds: 4 }), "The content of some events was erased.");
  assert.equal(describeErasure({ events: 3, kinds: [1, 2] }), "Erased the content of 3 events.");
  assert.equal(describeErasure({ events: 3, kinds: { a: "x", b: 3 } }), "Erased the content of 3 events (3 b).");
});

test("the feed keeps the erased flag when it parses an event", () => {
  const event = { seq: 4, id: "e", ts: 1, kind: "x.y", v: 1, actor: "owner", cause: null, root: null, subject: null, payload: {}, erased: true };
  assert.equal(parseFeedMessage(JSON.stringify(event))?.erased, true);
});
