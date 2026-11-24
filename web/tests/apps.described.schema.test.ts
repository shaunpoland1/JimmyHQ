import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_CELL, MAX_LABEL, cleanText, editableFields, fieldLabel, humanise, readDescription, recordNoun, viewFields } from "../src/modules/apps/described/lib/schema.ts";

const RAW = {
  tables: {
    author: { label: "Authors", fields: { name: { type: "text", required: true } } },
    book: {
      fields: {
        title: { type: "text", label: "Title", required: true },
        author: { type: "link", table: "author" },
        status: { type: "choice", options: ["to read", "done"] },
        paid: { type: "money", currency: "EUR", min: 0 },
        per_page: { type: "computed", formula: "paid / pages" },
      },
    },
  },
  views: [
    { id: "all", label: "All", table: "book", fields: ["title", "status"], sort: ["-title"], totals: { paid: "sum" }, group: "status" },
    { id: "authors", label: "Authors", table: "author" },
  ],
};

test("a description is read into only the shapes the renderer knows", () => {
  const d = readDescription(RAW);
  assert.deepEqual(Object.keys(d.tables), ["author", "book"]);
  assert.equal(d.tables.book?.fields.paid?.currency, "EUR");
  assert.deepEqual(d.views.map((v) => v.id), ["all", "authors"]);
  assert.equal(d.views[0]?.group, "status");
});

test("anything that is not a description reads as an empty one and never throws", () => {
  for (const raw of [null, undefined, 5, "x", [], { tables: 3 }, { tables: { book: 3 } }, { views: "no" }]) {
    assert.deepEqual(readDescription(raw), { tables: {}, views: [] });
  }
});

test("a field of an unknown type, a table without fields and a bad name are dropped", () => {
  const d = readDescription({
    tables: {
      book: { fields: { title: { type: "text" }, mystery: { type: "html" }, id: { type: "text" }, "Bad Name": { type: "text" } } },
      empty: { fields: {} },
      "<b>": { fields: { a: { type: "text" } } },
    },
  });
  assert.deepEqual(Object.keys(d.tables), ["book"]);
  assert.deepEqual(Object.keys(d.tables.book?.fields ?? {}), ["title"]);
});

test("a link is kept only when it names another table of the same app, never an address", () => {
  const d = readDescription({
    tables: {
      note: { fields: { t: { type: "text" }, away: { type: "link", table: "https://example.com/x" }, gone: { type: "link", table: "nowhere" }, ok: { type: "link", table: "note" } } },
    },
  });
  assert.deepEqual(Object.keys(d.tables.note?.fields ?? {}), ["t", "ok"]);
});

test("a view of a missing table, with a repeated id or naming no real field is trimmed or dropped", () => {
  const d = readDescription({
    ...RAW,
    views: [
      { id: "a", label: "A", table: "nope" },
      { id: "b", label: "B", table: "book", fields: ["nothing"], sort: ["-ghost", "title"], group: "ghost", totals: { ghost: "sum", paid: "total" } },
      { id: "b", label: "B again", table: "book" },
    ],
  });
  assert.equal(d.views.length, 1);
  assert.deepEqual(d.views[0], { id: "b", label: "B", table: "book", sort: ["title"] });
});

test("a label with markup stays text and is cut to its length", () => {
  const evil = '<img src=x onerror="alert(1)">'.repeat(10);
  const d = readDescription({ tables: { book: { label: evil, fields: { title: { type: "text", label: evil } } } }, views: [{ id: "all", label: evil, table: "book" }] });
  for (const label of [d.tables.book?.label, d.tables.book?.fields.title?.label, d.views[0]?.label]) {
    assert.ok(label && label.length <= MAX_LABEL);
    assert.ok(label.startsWith("<img"), "the markup is kept as characters, to be shown as text");
  }
});

test("cleaning text removes control and invisible characters, collapses spaces and caps the length", () => {
  assert.equal(cleanText("  a\u0000b\n\tc  ‮d "), "a b c d");
  assert.equal(cleanText(5), "");
  const long = cleanText("x".repeat(MAX_CELL + 50));
  assert.equal(Array.from(long).length, MAX_CELL);
  assert.ok(long.endsWith("…"));
  assert.equal(cleanText("😀".repeat(10), 5), "😀😀😀😀…");
});

test("a field or table without a label is called by its name, made readable", () => {
  assert.equal(humanise("finished_on"), "Finished on");
  assert.equal(fieldLabel("finished_on", {}), "Finished on");
  assert.equal(fieldLabel("title", { label: "Name" }), "Name");
  assert.equal(recordNoun("book_club"), "book club");
});

test("a view shows the fields it names, or every field of its table, and a form leaves out computed ones", () => {
  const d = readDescription(RAW);
  const all = d.views[0];
  const authors = d.views[1];
  assert.ok(all && authors);
  assert.deepEqual(viewFields(d, all), ["title", "status"]);
  assert.deepEqual(viewFields(d, authors), ["name"]);
  assert.deepEqual(editableFields(d.tables.book ?? { fields: {} }), ["title", "author", "status", "paid"]);
});
