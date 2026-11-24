import assert from "node:assert/strict";
import { test } from "node:test";
import { nextAfterLeaving } from "../src/modules/chat/lib/leave.ts";

const item = (id: string, updated: number, archived = false) => ({ id, updated, archived });

test("leaving the open conversation moves to the most recent one that remains", () => {
  assert.equal(nextAfterLeaving([item("a", 30), item("b", 20), item("c", 10)], "a"), "b");
  assert.equal(nextAfterLeaving([item("a", 30), item("b", 20), item("c", 10)], "b"), "a");
});

test("leaving the only conversation leaves nothing open", () => {
  assert.equal(nextAfterLeaving([item("a", 30)], "a"), null);
  assert.equal(nextAfterLeaving([], "a"), null);
});

test("an archived conversation is never the one moved to", () => {
  assert.equal(nextAfterLeaving([item("a", 30), item("b", 20, true)], "a"), null);
});

test("the choice does not depend on the order the list is given in", () => {
  assert.equal(nextAfterLeaving([item("c", 10), item("b", 20), item("a", 30)], "a"), "b");
});
