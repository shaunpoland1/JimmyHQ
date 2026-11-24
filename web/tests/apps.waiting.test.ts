// The number beside Posts and Articles in the sidebar is said by each app's program as well as
// by its page. The cases are one file for each app, read here against the page's own script and
// in the app's Python tests against the program's count, so the two cannot drift apart unseen.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The pages are plain JavaScript, served to the app's frame as they are.
import { count } from "../../apps/articles/web/lib/today.js";
// @ts-expect-error The pages are plain JavaScript, served to the app's frame as they are.
import { needs, waiting } from "../../apps/posts/web/lib/today.js";

type Case = { name: string; data: Record<string, unknown>; waiting: number };

function cases(app: string): Case[] {
  return JSON.parse(readFileSync(new URL(`../../apps/${app}/tests/fixtures/waiting_cases.json`, import.meta.url), "utf8"));
}

for (const one of cases("posts")) {
  test(`Posts' Today adds up to what its program says: ${one.name}`, () => {
    assert.equal(waiting(needs(one.data)), one.waiting);
  });
}

for (const one of cases("articles")) {
  test(`Articles' Today adds up to what its program says: ${one.name}`, () => {
    assert.equal(count(one.data), one.waiting);
  });
}
