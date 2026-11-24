// How installed apps reach the shell: the sidebar's lines and the `#/app/...` address.

import assert from "node:assert/strict";
import { test } from "node:test";
import { formatHash, parseHash } from "../src/lib/route.ts";
import { NAV_SCREENS, isScreenId } from "../src/lib/screens.ts";
import { affectsInstalled, appSub, findTarget, navItems, parseInstalled } from "../src/modules/apps/lib/installed.ts";

const WATER = { id: "water", name: "Water", version: "1.0.0", tier: "sandboxed", enabled: true, screens: [{ id: "main", label: "Water", described: false }] };
const POSTS = { id: "posts", name: "Posts", version: "0.1.0", tier: "full", enabled: true, screens: [{ id: "board", label: "Board", described: false }, { id: "queue", label: "Queue", described: false }] };
const OFF = { ...WATER, id: "books_read", name: "Books read", enabled: false, screens: [{ id: "main", label: "Books read", described: true }] };

test("an app screen's address is #/app/<id>/<screen> and reads back the same", () => {
  assert.deepEqual(parseHash("#/app/water/main"), { screen: "app", sub: "water/main" });
  assert.equal(formatHash({ screen: "app", sub: appSub("water", "main") }), "#/app/water/main");
  assert.equal(isScreenId("app"), true);
});

test("the app screen is not one of the shell's own sidebar screens", () => {
  assert.equal(NAV_SCREENS.some((screen) => screen.id === "app"), false);
});

test("the server's list is read whether it is wrapped or bare, and a row that is not an app is dropped", () => {
  assert.equal(parseInstalled({ apps: [WATER, POSTS] }).length, 2);
  assert.equal(parseInstalled([WATER]).length, 1);
  for (const odd of [null, undefined, "apps", 7, {}, { apps: "water" }]) assert.deepEqual(parseInstalled(odd), []);
  const rows = parseInstalled([null, "water", { id: "../settings" }, { id: "Water" }, { name: "No id" }, WATER, WATER]);
  assert.deepEqual(rows.map((app) => app.id), ["water"]);
});

test("an app is switched on only when the server plainly says so, and sandboxed unless it plainly says full", () => {
  const [app] = parseInstalled([{ id: "water", enabled: "yes", tier: "root", screens: "none", permissions: [1, "tasks.write", null] }]);
  assert.equal(app?.enabled, false);
  assert.equal(app?.tier, "sandboxed");
  assert.deepEqual(app?.screens, []);
  // What the owner granted is not in this list: the shell learns it when it mounts a screen.
  assert.equal("permissions" in (app ?? {}), false);
  assert.equal(app?.name, "water");
});

test("a screen with an id that could not be part of an address is dropped", () => {
  const [app] = parseInstalled([{ ...WATER, screens: [{ id: "a/b", label: "x" }, { id: "ok", entry: "../../x.html" }, { id: "drawn", described: true }, { id: "odd", described: "yes" }, null] }]);
  // A file's path is never read from this list, and only a plain `true` means JimmyHQ draws the screen.
  assert.deepEqual(app?.screens, [
    { id: "ok", label: "ok", described: false },
    { id: "drawn", label: "drawn", described: true },
    { id: "odd", label: "odd", described: false },
  ]);
});

test("the sidebar lists every screen of each app that is switched on, and none of one that is off", () => {
  const items = navItems(parseInstalled([WATER, OFF, POSTS]));
  assert.deepEqual(items.map((item) => [item.sub, item.label, item.title]), [
    ["water/main", "Water", "Water"],
    ["posts/board", "Board", "Posts: Board"],
    ["posts/queue", "Queue", "Posts: Queue"],
  ]);
});

test("an address opens its screen, or an app's first screen when none is named", () => {
  const apps = parseInstalled([WATER, POSTS, OFF]);
  const exact = findTarget(apps, "posts/queue");
  assert.equal(exact.state === "ok" && exact.screen.id, "queue");
  const first = findTarget(apps, "posts");
  assert.equal(first.state === "ok" && first.screen.id, "board");
});

test("an app that is switched off, missing or without that screen gets a plain state, never a frame", () => {
  const apps = parseInstalled([WATER, POSTS, OFF, { id: "empty", enabled: true }]);
  assert.equal(findTarget(apps, "books_read/main").state, "disabled");
  assert.equal(findTarget(apps, "gone/main").state, "missing");
  assert.equal(findTarget(apps, "water/other").state, "no_screen");
  assert.equal(findTarget(apps, "empty").state, "no_screen");
  for (const sub of [null, "", "Water/main", "../settings", "water/main/extra", "water main"]) assert.equal(findTarget(apps, sub).state, "missing", String(sub));
});

test("an app lifecycle event on the feed makes the list worth asking for again", () => {
  assert.equal(affectsInstalled({ kind: "apps.installed" }), true);
  assert.equal(affectsInstalled({ kind: "apps.disabled" }), true);
  assert.equal(affectsInstalled({ kind: "app.water.logged" }), false);
  assert.equal(affectsInstalled({ kind: "setting.updated" }), false);
});
