import assert from "node:assert/strict";
import { test } from "node:test";
import { blockHeadingId, buildNav, navPages, NO_LAYOUT, onThisPage, pageBlocks, resolveTarget, settingRowId, settingsHash } from "../src/lib/settingsNav.ts";
import { parseHash } from "../src/lib/route.ts";

const sections = [
  { id: "profile", label: "Profile" },
  { id: "models", label: "Models" },
  { id: "village", label: "Village" },
  { id: "village_sleep", label: "Rest and sleep" },
  { id: "village_web", label: "Web lookup" },
  { id: "gadgets", label: "Gadgets" },
  { id: "file", label: "Import and export" },
];
const place = (group: string, parent: string | null = null, description = "") => ({ group, parent, description });
const response = {
  sections,
  layout: {
    groups: [
      { id: "you", label: "You" },
      { id: "models", label: "Models" },
      { id: "agents", label: "Agents" },
      { id: "more", label: "More" },
      { id: "system", label: "This installation" },
    ],
    sections: {
      profile: place("you", null, "Who you are."),
      models: place("models"),
      village: place("agents"),
      village_sleep: place("agents", "village", "Bedtimes and naps."),
      village_web: place("agents", "village"),
      gadgets: place("more"),
      file: place("system"),
    },
  },
};
const settings = [
  { key: "owner.name", section: "profile" },
  { key: "village.headcount_cap", section: "village" },
  { key: "village.sleep.start_hour", section: "village_sleep" },
  { key: "village.web.pages_per_day", section: "village_web" },
  { key: "statusbar.show", section: "profile" },
];
const nav = () => buildNav(sections, response.layout, [{ id: "models", label: "Models and roles" }]);

test("the list follows the server's groups and order, and a part is not a page of its own", () => {
  const groups = nav();
  assert.deepEqual(
    groups.map((group) => [group.label, group.pages.map((page) => page.id)]),
    [
      ["You", ["profile"]],
      ["Models", ["models"]],
      ["Agents", ["village"]],
      ["More", ["gadgets"]],
      ["This installation", ["file"]],
    ],
  );
  const village = navPages(groups).find((page) => page.id === "village");
  assert.deepEqual(village?.parts.map((part) => part.id), ["village_sleep", "village_web"]);
  assert.equal(village?.parts[0]?.description, "Bedtimes and naps.");
  assert.equal(navPages(groups)[0]?.description, "Who you are.");
});

test("a section the web app draws itself keeps its own label", () => {
  assert.equal(navPages(nav()).find((page) => page.id === "models")?.label, "Models and roles");
});

test("a section with no place, or one only the web app knows, is listed under More and never lost", () => {
  const layout = { groups: [{ id: "you", label: "You" }, { id: "system", label: "This installation" }], sections: { profile: place("you"), file: place("system"), odd: place("nowhere") } };
  const groups = buildNav([{ id: "profile", label: "Profile" }, { id: "stray", label: "Stray" }, { id: "odd", label: "Odd" }, { id: "file", label: "File" }], layout, [{ id: "extra", label: "Extra" }]);
  assert.deepEqual(
    groups.map((group) => [group.id, group.pages.map((page) => page.id)]),
    [
      ["you", ["profile"]],
      ["more", ["stray", "odd", "extra"]],
      ["system", ["file"]],
    ],
  );
});

test("a part whose page is missing becomes a page so its settings can still be reached", () => {
  const layout = { groups: [{ id: "agents", label: "Agents" }], sections: { orphan: place("agents", "gone") } };
  const groups = buildNav([{ id: "orphan", label: "Orphan" }], layout);
  assert.deepEqual(navPages(groups).map((page) => page.id), ["orphan"]);
});

test("an older server that sends no layout still gives one plain list", () => {
  const groups = buildNav(sections, NO_LAYOUT);
  assert.equal(groups.length, 1);
  assert.equal(groups[0]?.label, "");
  assert.equal(groups[0]?.pages.length, sections.length);
});

test("a link to a section opens its page, and nothing or nonsense opens the first page", () => {
  const groups = nav();
  assert.deepEqual(resolveTarget("village", groups, settings), { page: "village", part: null, setting: null });
  assert.deepEqual(resolveTarget(null, groups, settings), { page: "profile", part: null, setting: null });
  assert.deepEqual(resolveTarget("nope", groups, settings), { page: "profile", part: null, setting: null });
  assert.equal(resolveTarget("village", [], settings), null);
});

test("an old link to a part of the village lands on that part of the one village page", () => {
  assert.deepEqual(resolveTarget("village_sleep", nav(), settings), { page: "village", part: "village_sleep", setting: null });
});

test("a link to one setting opens its page at that setting", () => {
  const groups = nav();
  assert.deepEqual(resolveTarget("profile/owner.name", groups, settings), { page: "profile", part: null, setting: "owner.name" });
  assert.deepEqual(resolveTarget("village_sleep/village.sleep.start_hour", groups, settings), { page: "village", part: "village_sleep", setting: "village.sleep.start_hour" });
  // Found by its key wherever it lives now, even when the link names another section.
  assert.deepEqual(resolveTarget("village/village.web.pages_per_day", groups, settings), { page: "village", part: "village_web", setting: "village.web.pages_per_day" });
  assert.deepEqual(resolveTarget("owner.name", groups, settings), { page: "profile", part: null, setting: "owner.name" });
  // A setting nobody knows: the section still opens.
  assert.deepEqual(resolveTarget("village/village.nope", groups, settings), { page: "village", part: null, setting: null });
});

test("a link to a section that is gone opens where its settings went", () => {
  assert.deepEqual(resolveTarget("statusbar", nav(), settings), { page: "profile", part: null, setting: null });
});

test("a settings link survives the trip through the address bar", () => {
  const hash = settingsHash("memory", "memory.auto_save_above");
  assert.equal(hash, "#/settings/memory/memory.auto_save_above");
  assert.deepEqual(parseHash(hash), { screen: "settings", sub: "memory/memory.auto_save_above" });
  assert.equal(settingsHash("village_sleep"), "#/settings/village_sleep");
});

test("a page with parts is cut into headed blocks, its own settings first", () => {
  const village = navPages(nav()).find((page) => page.id === "village");
  assert.ok(village);
  const blocks = pageBlocks(village, settings);
  assert.deepEqual(
    blocks.map((block) => [block.id, block.label, block.rows.map((row) => row.key)]),
    [
      ["village", "General", ["village.headcount_cap"]],
      ["village_sleep", "Rest and sleep", ["village.sleep.start_hour"]],
      ["village_web", "Web lookup", ["village.web.pages_per_day"]],
    ],
  );
  assert.deepEqual(onThisPage(blocks), [
    { id: "village", label: "General", count: 1 },
    { id: "village_sleep", label: "Rest and sleep", count: 1 },
    { id: "village_web", label: "Web lookup", count: 1 },
  ]);
});

test("a part with no settings is left out, and a plain page gets no heading and no list", () => {
  const village = navPages(nav()).find((page) => page.id === "village");
  assert.ok(village);
  const some = settings.filter((setting) => setting.section !== "village_web");
  assert.deepEqual(pageBlocks(village, some).map((block) => block.id), ["village", "village_sleep"]);
  const profile = navPages(nav())[0];
  assert.ok(profile);
  const blocks = pageBlocks(profile, settings);
  assert.deepEqual(blocks.map((block) => block.label), [""]);
  assert.deepEqual(onThisPage(blocks), []);
  assert.deepEqual(pageBlocks(profile, settings, "More settings").map((block) => block.label), ["More settings"]);
  assert.deepEqual(pageBlocks(profile, []), []);
});

test("ids made from keys are safe to use in the page", () => {
  assert.equal(settingRowId("app.demo.max size"), "setting-row-app-demo-max-size");
  assert.equal(blockHeadingId("village_sleep"), "settings-part-village_sleep");
});
