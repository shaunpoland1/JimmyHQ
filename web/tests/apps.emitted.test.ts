import assert from "node:assert/strict";
import { test } from "node:test";
import { emittedPath, isAppEventName, readEmitted } from "../src/modules/apps/lib/emitted.ts";

test("an app's own event reaches its frame with its data", () => {
  const found = readEmitted({ events: [{ seq: 4, event: "board.changed", data: { post: 1 }, at: 9 }], next: 4 }, 3);
  assert.deepEqual(found, { events: [{ event: "board.changed", data: { post: 1 } }], next: 4 });
});

test("an event named like one JimmyHQ sends never reaches the frame, whatever the server said", () => {
  for (const name of ["ready", "theme", "reload", "settings.changed", "bench.decided", "visible", "records.changed"]) {
    assert.equal(isAppEventName(name), false, name);
  }
  const found = readEmitted({ events: [{ event: "reload" }, { event: "Board" }, { event: "board.changed" }], next: 9 }, 0);
  assert.deepEqual(found.events.map((e) => e.event), ["board.changed"]);
});

test("where to ask from next never goes backwards and an unreadable answer changes nothing", () => {
  assert.equal(readEmitted({ events: [], next: 2 }, 5).next, 5);
  assert.deepEqual(readEmitted("nonsense", 5), { events: [], next: 5 });
});

test("only an app id is ever put in the address", () => {
  assert.equal(emittedPath("drafts", -1), "/api/apps/drafts/emitted?after=-1");
  assert.equal(emittedPath("../auth", 0), null);
});

test("an open frame loads again only when a new copy of its own app is put in place", async () => {
  const { reloadsFrame } = await import("../src/modules/apps/lib/events.ts");
  assert.equal(reloadsFrame("drafts", { kind: "apps.installed", payload: { app_id: "drafts" } }), true);
  assert.equal(reloadsFrame("drafts", { kind: "apps.updated", payload: { app_id: "drafts" } }), true);
  assert.equal(reloadsFrame("drafts", { kind: "apps.installed", payload: { app_id: "other" } }), false);
  assert.equal(reloadsFrame("drafts", { kind: "apps.enabled", payload: { app_id: "drafts" } }), false);
});
