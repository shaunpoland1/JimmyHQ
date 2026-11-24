import assert from "node:assert/strict";
import { test } from "node:test";
import { affectsDoor, appEffectWords, appGroups, appToolOn, appUsageText, canDo, ruleWords, withTool } from "../src/modules/door/lib/door.ts";

const LIST = { name: "tasks_list", title: "List", description: "", write: false, always: false, per_day: 200 };
const LOOKUP = { key: "app:library:1700000000000:lookup:read", name: "app_library_lookup", app_id: "library", app_name: "Library", write: false, per_day: 50 };
const NOTE = { key: "app:library:1700000000000:note:record", name: "app_library_note", app_id: "library", app_name: "Library", write: true, per_day: 10 };
const OTHER = { key: "app:shelf:1700000000001:lookup:read", name: "app_shelf_lookup", app_id: "shelf", app_name: "Shelf", write: false, per_day: 50 };

test("an app tool is off for a client until its own key is switched on", () => {
  assert.equal(appToolOn({ tools: {} }, LOOKUP), false);
  assert.equal(appToolOn({ tools: { app_library_lookup: null } }, LOOKUP), false);
  const tools = withTool({}, LOOKUP.key, true);
  assert.equal(appToolOn({ tools }, LOOKUP), true);
  assert.equal(appToolOn({ tools }, OTHER), false);
});

test("an app tool's use is counted under its name and limited under its key", () => {
  const client = { tools: { [LOOKUP.key]: 7 }, used_today: { app_library_lookup: 3 } };
  assert.equal(appUsageText(client, LOOKUP), "3 of 7 today");
  assert.equal(appUsageText({ tools: { [NOTE.key]: null }, used_today: {} }, NOTE), "0 of 10 today");
});

test("app tools are grouped by app in the order they came", () => {
  const groups = appGroups([LOOKUP, OTHER, NOTE]);
  assert.deepEqual(groups.map((g) => [g.app_name, g.tools.map((t) => t.name)]), [
    ["Library", ["app_library_lookup", "app_library_note"]],
    ["Shelf", ["app_shelf_lookup"]],
  ]);
});

test("what a client can do counts its app tools and says how many came from apps", () => {
  const client = { tools: { tasks_list: null, [NOTE.key]: null } };
  assert.equal(canDo(client, [LIST], [LOOKUP, NOTE]), "This client can use 2 tools (1 from your apps): 1 that only read and 1 that can add something.");
  assert.match(canDo({ tools: {} }, [LIST], [LOOKUP]), /nothing else/);
});

test("an app tool says plainly whether it only reads or writes into the app's records", () => {
  assert.equal(appEffectWords(LOOKUP), "Only reads");
  assert.equal(appEffectWords(NOTE), "Writes into Library's own records");
});

test("an app installed, switched off or removed refreshes the door's page, and an app's own events do not", () => {
  for (const kind of ["apps.installed", "apps.removed", "apps.disabled", "apps.updated"]) assert.equal(affectsDoor({ kind }), true, kind);
  assert.equal(affectsDoor({ kind: "app.library.noted" }), false);
  assert.equal(affectsDoor({ kind: "apps.tool_recorded" }), false);
});

test("the refusals of app tools read as plain words", () => {
  assert.equal(ruleWords("door_app_tool_timeout"), "the app did not answer in time");
  assert.equal(ruleWords("door_app_not_running"), "the app was not running");
  assert.equal(ruleWords("door_app_tool_failed"), "the app said it did not work");
});
