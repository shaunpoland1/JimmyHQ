import assert from "node:assert/strict";
import { test } from "node:test";
import { parseHash } from "../src/lib/route.ts";
import { APP_ICON_NAMES, appIconName, findTarget, navItems, parseInstalled } from "../src/modules/apps/lib/installed.ts";
import { APPS_SETTINGS_HASH, appIdFromSub, openAppId, detailHash, screenHash, settingsHash } from "../src/modules/apps/lib/route.ts";
import { cardAppLink } from "../src/modules/apps/lib/cardLink.ts";

test("an app's detail view is found from the address and a stray path is not taken for one", () => {
  assert.equal(appIdFromSub("water"), "water");
  assert.equal(appIdFromSub("books_read/extra"), "books_read");
  assert.equal(appIdFromSub(null), null);
  assert.equal(appIdFromSub(""), null);
  assert.equal(appIdFromSub("../etc"), null);
  assert.equal(appIdFromSub("Water"), null);
});

test("an app's detail address is its id", () => {
  assert.equal(detailHash("water"), "#/apps/water");
});

test("an app's settings link goes to the section the server names, or to its own by default", () => {
  assert.equal(settingsHash({ id: "water" }), "#/settings/app.water");
  assert.equal(settingsHash({ id: "water", settings_section: "app_water" }), "#/settings/app_water");
  assert.equal(APPS_SETTINGS_HASH, "#/settings/apps");
});

test("an app's screen address is the one form the shell routes, #/app/<id>/<screen>", () => {
  assert.equal(screenHash("water", "main"), "#/app/water/main");
  const route = parseHash(screenHash("books_read", "main"));
  assert.deepEqual(route, { screen: "app", sub: "books_read/main" });
  const target = findTarget(parseInstalled({ apps: [{ id: "books_read", name: "Books read", enabled: true, screens: [{ id: "main", label: "Books read", described: true }] }] }), route.sub);
  assert.equal(target.state, "ok");
});

test("opening Settings over the Apps screen does not change which app is open", () => {
  assert.equal(openAppId(parseHash("#/apps/water"), null), "water");
  assert.equal(openAppId(parseHash("#/settings/file"), null), null);
  assert.equal(openAppId(parseHash("#/settings/lookup/lookup.enabled"), "water"), "water");
  assert.equal(openAppId(parseHash("#/apps"), "water"), null);
});

test("a card an installed app filed gets an Open button that goes to the app's first screen", () => {
  const apps = parseInstalled({
    apps: [
      { id: "books", name: "Books", enabled: true, screens: [{ id: "main", label: "Books" }, { id: "bills", label: "Bills" }] },
      { id: "quiet", name: "Quiet", enabled: false, screens: [{ id: "main", label: "Quiet" }] },
      { id: "bare", name: "Bare", enabled: true, screens: [] },
    ],
  });
  assert.deepEqual(cardAppLink("app:books", apps), { label: "Open Books", href: "#/app/books/main" });
  assert.equal(cardAppLink("app:quiet", apps), null);
  assert.equal(cardAppLink("app:bare", apps), null);
  assert.equal(cardAppLink("app:missing", apps), null);
  assert.equal(cardAppLink("owner", apps), null);
  assert.equal(cardAppLink("agent:wren", apps), null);
});

test("an app's sidebar line carries the picture it named, and a word the shell does not draw is no picture", () => {
  const rows = [
    { id: "boxes", name: "Boxes", version: "1", tier: "full", enabled: true, icon: "package", screens: [{ id: "main", label: "Boxes" }] },
    { id: "odd", name: "Odd", version: "1", tier: "full", enabled: true, icon: "<svg onload=alert(1)>", screens: [{ id: "main", label: "Odd" }] },
    { id: "plain", name: "Plain", version: "1", tier: "full", enabled: true, screens: [{ id: "main", label: "Plain" }] },
  ];
  assert.deepEqual(navItems(parseInstalled(rows)).map((item) => item.icon), ["package", null, null]);
  assert.equal(appIconName("package"), "package");
  assert.equal(appIconName("https://example.com/x.png"), null);
  assert.equal(appIconName(7), null);
});

test("every picture an app may name is one of a small fixed set", () => {
  assert.ok(APP_ICON_NAMES.length <= 20);
  for (const name of APP_ICON_NAMES) assert.match(name, /^[a-z]+$/);
});
