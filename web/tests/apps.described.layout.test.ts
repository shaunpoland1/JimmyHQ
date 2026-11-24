import assert from "node:assert/strict";
import { test } from "node:test";
import type { DescribedRecord, Description } from "../src/modules/apps/described/api.ts";
import { boardField, calendarField, cardFields, chooseLayout, defaultLayout, groupRecords, layoutsFor, moveValues, titleField } from "../src/modules/apps/described/lib/layout.ts";
import { readDescription } from "../src/modules/apps/described/lib/schema.ts";
import { ariaSort, directionOf, effectiveSort, nextSort } from "../src/modules/apps/described/lib/sort.ts";

const D: Description = readDescription({
  tables: {
    chore: {
      fields: {
        title: { type: "text", required: true },
        state: { type: "choice", options: ["to do", "doing", "done"] },
        due: { type: "date" },
        minutes: { type: "int" },
        repeats: { type: "bool" },
      },
    },
  },
  views: [
    { id: "board", label: "Board", table: "chore", group: "state", fields: ["title", "due", "minutes"], sort: ["due"] },
    { id: "list", label: "List", table: "chore", fields: ["title", "minutes"] },
    { id: "bools", label: "By repeat", table: "chore", group: "repeats" },
  ],
});
const [BOARD, LIST, BOOLS] = D.views;
if (!BOARD || !LIST || !BOOLS) throw new Error("fixture");
const table = D.tables.chore;
if (!table) throw new Error("fixture");

function rec(id: number, state: string | null, extra: Record<string, string | number | boolean | null> = {}): DescribedRecord {
  return { id, label: `Chore ${id}`, created: id, updated: id, values: { title: `Chore ${id}`, state, ...extra } };
}

test("a view offers a board only when it groups by a choice field, and a calendar only with a date field", () => {
  assert.deepEqual(layoutsFor(D, BOARD), ["table", "board", "calendar"]);
  assert.deepEqual(layoutsFor(D, LIST), ["table", "calendar"]);
  assert.equal(boardField(D, BOOLS), null);
  assert.equal(boardField(D, BOARD), "state");
  assert.equal(calendarField(D, BOARD), "due");
});

test("a view that groups by a choice opens as a board and any other opens as a table", () => {
  assert.equal(defaultLayout(D, BOARD), "board");
  assert.equal(defaultLayout(D, LIST), "table");
  assert.equal(defaultLayout(D, BOOLS), "table");
});

test("a remembered layout is used only while the view still offers it", () => {
  assert.equal(chooseLayout(D, BOARD, "table"), "table");
  assert.equal(chooseLayout(D, LIST, "board"), "table");
  assert.equal(chooseLayout(D, BOARD, "<script>"), "board");
  assert.equal(chooseLayout(D, BOARD, undefined), "board");
});

test("a choice's records fall into one column per option in the order of its options, empty ones included", () => {
  const columns = groupRecords(table, "state", [rec(1, "done"), rec(2, "to do"), rec(3, "done")]);
  assert.deepEqual(columns.map((c) => [c.label, c.records.map((r) => r.id), c.count]), [
    ["to do", [2], 1],
    ["doing", [], 0],
    ["done", [1, 3], 2],
  ]);
});

test("records with no choice, or one the app no longer lists, go in a last column of their own", () => {
  const columns = groupRecords(table, "state", [rec(1, null), rec(2, "archived"), rec(3, "doing")]);
  assert.deepEqual(columns.map((c) => c.label), ["to do", "doing", "done", "No state"]);
  assert.deepEqual(columns[3]?.records.map((r) => r.id), [1, 2]);
});

test("a column's count is the server's when it gave one, since the page loaded may hold fewer", () => {
  const columns = groupRecords(table, "state", [rec(1, "doing")], [
    { value: "to do", count: 7 },
    { value: "doing", count: 12, totals: { minutes: 90 } },
    { value: "done", count: 0 },
  ]);
  assert.deepEqual(columns.map((c) => c.count), [7, 12, 0]);
  assert.deepEqual(columns[1]?.totals, { minutes: 90 });
});

test("grouping by a field that is not a choice follows the server's order, then the order met", () => {
  const records = [rec(1, null, { minutes: 20 }), rec(2, null, { minutes: 5 }), rec(3, null, { minutes: 20 })];
  const columns = groupRecords(table, "minutes", records, [{ value: 5, count: 1 }, { value: 20, count: 2 }]);
  assert.deepEqual(columns.map((c) => [c.value, c.records.length]), [[5, 1], [20, 2]]);
});

test("moving a card sets only the group field", () => {
  assert.deepEqual(moveValues("state", "done"), { state: "done" });
  assert.deepEqual(moveValues("state", null), { state: null });
});

test("a card shows a few of the view's fields, never the title or the grouping field", () => {
  assert.deepEqual(cardFields(D, BOARD, ["state"]), ["due", "minutes"]);
  assert.deepEqual(cardFields(D, BOARD, ["state"], 1), ["due"]);
  assert.equal(titleField(D, BOARD), "title");
});

test("pressing a heading sorts forwards, then backwards, then goes back to the view's own order", () => {
  const declared = ["due"];
  let chosen: string[] | null = null;
  assert.equal(directionOf(effectiveSort(chosen, declared), "due"), "asc");
  chosen = nextSort(chosen, declared, "due");
  assert.deepEqual(chosen, ["-due"]);
  chosen = nextSort(chosen, declared, "due");
  assert.equal(chosen, null);
  assert.deepEqual(nextSort(chosen, declared, "title"), ["title"]);
  assert.deepEqual(nextSort(["title"], declared, "title"), ["-title"]);
});

test("a heading says which way it is sorted to assistive technology", () => {
  assert.equal(ariaSort("asc"), "ascending");
  assert.equal(ariaSort("desc"), "descending");
  assert.equal(ariaSort(directionOf(["due"], "title")), "none");
});
