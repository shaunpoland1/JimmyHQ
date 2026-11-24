import assert from "node:assert/strict";
import { test } from "node:test";
import type { ApiToken } from "../src/modules/access/api.ts";
import { LEVELS, countLine, labelProblem, levelLabel, madeLine, ordered, revokeMessage, revokedLine } from "../src/modules/access/lib/tokens.ts";

function token(changes: Partial<ApiToken> = {}): ApiToken {
  return { id: "01JABCDEFGHJKMNPQRSTVWXYZ0", label: "hallway screen", level: "reader", scopes: ["read"], created: 1_800_000_000_000, revoked: null, ...changes };
}

test("a label that can be sent has no problem", () => {
  assert.equal(labelProblem("hallway screen"), null);
  assert.equal(labelProblem("  backup script  "), null);
});

test("each thing the server would refuse in a label is caught before it is sent", () => {
  assert.match(labelProblem("") ?? "", /Give the token a label/);
  assert.match(labelProblem("   ") ?? "", /Give the token a label/);
  assert.match(labelProblem("x".repeat(65)) ?? "", /64 characters/);
  assert.equal(labelProblem("x".repeat(64)), null);
  assert.match(labelProblem("has:colon") ?? "", /colon/);
  assert.match(labelProblem("tab\there") ?? "", /letters, digits/);
});

test("there are two levels, and only the writer can make changes", () => {
  assert.deepEqual(LEVELS.map((entry) => entry.value), ["reader", "writer"]);
  assert.match(LEVELS[0]?.help ?? "", /Cannot change anything/);
  assert.match(LEVELS[1]?.help ?? "", /Cannot change settings, passwords or tokens/);
  assert.equal(levelLabel("writer"), "Writer");
});

test("live tokens come first, newest first, and revoked ones are kept apart", () => {
  const old = token({ id: "a", created: 1 });
  const fresh = token({ id: "b", created: 2 });
  const gone = token({ id: "c", created: 3, revoked: 4 });
  const { live, revoked } = ordered([old, gone, fresh]);
  assert.deepEqual(live.map((t) => t.id), ["b", "a"]);
  assert.deepEqual(revoked.map((t) => t.id), ["c"]);
});

test("the count line counts only live tokens and says what happens with none", () => {
  assert.match(countLine([]), /No token is in use/);
  assert.match(countLine([token({ revoked: 5 })]), /No token is in use/);
  assert.equal(countLine([token()]), "1 token is in use.");
  assert.equal(countLine([token({ id: "a" }), token({ id: "b" })]), "2 tokens are in use.");
});

test("revoking says it is at once and cannot be undone", () => {
  const message = revokeMessage(token());
  assert.match(message, /hallway screen/);
  assert.match(message, /next request/);
  assert.match(message, /cannot be undone/);
});

test("what happened is said with the label and the level", () => {
  assert.equal(madeLine(token({ level: "writer" })), "The writer token “hallway screen” was made.");
  assert.equal(revokedLine(token()), "The token “hallway screen” was revoked. It no longer works.");
});
