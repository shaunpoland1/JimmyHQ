// The number beside an app in the sidebar: the program's own (`ui.count`, carried by the list of
// installed apps) and the page's (`ui.badge`), and which one shows when.

import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_REFRESH_MS, parseInstalled, refreshMs, serverCount, shownBadge } from "../src/modules/apps/lib/installed.ts";

const POSTS = { id: "posts", name: "Posts", version: "0.3.1", tier: "full", enabled: true, screens: [{ id: "posts", label: "Posts", described: false }] };

test("the list carries each app's count, and an app that has not said has none", () => {
  const rows = parseInstalled({ apps: [{ ...POSTS, count: 4 }, { ...POSTS, id: "reader", count: null }, { ...POSTS, id: "water" }] });
  assert.deepEqual(rows.map((app) => app.count), [4, null, null]);
});

test("a count that is not a whole number from 0 up is no count, and one above what a badge shows is held to it", () => {
  for (const odd of [-1, "3", true, null, undefined, Number.NaN, Infinity, [3], { n: 3 }]) assert.equal(serverCount(odd), null, String(odd));
  assert.equal(serverCount(0), 0);
  assert.equal(serverCount(7.9), 7);
  assert.equal(serverCount(999), 99);
});

test("the list is read again as often as the server says, held between ten seconds and an hour, else every minute", () => {
  assert.equal(refreshMs({ apps: [], refresh_s: 120 }), 120_000);
  assert.equal(refreshMs({ refresh_s: 1 }), 10_000);
  assert.equal(refreshMs({ refresh_s: 999_999 }), 3_600_000);
  for (const odd of [null, undefined, [], {}, { refresh_s: "60" }, { refresh_s: 0 }, { refresh_s: -5 }, { refresh_s: Number.NaN }]) {
    assert.equal(refreshMs(odd), DEFAULT_REFRESH_MS, String(odd));
  }
});

test("while an app's page is open its own number wins, even a zero that clears the program's", () => {
  assert.equal(shownBadge({ count: 2, open: true }, 5), 2);
  assert.equal(shownBadge({ count: 0, open: true }, 5), 0);
});

test("once the page is closed the program's number shows, and an app with none keeps what its page last said", () => {
  assert.equal(shownBadge({ count: 2, open: false }, 5), 5);
  assert.equal(shownBadge({ count: 2, open: false }, 0), 0);
  assert.equal(shownBadge({ count: 2, open: false }, null), 2);
});

test("the program's number shows with no page opened at all, and an app with neither shows none", () => {
  assert.equal(shownBadge(undefined, 3), 3);
  assert.equal(shownBadge(undefined, null), undefined);
});
