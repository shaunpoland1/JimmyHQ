import assert from "node:assert/strict";
import { test } from "node:test";
import type { DescribedRecord, TableSpec } from "../src/modules/apps/described/api.ts";
import { changesOnly, controlFor, formFor, readField, readForm } from "../src/modules/apps/described/lib/form.ts";

const TABLE: TableSpec = {
  fields: {
    title: { type: "text", label: "Title", required: true },
    notes: { type: "text" },
    stars: { type: "int", label: "Stars", min: 1, max: 5 },
    weight: { type: "number", min: 0 },
    read: { type: "bool" },
    finished: { type: "date" },
    status: { type: "choice", options: ["to read", "done"] },
    paid: { type: "money", currency: "USD", min: 0 },
    author: { type: "link", table: "author" },
    per_page: { type: "computed", formula: "paid / 2" },
  },
};
const RECORD: DescribedRecord = { id: 4, label: "Dune", created: 1, updated: 2, values: { title: "Dune", notes: null, stars: 5, weight: null, read: true, finished: "2030-01-02", status: "done", paid: 1250, author: 3, per_page: 625 } };

test("each field gets the control that fits its type, and the first text field is a single line", () => {
  const kinds = Object.fromEntries(Object.keys(TABLE.fields).map((n) => [n, controlFor(TABLE, n)]));
  assert.deepEqual(kinds, { title: "text", notes: "textarea", stars: "int", weight: "number", read: "checkbox", finished: "date", status: "select", paid: "money", author: "link", per_page: "text" });
});

test("a form starts empty for a new record, with any preset, and full for an existing one", () => {
  const blank = formFor(TABLE, undefined, { finished: "2030-05-06" }, "en-US");
  assert.equal(blank.title, "");
  assert.equal(blank.finished, "2030-05-06");
  assert.equal(blank.read, false);
  assert.ok(!("per_page" in blank), "a computed field has no control");
  const full = formFor(TABLE, RECORD, {}, "en-US");
  assert.deepEqual([full.stars, full.paid, full.author, full.read], ["5", "12.50", "3", true]);
});

test("a whole number must be whole and inside its range, with a sentence naming the field", () => {
  const stars = TABLE.fields.stars;
  assert.ok(stars);
  assert.deepEqual(readField("stars", stars, "4"), { value: 4 });
  assert.deepEqual(readField("stars", stars, ""), { value: null });
  for (const bad of ["4.5", "9", "0", "abc", "1e2", "--1"]) {
    const read = readField("stars", stars, bad);
    assert.ok("error" in read && read.error.startsWith("Stars must be a whole number from 1 to 5"), bad);
  }
});

test("a number takes a decimal point or comma and refuses the rest", () => {
  const weight = TABLE.fields.weight;
  assert.ok(weight);
  assert.deepEqual(readField("weight", weight, "2.5"), { value: 2.5 });
  assert.deepEqual(readField("weight", weight, "2,5"), { value: 2.5 });
  assert.ok("error" in readField("weight", weight, "-1"));
  assert.ok("error" in readField("weight", weight, "NaN"));
});

test("a date must be a real day written year-month-day", () => {
  const spec = TABLE.fields.finished;
  assert.ok(spec);
  assert.deepEqual(readField("finished", spec, "2030-02-28"), { value: "2030-02-28" });
  for (const bad of ["2030-02-30", "2030-2-3", "tomorrow", "31/01/2030"]) assert.ok("error" in readField("finished", spec, bad), bad);
});

test("a choice must be one of its options, and a required field must be filled", () => {
  const status = TABLE.fields.status;
  const title = TABLE.fields.title;
  assert.ok(status && title);
  assert.deepEqual(readField("status", status, "done"), { value: "done" });
  assert.ok("error" in readField("status", status, "lost"));
  const missing = readField("title", title, "   ");
  assert.deepEqual(missing, { error: "Title is needed. Fill it in." });
});

test("money is read into whole minor units and a negative amount is refused where the field forbids it", () => {
  const paid = TABLE.fields.paid;
  assert.ok(paid);
  assert.deepEqual(readField("paid", paid, "12.50", "en-US"), { value: 1250 });
  const bad = readField("paid", paid, "-1", "en-US");
  assert.ok("error" in bad && bad.error.includes("not below zero"));
  assert.ok("error" in readField("paid", paid, "12.505", "en-US"));
});

test("a link is picked from a list, so only a whole id is accepted", () => {
  const author = TABLE.fields.author;
  assert.ok(author);
  assert.deepEqual(readField("author", author, "3"), { value: 3 });
  assert.ok("error" in readField("author", author, "https://example.com"));
});

test("reading a whole form gives the values to send and one sentence for each field it could not read", () => {
  const state = { ...formFor(TABLE), title: "", stars: "9", status: "done" };
  const read = readForm(TABLE, state, "en-US");
  assert.deepEqual(Object.keys(read.errors).sort(), ["stars", "title"]);
  assert.equal(read.values.status, "done");
  assert.equal(readForm(TABLE, { ...state, title: "A", stars: "2" }, "en-US").errors.stars, undefined);
});

test("an edit sends only the fields that changed", () => {
  const same = readForm(TABLE, formFor(TABLE, RECORD, {}, "en-US"), "en-US").values;
  assert.deepEqual(changesOnly(RECORD, same), {});
  assert.deepEqual(changesOnly(RECORD, { ...same, stars: 3, notes: "x" }), { stars: 3, notes: "x" });
  assert.deepEqual(changesOnly(RECORD, { ...same, finished: null }), { finished: null });
});
