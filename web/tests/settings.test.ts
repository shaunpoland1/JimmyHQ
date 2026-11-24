import assert from "node:assert/strict";
import { test } from "node:test";
import { addItem, coerceSettingValue, describeValue, dropIndex, isDefaultValue, moveAnnouncement, moveItem, removeItem, valuesEqual } from "../src/lib/settings.ts";

test("int coercion enforces whole numbers and the range", () => {
  const shape = { type: "int", min: 1, max: 10 } as const;
  assert.deepEqual(coerceSettingValue(shape, "7"), { ok: true, value: 7 });
  assert.deepEqual(coerceSettingValue(shape, 3), { ok: true, value: 3 });
  assert.equal(coerceSettingValue(shape, "2.5").ok, false);
  assert.deepEqual(coerceSettingValue(shape, "0"), { ok: false, error: "Must be at least 1." });
  assert.deepEqual(coerceSettingValue(shape, "11"), { ok: false, error: "Must be at most 10." });
  assert.equal(coerceSettingValue(shape, "").ok, false);
  assert.equal(coerceSettingValue(shape, "abc").ok, false);
});

test("float coercion allows fractions", () => {
  assert.deepEqual(coerceSettingValue({ type: "float", min: 0, max: 2 }, "0.75"), { ok: true, value: 0.75 });
  assert.equal(coerceSettingValue({ type: "float", min: 0, max: 2 }, "2.1").ok, false);
  assert.deepEqual(coerceSettingValue({ type: "float" }, "-4"), { ok: true, value: -4 });
});

test("choice coercion returns the schema's own value", () => {
  const shape = { type: "choice" as const, choices: [{ value: 1, label: "One" }, { value: "b", label: "B" }] };
  assert.deepEqual(coerceSettingValue(shape, "1"), { ok: true, value: 1 });
  assert.deepEqual(coerceSettingValue(shape, "b"), { ok: true, value: "b" });
  assert.equal(coerceSettingValue(shape, "c").ok, false);
});

test("bool, str and list coercion", () => {
  assert.deepEqual(coerceSettingValue({ type: "bool" }, true), { ok: true, value: true });
  assert.deepEqual(coerceSettingValue({ type: "bool" }, "false"), { ok: true, value: false });
  assert.equal(coerceSettingValue({ type: "bool" }, "yes").ok, false);
  assert.deepEqual(coerceSettingValue({ type: "str" }, " keep spaces "), { ok: true, value: " keep spaces " });
  assert.equal(coerceSettingValue({ type: "str" }, 5).ok, false);
  assert.deepEqual(coerceSettingValue({ type: "list" }, [" a ", "", "b"]), { ok: true, value: ["a", "b"] });
  assert.equal(coerceSettingValue({ type: "list" }, "a").ok, false);
});

test("valuesEqual and isDefaultValue compare lists by content", () => {
  assert.ok(valuesEqual(["a", "b"], ["a", "b"]));
  assert.ok(!valuesEqual(["a", "b"], ["b", "a"]));
  assert.ok(!valuesEqual(1, "1"));
  assert.ok(isDefaultValue({ value: ["x"], default: ["x"] }));
  assert.ok(!isDefaultValue({ value: true, default: false }));
});

test("describeValue is readable for each type", () => {
  assert.equal(describeValue({ type: "bool" }, true), "On");
  assert.equal(describeValue({ type: "choice", choices: [{ value: "a", label: "Alpha" }] }, "a"), "Alpha");
  assert.equal(describeValue({ type: "list" }, []), "empty");
  assert.equal(describeValue({ type: "list" }, ["a", "b"]), "a, b");
  assert.equal(describeValue({ type: "str" }, ""), "empty");
  assert.equal(describeValue({ type: "int" }, 0), "0");
});

test("list helpers do not mutate their input", () => {
  const items = ["a", "b", "c"];
  assert.deepEqual(moveItem(items, 0, 2), ["b", "c", "a"]);
  assert.deepEqual(moveItem(items, 2, 1), ["a", "c", "b"]);
  assert.deepEqual(moveItem(items, 0, 5), items);
  assert.deepEqual(removeItem(items, 1), ["a", "c"]);
  assert.deepEqual(addItem(items, "  d "), ["a", "b", "c", "d"]);
  assert.deepEqual(addItem(items, "a"), items);
  assert.deepEqual(addItem(items, "   "), items);
  assert.deepEqual(items, ["a", "b", "c"]);
});

test("dropping an item above or below another says where it lands", () => {
  // a b c d: drag a (0)
  assert.equal(dropIndex(4, 0, 2, "before"), 1);
  assert.equal(dropIndex(4, 0, 2, "after"), 2);
  assert.equal(dropIndex(4, 0, 3, "after"), 3);
  // drag d (3) upward
  assert.equal(dropIndex(4, 3, 0, "before"), 0);
  assert.equal(dropIndex(4, 3, 1, "after"), 2);
  assert.deepEqual(moveItem(["a", "b", "c", "d"], 0, dropIndex(4, 0, 2, "after") ?? 0), ["b", "c", "a", "d"]);
  assert.deepEqual(moveItem(["a", "b", "c", "d"], 3, dropIndex(4, 3, 1, "before") ?? 3), ["a", "d", "b", "c"]);
});

test("a drop that would leave the list as it is counts as no move", () => {
  assert.equal(dropIndex(4, 1, 1, "before"), null);
  assert.equal(dropIndex(4, 1, 2, "before"), null);
  assert.equal(dropIndex(4, 1, 0, "after"), null);
  assert.equal(dropIndex(4, 5, 0, "before"), null);
  assert.equal(dropIndex(4, 0, -1, "after"), null);
});

test("a move is announced with the new position", () => {
  assert.equal(moveAnnouncement("Tasks", 1, 6), "Tasks moved to position 2 of 6.");
});
