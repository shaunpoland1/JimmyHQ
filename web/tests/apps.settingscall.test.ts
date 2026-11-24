// The call that takes the owner to the app's own page under Settings, and the sentence that
// page may show after "Your own addresses".

import assert from "node:assert/strict";
import { test } from "node:test";
import { CALLS, createRelay } from "../src/modules/apps/lib/bridge.ts";
import type { BridgeResponse } from "../src/modules/apps/lib/bridge.ts";
import { readHosts } from "../src/modules/apps/lib/hosts.ts";
import { SETTINGS_REFUSALS, ownSettingsTarget } from "../src/modules/apps/lib/ownSettings.ts";
import { GESTURE_WINDOW_MS } from "../src/modules/apps/lib/uploads.ts";

const ARTICLES = { id: "articles", settings_section: null };

test("opening Settings is a call the shell answers itself, never one the server sees", () => {
  assert.equal(CALLS["ui.settings"]?.shell, true);
  assert.equal(CALLS["ui.settings"]?.needs, undefined);
});

test("the owner's touch takes the page to its own app's settings and nowhere else", () => {
  const active = { isActive: true, hasBeenActive: true };
  assert.deepEqual(ownSettingsTarget(ARTICLES, active, null, 1000), { ok: true, hash: "#/settings/app.articles" });
  assert.deepEqual(ownSettingsTarget({ id: "posts", settings_section: "app.posts_board" }, active, null, 1000), { ok: true, hash: "#/settings/app.posts_board" });
});

test("a page that was not touched by the owner just now cannot open Settings", () => {
  const idle = { isActive: false, hasBeenActive: true };
  const refused = ownSettingsTarget(ARTICLES, idle, 1000, 1000 + GESTURE_WINDOW_MS + 1);
  assert.equal(refused.ok, false);
  assert.equal(!refused.ok && refused.error.code, "not_allowed");
  assert.equal(!refused.ok && refused.error.message, SETTINGS_REFUSALS.noGesture);
  assert.equal(ownSettingsTarget(ARTICLES, { isActive: false, hasBeenActive: false }, null, 1000).ok, false);
  assert.equal(ownSettingsTarget(ARTICLES, idle, 1000, 2000).ok, true);
});

test("an address, a path or an app id sent with the call changes nothing about where it goes", async () => {
  const seen: unknown[] = [];
  const out: BridgeResponse[] = [];
  const relay = createRelay({
    appId: "articles",
    grant: { permissions: [], tier: "sandboxed" } as never,
    send: () => Promise.reject(new Error("never sent to the server")),
    post: (r) => out.push(r),
    shell: { "ui.settings": (args) => { seen.push(args); return ownSettingsTarget(ARTICLES, { isActive: true, hasBeenActive: true }, null, 0); } },
  });
  await relay.handle({ id: 1, call: "ui.settings", args: { hash: "#/settings/other", app: "posts", url: "https://example.com" } });
  assert.equal(out.length, 1);
  assert.equal(out[0]?.ok, true);
  const result = (out[0] as { result: { hash: string } }).result;
  assert.equal(result.hash, "#/settings/app.articles");
});

test("the sentence about what to do next is read when the app declares one, and is plain text of limited length", () => {
  const base = { purpose: "Publish drafts.", max: 2 };
  assert.equal(readHosts({ declared: { ...base, next: "Then open the app." }, hosts: [] }).declared?.next, "Then open the app.");
  assert.equal(readHosts({ declared: base, hosts: [] }).declared?.next, null);
  assert.equal(readHosts({ declared: { ...base, next: "  " }, hosts: [] }).declared?.next, null);
  assert.equal(readHosts({ declared: { ...base, next: 7 }, hosts: [] }).declared?.next, null);
  assert.equal(readHosts({ declared: { ...base, next: "x".repeat(500) }, hosts: [] }).declared?.next?.length, 200);
});
