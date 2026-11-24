import assert from "node:assert/strict";
import { test } from "node:test";
import { describeFiler } from "../src/modules/bench/lib/format.ts";
import { plainActor, plainKind, titleWords } from "../src/lib/plainWords.ts";

test("an app's card kind is a plain label, with the app's name added only where it is not shown already", () => {
  assert.equal(plainKind("app.books.due"), "Due");
  assert.equal(plainKind("app.books.due", true), "Books: due");
  assert.equal(plainKind("app.books_read.new_entry", true), "Books read: new entry");
  assert.equal(plainKind("task.created"), "Task created");
  assert.equal(plainKind("app.broken"), "App broken");
});

test("who filed a card reads as a name, never as an internal id", () => {
  assert.equal(plainActor("app:books", "Books"), "Books");
  assert.equal(plainActor("app:books_read"), "Books read");
  assert.equal(plainActor("owner"), "You");
  assert.equal(plainActor("token:hub"), "hub");
  assert.equal(titleWords("a_b-c"), "A b c");
  const filer = describeFiler("app:books", undefined, "Books");
  assert.equal(filer.name, "Books");
  assert.equal(filer.letter, "B");
  assert.equal(describeFiler("app:books").name, "Books");
});
