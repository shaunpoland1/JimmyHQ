import assert from "node:assert/strict";
import { test } from "node:test";
import type { TapeEvent } from "../src/api.ts";
import { mergeEvents, parseFeedMessage } from "../src/lib/tape.ts";

function event(seq: number, kind = "task.created"): TapeEvent {
  return { seq, id: `evt_${seq}`, ts: 1_000 + seq, kind, v: 1, actor: "system", cause: null, root: null, subject: null, payload: {} };
}

test("mergeEvents de-duplicates by seq and sorts newest first", () => {
  const merged = mergeEvents([event(4), event(2, "updated")], [event(3), event(2), event(1)]);
  assert.deepEqual(merged.map((item) => item.seq), [4, 3, 2, 1]);
  assert.equal(merged[2]?.kind, "updated");
});

test("mergeEvents caps the list", () => {
  const merged = mergeEvents([event(5), event(6)], [event(1), event(2), event(3), event(4)], 3);
  assert.deepEqual(merged.map((item) => item.seq), [6, 5, 4]);
});

test("parseFeedMessage rejects anything that is not an event", () => {
  assert.equal(parseFeedMessage(JSON.stringify(event(9)))?.seq, 9);
  assert.equal(parseFeedMessage("not json"), null);
  assert.equal(parseFeedMessage(JSON.stringify({ seq: "1" })), null);
  assert.equal(parseFeedMessage("null"), null);
});
