// The page's own logic, run by node (no browser): saving a file whole, and the words.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const lib = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "web", "lib");
const saving = await import(pathToFileURL(path.join(lib, "saving.js")).href);
const util = await import(pathToFileURL(path.join(lib, "util.js")).href);

const refusal = (code) => Object.assign(new Error("refused"), { code });
const file = (text) => ({ name: "books-everything", type: "application/json", text });

/** A stand-in for hq.ui.save with the owner's limit in characters. */
function saver(limit, calls = []) {
  return async (f) => {
    calls.push(f.text.length);
    if (f.text.length > limit) throw refusal("too_large");
    return { name: `${f.name}.json`, bytes: f.text.length };
  };
}

const big = (rows) => JSON.stringify({ rows: Array.from({ length: rows }, (_, i) => ({ id: i, payee: `Shop ${i}`, memo: "an invented row for a size test", cents: -i, state: "c" })) }, null, 1);

test("a ten thousand row file is saved whole when the limit allows it, with no second try", async () => {
  const text = big(10_000);
  assert.ok(text.length > 1_000_000);
  const calls = [];
  const done = await saving.saveWhole(file(text), saver(8_000_000, calls));
  assert.equal(done.packed, false);
  assert.equal(done.saved.name, "books-everything.json");
  assert.deepEqual(calls, [text.length]);
});

test("a file over the limit is tried again packed onto one line, and saved if that fits", async () => {
  const text = big(10_000);
  const smaller = JSON.stringify(JSON.parse(text));
  const calls = [];
  const done = await saving.saveWhole(file(text), saver(smaller.length + 10, calls));
  assert.equal(done.packed, true);
  assert.deepEqual(calls, [text.length, smaller.length]);
});

test("a file over the limit even when packed is not saved and the answer says so", async () => {
  const done = await saving.saveWhole(file(big(10_000)), saver(1000));
  assert.deepEqual(done, { tooLarge: true });
});

test("a CSV file is never repacked, and over the limit it is not saved", async () => {
  const calls = [];
  const done = await saving.saveWhole({ name: "x", type: "text/csv", text: "a,b\n".repeat(500) }, saver(100, calls));
  assert.deepEqual(done, { tooLarge: true });
  assert.equal(calls.length, 1);
});

test("a refusal that is not about size is passed on, not taken for a limit", async () => {
  const save = async () => { throw refusal("not_allowed"); };
  await assert.rejects(saving.saveWhole(file("{}"), save), { code: "not_allowed" });
  await assert.rejects(saving.saveWhole(file(big(5)), async () => { throw new Error("plain"); }), { message: "plain" });
});

test("packing leaves text that is not JSON alone", () => {
  assert.equal(saving.packed(file("not json {")), null);
  assert.equal(saving.packed(file('{"a":1}')), null);  // already as small as it goes
  assert.equal(saving.packed({ type: "text/csv", text: "a" }), null);
});

test("the sentence for a file that is too large names the setting and what else to do", () => {
  const words = saving.tooLargeWords(12_345_678, "json");
  assert.match(words, /12\.3 MB/);
  assert.match(words, /Largest file an app may offer you to keep \(MB\)/);
  assert.match(words, /Settings, Apps/);
  assert.match(words, /save each account as CSV instead/);
  assert.match(words, /Nothing was saved\.$/);
  assert.match(saving.tooLargeWords(900_000, "csv"), /900 KB.*save one account at a time/);
});

test("a refusal is shown as the program wrote it, with no lead-in taken off", () => {
  assert.equal(util.reason({ message: "The app could not do that: it is gone." }), "The app could not do that: it is gone.");
  assert.equal(util.reason({ owner_message: "Say this", message: "not this" }), "Say this");
  assert.equal(util.reason(new Error("Nothing was changed.")), "Nothing was changed.");
  assert.match(util.reason(null), /nothing was changed/);
});

test("a new bill starts on the account the last bill used, else the first bank account, never the card added last", () => {
  const accounts = [
    { id: 1, name: "Everyday", kind: "checking" },
    { id: 2, name: "Rainy day", kind: "savings" },
    { id: 3, name: "Store card", kind: "credit_card" },
  ];
  assert.equal(util.billAccount(accounts, []).id, 1);
  assert.equal(util.billAccount([accounts[2], accounts[1]], []).id, 2);
  const reminders = [
    { id: 4, kind: "bill", account: 2, paid_other: false },
    { id: 7, kind: "bill", account: 3, paid_other: false },
    { id: 9, kind: "income", account: 1, paid_other: false },
    { id: 11, kind: "bill", account: null, paid_other: true },
  ];
  assert.equal(util.billAccount(accounts, reminders).id, 3);
  assert.equal(util.billAccount(accounts.slice(0, 2), reminders).id, 2);
  assert.equal(util.billAccount([], reminders), null);
  assert.equal(util.billAccount([{ id: 3, kind: "credit_card" }], []).id, 3);
});
