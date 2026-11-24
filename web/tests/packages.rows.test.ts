// The Packages page: which packages are on which tab, and in what order.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { counts, emptyWords, forTab, loud, rankOf, registerAgain, tabOf } from "../../apps/packages/web/lib/rows.js";

const row = (id: number, status: string, more: Record<string, unknown> = {}) => ({ id, label: `Parcel ${id}`, status, created: 1000 + id, event_ms: null, delivered_ms: null, ...more });

test("a delivered package is on the Delivered tab and every other one is on the way", () => {
  assert.equal(tabOf(row(1, "delivered")), "delivered");
  for (const status of ["waiting", "on_its_way", "out_for_delivery", "pickup", "problem", "not_found", "expired"]) assert.equal(tabOf(row(1, status)), "way");
});

test("on the way, the ones that need you come first and then the newest news", () => {
  const rows = [row(1, "on_its_way", { event_ms: 5000 }), row(2, "problem"), row(3, "out_for_delivery"), row(4, "on_its_way", { event_ms: 9000 }), row(5, "waiting"), row(6, "delivered")];
  assert.deepEqual(forTab(rows, "way").map((r: { id: number }) => r.id), [2, 3, 4, 1, 5]);
});

test("delivered packages are listed newest delivery first", () => {
  const rows = [row(1, "delivered", { delivered_ms: 100 }), row(2, "delivered", { delivered_ms: 300 }), row(3, "on_its_way")];
  assert.deepEqual(forTab(rows, "delivered").map((r: { id: number }) => r.id), [2, 1]);
});

test("listing a tab leaves the list it was given as it was", () => {
  const rows = [row(1, "waiting"), row(2, "problem")];
  forTab(rows, "way");
  assert.deepEqual(rows.map((r) => r.id), [1, 2]);
});

test("each tab says how many packages it holds", () => {
  assert.deepEqual(counts([row(1, "waiting"), row(2, "delivered"), row(3, "problem")]), { way: 2, delivered: 1 });
  assert.deepEqual(counts([]), { way: 0, delivered: 0 });
});

test("the sidebar counts only packages out for delivery or with a problem", () => {
  assert.equal(loud([row(1, "waiting"), row(2, "out_for_delivery"), row(3, "problem"), row(4, "delivered"), row(5, "pickup")]), 2);
  assert.equal(loud(undefined), 0);
});

test("a status the page does not know sorts last instead of breaking the list", () => {
  assert.equal(rankOf("teleported"), 99);
  assert.ok(rankOf("problem") < rankOf("on_its_way"));
});

test("an empty tab says why in plain words", () => {
  assert.match(emptyWords("way", false), /No packages yet/);
  assert.match(emptyWords("way", true), /Nothing is on its way/);
  assert.match(emptyWords("delivered", true), /Nothing has been delivered yet/);
});

test("a number the service forgot offers Register again, saying it uses quota, never as the main button", () => {
  assert.deepEqual(registerAgain(row(1, "waiting", { held: true })), { label: "Register again (uses quota)", kind: "" });
  assert.equal(registerAgain(row(2, "waiting")), null);
});
