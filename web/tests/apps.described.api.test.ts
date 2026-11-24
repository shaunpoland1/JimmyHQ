import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "../src/api.ts";
import { describedApi, unwrap } from "../src/modules/apps/described/api.ts";
import type { ListResult, Transport } from "../src/modules/apps/described/api.ts";
import { joinPages } from "../src/modules/apps/described/lib/pages.ts";

test("the bridge's answer is its envelope: a result is handed on and a refusal is thrown with its sentence", () => {
  assert.deepEqual(unwrap({ id: null, ok: true, result: { total: 2 } }), { total: 2 });
  assert.equal(unwrap({ ok: true, result: null }), null);
  const refusal = { id: null, ok: false, error: { code: "invalid", message: "\"Stars\" must be a whole number from 1 to 5, not 9. Nothing was saved." } };
  assert.throws(() => unwrap(refusal), (error: unknown) => error instanceof ApiError && error.status === 422 && /Nothing was saved/.test(error.detail));
  assert.throws(() => unwrap({ ok: false, error: { code: "quota", message: "Too many.", retry_after_s: 5 } }), (error: unknown) => error instanceof ApiError && error.status === 429);
  // Anything that is not the envelope is a fault, never taken for a result.
  for (const odd of [null, { total: 1 }, "ok", { ok: "yes", result: 1 }]) assert.throws(() => unwrap(odd), ApiError);
});

test("each call goes to the app's records call with its table and arguments", async () => {
  const sent: [string, string, Record<string, unknown>][] = [];
  const transport: Transport = async (app, call, args) => {
    sent.push([app, call, args]);
    return {};
  };
  const api = describedApi("books_read", transport);
  await api.list({ table: "book", view: "all", limit: 10 });
  await api.get("book", 3);
  await api.create("book", { title: "A" });
  await api.update("book", 3, { stars: 2 });
  await api.remove("book", 3, true);
  assert.deepEqual(sent.map((s) => s[1]), ["records.list", "records.get", "records.create", "records.update", "records.delete"]);
  assert.ok(sent.every((s) => s[0] === "books_read"));
  assert.deepEqual(sent[4]?.[2], { table: "book", id: 3, unlink: true });
});

test("a refusal from the server reaches the caller with its sentence", async () => {
  const api = describedApi("x", async () => {
    throw new Error("\"Stars\" must be a whole number from 1 to 5, not 9. Nothing was saved.");
  });
  await assert.rejects(api.create("book", { stars: 9 }), /Nothing was saved/);
});

test("pages are joined in order with their link labels, and the totals are the latest page's", () => {
  const page = (ids: number[], next: string | null, labels: ListResult["labels"], totals: ListResult["totals"]): ListResult => ({
    records: ids.map((id) => ({ id, label: `R${id}`, values: {}, created: 0, updated: 0 })),
    total: 5,
    labels,
    next,
    totals,
  });
  const joined = joinPages([page([1, 2], "a", { author: { "1": "A" } }, { n: 1 }), page([3], null, { author: { "2": "B" } }, { n: 2 })]);
  assert.deepEqual(joined.records.map((r) => r.id), [1, 2, 3]);
  assert.deepEqual(joined.labels, { author: { "1": "A", "2": "B" } });
  assert.deepEqual(joined.totals, { n: 2 });
  assert.equal(joined.hasMore, false);
  assert.equal(joinPages([page([1], "a", {}, undefined)]).hasMore, true);
});
