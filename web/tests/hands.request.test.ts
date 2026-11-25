import assert from "node:assert/strict";
import { test } from "node:test";
import type { RequestValues } from "../src/modules/hands/api.ts";
import {
  GONE_SENTENCE,
  NONE_SENTENCE,
  OWNER_ONLY_SENTENCE,
  isRequestCard,
  requestView,
  valueRows,
  valueText,
} from "../src/modules/hands/lib/request.ts";

const WAITING: RequestValues = {
  card_id: "01JABCDEFGHJKMNPQRSTVWXYZ0",
  agent: "ada",
  account: "notes",
  action: "messages.send",
  tool: "notes_messages_send",
  waiting: true,
  arguments: { to: ["kit", "example.com"].join("@"), text: "  Two spaces first.\n\tA tab.\n", count: 3 },
};

test("only a request card asks for values", () => {
  assert.equal(isRequestCard("village.hand"), true);
  for (const kind of ["village.work", "hand.done", "village.handover", ""]) assert.equal(isRequestCard(kind), false, kind);
});

test("a waiting request shows every value whole, in name order", () => {
  const view = requestView(WAITING, null);
  assert.equal(view.state, "values");
  assert.ok(view.state === "values");
  assert.deepEqual(
    view.rows.map((row) => row.name),
    ["count", "text", "to"],
  );
  assert.deepEqual(view.rows[1], { name: "text", text: "  Two spaces first.\n\tA tab.\n" });
  assert.deepEqual(view.rows[0], { name: "count", text: "3" });
});

test("a value that is not text is shown as JSON, and text exactly as it is", () => {
  assert.equal(valueText("  as is \n"), "  as is \n");
  assert.equal(valueText(["a", "b"]), '[\n  "a",\n  "b"\n]');
  assert.equal(valueText(false), "false");
  assert.equal(valueText(null), "null");
  assert.deepEqual(valueRows({}), []);
});

test("an answered request says its details are no longer kept, and shows none", () => {
  const view = requestView({ ...WAITING, waiting: false, arguments: null }, null);
  assert.deepEqual(view, { state: "gone", sentence: GONE_SENTENCE });
  assert.match(GONE_SENTENCE, /no longer kept/);
  // Values the server might still send for a closed request are never shown.
  assert.equal(requestView({ ...WAITING, waiting: false }, null).state, "gone");
});

test("a card with no request, a refusal and a failure each say what to do", () => {
  assert.deepEqual(requestView(undefined, { status: 404, detail: "x" }), { state: "none", sentence: NONE_SENTENCE });
  assert.deepEqual(requestView(undefined, { status: 403, detail: "x" }), { state: "error", sentence: OWNER_ONLY_SENTENCE });
  assert.deepEqual(requestView(undefined, { status: 0, detail: "The server could not be reached." }), {
    state: "error",
    sentence: "The server could not be reached.",
  });
  assert.deepEqual(requestView(undefined, null), { state: "loading" });
});
