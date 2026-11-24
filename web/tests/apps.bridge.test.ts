import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BADGE_MAX,
  CALLS,
  bridgeBody,
  readMount,
  DEFAULT_LIMITS,
  ERROR_CODES,
  FRAME_ALLOW,
  FRAME_REFERRER_POLICY,
  FRAME_SANDBOX,
  REFUSALS,
  TOAST_MAX_LENGTH,
  badgeCount,
  bridgePath,
  checkJson,
  createLimiter,
  errorFromFailure,
  eventFromFeed,
  frameSrc,
  httpsHost,
  isFromFrame,
  isHello,
  mountPath,
  parseRequest,
  readReply,
  readyEvent,
  refusalFor,
  requestId,
  toastText,
} from "../src/modules/apps/lib/bridge.ts";
import type { Grant } from "../src/modules/apps/lib/bridge.ts";

const MOUNT = "m".repeat(32);
const sandboxed = (...permissions: string[]): Grant => ({ tier: "sandboxed", permissions });

test("the frame may run scripts and use forms and nothing else, and above all is never given its own origin", () => {
  assert.equal(FRAME_SANDBOX, "allow-scripts allow-forms");
  for (const token of ["allow-same-origin", "allow-top-navigation", "allow-popups", "allow-modals", "allow-downloads"]) {
    assert.equal(FRAME_SANDBOX.includes(token), false, token);
  }
  assert.equal(FRAME_ALLOW, "");
  assert.equal(FRAME_REFERRER_POLICY, "no-referrer");
});

test("the frame's address is /appfiles/<mount>/<entry>, and a described screen has none", () => {
  assert.equal(frameSrc(MOUNT, "index.html"), `/appfiles/${MOUNT}/index.html`);
  assert.equal(frameSrc(MOUNT, "web/index.html"), `/appfiles/${MOUNT}/web/index.html`);
  // The server sends no entry for a described screen: JimmyHQ draws it, and nothing is framed.
  assert.equal(frameSrc(MOUNT, null), null);
});

test("the mount's answer gives the frame its address, the owner's grant and the two things about the owner", () => {
  const answer = { mount: MOUNT, base: `/appfiles/${MOUNT}/`, entry: "index.html", app: "water", name: "Water", version: "1.0.0", tier: "sandboxed", permissions: ["tasks.write"], development: false, api: "1.0", timezone: "Europe/Paris", week_start: "sunday", upload_max_mb: 7, thumbnail_px: 200, picture_px: 3000, picture_max_mb: 9, pictures_per_minute: 12, dictate_max_s: 90, private: false };
  assert.deepEqual(readMount(answer), {
    src: `/appfiles/${MOUNT}/index.html`,
    version: "1.0.0",
    grant: { tier: "sandboxed", permissions: ["tasks.write"] },
    timezone: "Europe/Paris",
    weekStart: "sunday",
    uploadMaxBytes: 7 * 1024 * 1024,
    thumbnailPx: 200,
    picture: { px: 3000, maxBytes: 9 * 1024 * 1024, perMinute: 12 },
    dictateMaxS: 90,
    private: false,
    save: { maxBytes: 8 * 1024 * 1024, maxParts: 5, html: true, perMinute: 12 },
  });
  assert.deepEqual(readMount({ ...answer, tier: "full" })?.grant.tier, "full");
});

test("a mount answer that is bent out of shape opens nothing or falls back to the safe reading", () => {
  const answer = { mount: MOUNT, entry: "index.html", version: "1.0.0", tier: "sandboxed", permissions: ["tasks.write"] };
  for (const odd of [null, "x", [], {}, { ...answer, mount: "../../api" }, { ...answer, entry: null }, { ...answer, entry: "https://example.com/x.html" }, { ...answer, entry: "../index.html" }]) {
    assert.equal(readMount(odd), null);
  }
  const read = readMount({ ...answer, tier: "root", permissions: "all", timezone: "<script>", week_start: "friday" });
  assert.deepEqual(read?.grant, { tier: "sandboxed", permissions: [] });
  assert.equal(read?.timezone, "");
  assert.equal(read?.weekStart, "monday");
  assert.deepEqual(readMount({ ...answer, permissions: [1, "tasks.write", null, {}] })?.grant.permissions, ["tasks.write"]);
  // An answer that does not plainly say "not private" is read as private, and a bent limit as the default.
  assert.equal(read?.private, true);
  assert.equal(readMount({ ...answer, private: 0 })?.private, true);
  assert.equal(readMount({ ...answer, private: false })?.private, false);
  assert.equal(readMount({ ...answer, dictate_max_s: -5 })?.dictateMaxS, 300);
  assert.equal(readMount({ ...answer, dictate_max_s: "9999" })?.dictateMaxS, 300);
});

test("the page is told its own id, version, permissions, theme, time zone and week start, and nothing else", () => {
  const event = readyEvent({ id: "water", version: "1.0.0", permissions: ["tasks.write"] }, "dark", "Europe/Paris", "sunday");
  assert.deepEqual(event, { event: "ready", data: { app: "water", version: "1.0.0", theme: "dark", timezone: "Europe/Paris", week_start: "sunday", permissions: ["tasks.write"] } });
  const extra = readyEvent({ id: "water", version: "1.0.0", permissions: [], name: "Sam", email: "x", session: "y" } as never, "light", "UTC");
  assert.deepEqual(Object.keys(extra.data as object).sort(), ["app", "permissions", "theme", "timezone", "version", "week_start"]);
});

test("a relayed call names its call again in the body, and is sent only to an app's own bridge address", () => {
  assert.deepEqual(bridgeBody("storage.set", { key: "k", value: 1 }), { jimmyhq: 1, call: "storage.set", args: { key: "k", value: 1 } });
  assert.deepEqual(bridgeBody("storage.keys", undefined), { jimmyhq: 1, call: "storage.keys", args: {} });
  assert.equal(bridgePath("water", "records.list"), "/api/apps/water/bridge/records.list");
  for (const id of ["_install", "_installed", "_examples", "..", "Water", "a"]) assert.equal(bridgePath(id, "storage.get"), null, id);
  assert.equal(bridgePath("water", "ui.toast"), null);
  assert.equal(mountPath("_install"), null);
});

test("an entry that would leave /appfiles/ or name another place never becomes an address", () => {
  const bad = ["", "/etc/passwd", "../index.html", "a/../../b.html", "a//b.html", "https://example.com/x.html", "//example.com/x", "javascript:alert(1)", "data:text/html,x", "a\\b.html", "index.html?x=1", "index.html#x", "a b.html", ".hidden", "x".repeat(400)];
  for (const entry of bad) assert.equal(frameSrc(MOUNT, entry), null, entry);
  for (const mount of ["", "short", "../../api", `${MOUNT}/x`, `${MOUNT}?`, null, 7]) assert.equal(frameSrc(mount, "index.html"), null, String(mount));
});

test("a window message counts only when it comes from the frame's own window with the opaque origin", () => {
  const frame = { name: "frame" };
  const other = { name: "other" };
  assert.equal(isFromFrame({ source: frame, origin: "null" }, frame), true);
  assert.equal(isFromFrame({ source: other, origin: "null" }, frame), false);
  assert.equal(isFromFrame({ source: null, origin: "null" }, frame), false);
  assert.equal(isFromFrame({ source: null, origin: "null" }, null), false);
  assert.equal(isFromFrame({ source: undefined, origin: "null" }, undefined), false);
  // The same window with a real origin is no longer the sandboxed page that was mounted.
  assert.equal(isFromFrame({ source: frame, origin: "http://127.0.0.1:8440" }, frame), false);
  assert.equal(isFromFrame({ source: frame, origin: "https://example.com" }, frame), false);
});

test("the only window message the shell hears is an exact hello", () => {
  assert.equal(isHello({ jimmyhq: 1, hello: true }), true);
  for (const bad of [null, "hello", { jimmyhq: 1 }, { hello: true }, { jimmyhq: 2, hello: true }, { jimmyhq: 1, hello: "yes" }, { jimmyhq: 1, hello: true, app: "other" }, [1, true]]) {
    assert.equal(isHello(bad), false, JSON.stringify(bad));
  }
});

test("a request needs an id that is a whole number or short text", () => {
  assert.equal(requestId({ id: 1 }), 1);
  assert.equal(requestId({ id: 0 }), 0);
  assert.equal(requestId({ id: "a" }), "a");
  for (const id of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, "", "x".repeat(65), null, true, {}, []]) {
    assert.equal(requestId({ id }), null, String(id));
  }
  assert.equal(requestId("id"), null);
  assert.equal(requestId(Object.create({ id: 1 })), null);
});

test("a well-formed request is accepted, and missing args mean an empty object", () => {
  assert.deepEqual(parseRequest({ id: 1, call: "storage.get", args: { key: "k" } }), { ok: true, request: { id: 1, call: "storage.get", args: { key: "k" } } });
  assert.deepEqual(parseRequest({ id: "a", call: "storage.keys" }), { ok: true, request: { id: "a", call: "storage.keys", args: {} } });
});

test("every refusal is a sentence that says nothing was done", () => {
  const sentences = [
    ...Object.values(REFUSALS).map((value) => (typeof value === "function" ? (value as (...words: string[]) => string)("tasks.create", "tasks.write") : value)),
  ];
  for (const sentence of sentences) {
    assert.match(sentence, /^You/, sentence.slice(0, 30));
    assert.match(sentence, /nothing was done/, sentence);
    assert.match(sentence, /\.$/, sentence);
  }
});

test("a call that is not on the list is refused, and its name is echoed safely", () => {
  for (const call of ["tape.read", "storage.drop", "__proto__", "constructor", "toString", "storage.get/../../settings", "STORAGE.GET", ""]) {
    const parsed = parseRequest({ id: 1, call });
    assert.equal(parsed.ok, false, call);
    if (!parsed.ok) assert.equal(parsed.error.code, "invalid");
  }
  const noisy = parseRequest({ id: 1, call: `<img src=x onerror=1>${"x".repeat(500)}` });
  assert.equal(noisy.ok, false);
  if (!noisy.ok) {
    assert.equal(/[<>]/.test(noisy.error.message), false);
    assert.ok(noisy.error.message.length < 200);
  }
});

test("only plain JSON passes: no dates, maps, typed arrays, functions, class instances, holes or numbers that are not numbers", () => {
  class Thing {
    a = 1;
  }
  const sparse: unknown[] = [];
  sparse[3] = 1;
  const bad: unknown[] = [new Date(0), new Map(), new Set(), new Uint8Array(4), () => 1, Symbol("s"), 10n, new Thing(), sparse, Number.NaN, Number.POSITIVE_INFINITY, undefined, { [Symbol("s")]: 1 }, /x/];
  for (const value of bad) {
    const check = checkJson({ value });
    assert.equal(check.ok, false, String(value));
    if (!check.ok) assert.equal(check.error.code, "invalid");
  }
  assert.equal(checkJson({ a: [1, "two", true, null, { b: 2.5 }] }).ok, true);
  assert.equal(checkJson(Object.assign(Object.create(null), { a: 1 })).ok, true);
});

test("data that refers to itself or nests too deeply is refused instead of walked for ever", () => {
  const loop: Record<string, unknown> = {};
  loop.self = loop;
  const check = checkJson(loop);
  assert.equal(check.ok, false);
  if (!check.ok) assert.equal(check.error.message, REFUSALS.tooDeep);

  let deep: unknown = 1;
  for (let i = 0; i < DEFAULT_LIMITS.maxDepth + 1; i += 1) deep = [deep];
  assert.equal(checkJson(deep).ok, false);
  let fine: unknown = 1;
  for (let i = 0; i < DEFAULT_LIMITS.maxDepth; i += 1) fine = [fine];
  assert.equal(checkJson(fine).ok, true);
});

test("size is counted in bytes, so text outside ASCII cannot slip past the limit", () => {
  const limits = { maxBytes: 100, maxDepth: 8 };
  assert.equal(checkJson("a".repeat(90), limits).ok, true);
  assert.equal(checkJson("é".repeat(60), limits).ok, false);
  assert.equal(checkJson("😀".repeat(30), limits).ok, false);
  assert.equal(checkJson(Array.from({ length: 200 }, () => 1), limits).ok, false);
  assert.equal(checkJson({ ["k".repeat(120)]: 1 }, limits).ok, false);
  const big = checkJson("a".repeat(1000), limits);
  assert.equal(big.ok, false);
  if (!big.ok) assert.equal(big.error.code, "too_large");
});

test("the shell allows for the largest call the design names and a little more", () => {
  assert.ok(DEFAULT_LIMITS.maxBytes >= 1024 * 1024);
  assert.ok(DEFAULT_LIMITS.maxBytes < 2 * 1024 * 1024);
  assert.equal(DEFAULT_LIMITS.callsPerSecond, 20);
});

test("a call that needs no permission is allowed with none", () => {
  for (const call of ["storage.get", "storage.set", "storage.delete", "storage.keys", "records.list", "records.create", "settings.get", "ui.toast", "ui.badge"]) {
    assert.equal(refusalFor(call, {}, sandboxed()), null, call);
  }
});

test("asking the owner's limits needs no permission and is answered by the server, not by the shell", () => {
  assert.equal(CALLS["ui.limits"]?.needs, undefined);
  assert.equal(CALLS["ui.limits"]?.shell, undefined);
  assert.equal(refusalFor("ui.limits", {}, sandboxed()), null);
  assert.equal(bridgePath("water", "ui.limits"), "/api/apps/water/bridge/ui.limits");
});

test("each call that needs a permission is refused without it and allowed with it", () => {
  const needs: Record<string, string> = {
    "tasks.list": "tasks.read",
    "tasks.create": "tasks.write",
    "tasks.complete": "tasks.write",
    "bench.file": "bench.file",
    "model.ask": "model.ask",
    "memory.recall": "memory.recall",
  };
  for (const [call, permission] of Object.entries(needs)) {
    const refused = refusalFor(call, {}, sandboxed());
    assert.equal(refused?.code, "not_allowed", call);
    assert.ok(refused?.message.includes(permission), call);
    assert.equal(refusalFor(call, {}, sandboxed(permission)), null, call);
  }
  // Reading tasks is not writing them, and the other way round.
  assert.equal(refusalFor("tasks.create", {}, sandboxed("tasks.read"))?.code, "not_allowed");
  assert.equal(refusalFor("tasks.list", {}, sandboxed("tasks.write"))?.code, "not_allowed");
});

test("every call on the list has a rule this file tests", () => {
  assert.deepEqual(Object.keys(CALLS).sort(), [
    "app.call", "bench.file", "files.picking", "files.picture", "files.thumbnail", "files.upload",
    "hosts.list", "memory.recall", "model.ask", "model.embed", "net.fetch", "records.create", "records.delete", "records.get", "records.list", "records.update",
    "settings.get", "speech.available", "speech.dictate", "storage.delete", "storage.get", "storage.keys", "storage.set",
    "tasks.complete", "tasks.create", "tasks.list", "ui.badge", "ui.limits", "ui.save", "ui.settings", "ui.toast", "ui.touch",
    "web.read", "web.search",
  ]);
  assert.equal(Object.isFrozen(CALLS), true);
});

test("net.fetch is allowed only for an https address on exactly a host the owner agreed to", () => {
  const grant = sandboxed("net:api.booking.example");
  assert.equal(refusalFor("net.fetch", { url: "https://api.booking.example/v1/slots" }, grant), null);
  assert.equal(refusalFor("net.fetch", { url: "https://API.Booking.Example/v1" }, grant), null);
  for (const url of [
    "http://api.booking.example/",
    "https://booking.example/",
    "https://evil.api.booking.example/",
    "https://api.booking.example.evil.example/",
    // Built here so the text does not read as an address of a person.
    ["https://user:pw", "api.booking.example/"].join("@"),
    ["https://api.booking.example", "evil.example/"].join("@"),
    "ftp://api.booking.example/",
    "//api.booking.example/",
    "not an address",
  ]) {
    assert.equal(refusalFor("net.fetch", { url }, grant)?.code, "not_allowed", url);
  }
  assert.equal(refusalFor("net.fetch", { url: "https://api.booking.example/" }, sandboxed())?.code, "not_allowed");
  assert.equal(refusalFor("net.fetch", {}, sandboxed())?.code, "not_allowed");
  assert.equal(httpsHost("https://Example.COM:8443/x"), "example.com");
});

test("a page reaches a host the owner gave the app, exactly that host over https, and no other", () => {
  const mounted = readMount({ mount: "m".repeat(32), entry: "index.html", version: "1.0.0", tier: "sandboxed", permissions: ["net:api.booking.example"], owner_hosts: ["Blog.Example.org", "x y", 5, "../x"] });
  assert.deepEqual(mounted?.grant, { tier: "sandboxed", permissions: ["net:api.booking.example"], ownerHosts: ["blog.example.org"] });
  const grant = mounted?.grant ?? sandboxed();
  assert.equal(refusalFor("net.fetch", { url: "https://blog.example.org/wp-json/" }, grant), null);
  assert.equal(refusalFor("net.fetch", { url: "https://api.booking.example/v1" }, grant), null);
  for (const url of ["http://blog.example.org/", "https://example.org/", "https://x.blog.example.org/", "https://blog.example.org.evil.example/"]) {
    assert.equal(refusalFor("net.fetch", { url }, grant)?.code, "not_allowed", url);
  }
  // With none given, the grant reads as it always did and the host is refused.
  const bare = readMount({ mount: "m".repeat(32), entry: "index.html", version: "1.0.0", tier: "sandboxed", permissions: [] });
  assert.deepEqual(bare?.grant, { tier: "sandboxed", permissions: [] });
  assert.equal(refusalFor("net.fetch", { url: "https://blog.example.org/" }, bare?.grant ?? sandboxed())?.code, "not_allowed");
});

test("the web, similarity and hosts calls need what the server needs and nothing more", () => {
  assert.equal(refusalFor("web.search", { query: "x" }, sandboxed())?.code, "not_allowed");
  assert.equal(refusalFor("web.read", { url: "https://example.org/" }, sandboxed("model.ask"))?.code, "not_allowed");
  assert.equal(refusalFor("web.search", { query: "x" }, sandboxed("web")), null);
  assert.equal(refusalFor("web.read", { url: "https://example.org/" }, sandboxed("web")), null);
  assert.equal(refusalFor("model.embed", { texts: ["x"] }, sandboxed("web"))?.code, "not_allowed");
  assert.equal(refusalFor("model.embed", { texts: ["x"] }, sandboxed("model.ask")), null);
  assert.equal(refusalFor("hosts.list", {}, sandboxed()), null);
});

test("app.call belongs to full apps only", () => {
  assert.equal(refusalFor("app.call", { name: "board" }, sandboxed())?.code, "not_allowed");
  assert.equal(refusalFor("app.call", { name: "board" }, { tier: "full", permissions: [] }), null);
});

test("the limiter lets a second's worth through, refuses the rest with a wait, and refills with time", () => {
  let clock = 1000;
  const limiter = createLimiter({ callsPerSecond: 20, silentAfter: 100 }, () => clock);
  for (let i = 0; i < 20; i += 1) assert.equal(limiter.take().ok, true, String(i));
  const refused = limiter.take();
  assert.equal(refused.ok, false);
  if (!refused.ok) {
    assert.equal(refused.retry_after_s, 1);
    assert.equal(refused.silent, false);
  }
  clock += 500;
  for (let i = 0; i < 10; i += 1) assert.equal(limiter.take().ok, true, String(i));
  assert.equal(limiter.take().ok, false);
  // A clock that jumps backwards gives nothing away.
  clock -= 60_000;
  assert.equal(limiter.take().ok, false);
});

test("a flood past the silent mark gets no more answers until it stops", () => {
  let clock = 0;
  const limiter = createLimiter({ callsPerSecond: 20, silentAfter: 100 }, () => clock);
  for (let i = 0; i < 20; i += 1) limiter.take();
  let silent = 0;
  for (let i = 0; i < 300; i += 1) {
    const turn = limiter.take();
    if (!turn.ok && turn.silent) silent += 1;
  }
  assert.equal(silent, 200);
  clock += 2000;
  assert.equal(limiter.take().ok, true);
});

test("the server path is built only from an app id and a listed call", () => {
  assert.equal(bridgePath("water", "storage.get"), "/api/apps/water/bridge/storage.get");
  for (const id of ["", "Water", "wa ter", "../settings", "water/../../auth", "water?x", "a", "1app", "x".repeat(40)]) {
    assert.equal(bridgePath(id, "storage.get"), null, id);
    assert.equal(mountPath(id), null, id);
  }
  for (const call of ["tape.read", "../mount", "storage.get/..", "", "ui.toast", "ui.badge"]) assert.equal(bridgePath("water", call), null, call);
  assert.equal(mountPath("water"), "/api/apps/water/mount");
});

test("a failed request becomes one of the seven codes with a sentence", () => {
  const cases: [number, string][] = [[0, "unavailable"], [503, "unavailable"], [401, "not_allowed"], [403, "not_allowed"], [404, "not_found"], [413, "too_large"], [429, "quota"], [400, "invalid"], [422, "invalid"], [500, "internal"], [418, "internal"]];
  for (const [status, code] of cases) {
    const error = errorFromFailure(status, "The server's sentence.");
    assert.equal(error.code, code, String(status));
    assert.ok((ERROR_CODES as readonly string[]).includes(error.code));
    assert.ok(error.message.length > 0);
  }
  assert.equal(errorFromFailure(403, "You may not do that.").message, "You may not do that.");
  // An internal failure's detail may describe the server; the app gets a plain sentence instead.
  assert.equal(errorFromFailure(500, "Traceback: /srv/secret/path").message, REFUSALS.internal);
});

test("the server's answer is passed on in the protocol's shape and nothing else", () => {
  assert.deepEqual(readReply({ ok: true, result: { n: 1 } }), { ok: true, result: { n: 1 } });
  assert.deepEqual(readReply({ ok: true }), { ok: true, result: null });
  assert.deepEqual(readReply({ ok: false, error: { code: "quota", message: "You are over your limit.", retry_after_s: 12, internal: "x" } }), {
    ok: false,
    error: { code: "quota", message: "You are over your limit.", retry_after_s: 12 },
  });
  for (const odd of [null, "ok", [], { result: 1 }, { ok: "true" }]) assert.equal(readReply(odd).ok, false, JSON.stringify(odd));
  const strange = readReply({ ok: false, error: { code: "hacked", message: 7 } });
  assert.deepEqual(strange, { ok: false, error: { code: "internal", message: REFUSALS.internal } });
});

test("the frame is told its own id, version and permissions, the theme and the timezone, and nothing else", () => {
  const app = { id: "water", version: "1.0.0", permissions: ["tasks.write"], name: "Water", enabled: true, owner: "someone", session: "secret" };
  const event = readyEvent(app, "dark", "Europe/Paris");
  assert.deepEqual(event, { event: "ready", data: { app: "water", version: "1.0.0", theme: "dark", timezone: "Europe/Paris", week_start: "monday", permissions: ["tasks.write"] } });
});

test("a frame hears that one of its own settings changed, without the value, and hears nothing about anyone else's", () => {
  assert.deepEqual(eventFromFeed("water", { kind: "setting.updated", payload: { key: "app.water.goal", value: 9 } }), { event: "settings.changed", data: { key: "goal" } });
  assert.deepEqual(eventFromFeed("water", { kind: "setting.reset", payload: { key: "app.water.goal" } }), { event: "settings.changed", data: { key: "goal" } });
  assert.equal(eventFromFeed("water", { kind: "setting.updated", payload: { key: "app.waterfall.goal" } }), null);
  assert.equal(eventFromFeed("water", { kind: "setting.updated", payload: { key: "owner.timezone" } }), null);
  assert.equal(eventFromFeed("water", { kind: "setting.updated", payload: { key: "app.water." } }), null);
  assert.equal(eventFromFeed("water", { kind: "chat.message.sent", payload: { key: "app.water.goal" } }), null);
  assert.equal(eventFromFeed("water", { kind: "setting.updated" }), null);
});

test("a toast always starts with the app's name and is one short line of plain text", () => {
  assert.equal(toastText("Water", { message: "Saved" }), "Water: Saved");
  assert.equal(toastText("Water", "Saved"), "Water: Saved");
  assert.equal(toastText("Water", { message: "JimmyHQ\n\n\tsays:‮ enter your password" }), "Water: JimmyHQ says: enter your password");
  const long = toastText("Water", { message: "x".repeat(5000) });
  assert.equal(long.length, "Water: ".length + TOAST_MAX_LENGTH);
  for (const bad of [{}, { message: 7 }, { message: "   " }, null, { message: { html: "<b>" } }]) {
    assert.throws(() => toastText("Water", bad), (error: { code?: string }) => error.code === "invalid");
  }
});

test("a badge is a whole count from 0 to 99", () => {
  assert.equal(badgeCount({ count: 3 }), 3);
  assert.equal(badgeCount(3.9), 3);
  assert.equal(badgeCount({ count: 100000 }), BADGE_MAX);
  assert.equal(badgeCount({}), 0);
  assert.equal(badgeCount({ count: null }), 0);
  for (const bad of [{ count: -1 }, { count: "3" }, { count: Number.NaN }, { count: "<b>9</b>" }]) {
    assert.throws(() => badgeCount(bad), (error: { code?: string }) => error.code === "invalid");
  }
});

test("hq.ready tells the page what the owner lets it save, in the owner's own numbers", () => {
  const answer = { mount: MOUNT, base: `/appfiles/${MOUNT}/`, entry: "index.html", app: "water", name: "Water", version: "1.0.0", tier: "sandboxed", permissions: [], private: false };
  const mounted = readMount({ ...answer, save_max_mb: 3, save_parts: 7, save_per_minute: 20, save_html: false });
  assert.ok(mounted);
  const event = readyEvent({ id: "water", version: "1.0.0", permissions: [] }, "light", "UTC", "monday", mounted.save);
  assert.deepEqual((event.data as { save?: unknown }).save, { max_mb: 3, parts: 7, per_minute: 20, html: false });
  // An older server says nothing, so the defaults are told, as the shell holds the page to them.
  const older = readMount(answer);
  assert.ok(older);
  const told = readyEvent({ id: "water", version: "1.0.0", permissions: [] }, "light", "UTC", "monday", older.save);
  assert.deepEqual((told.data as { save?: unknown }).save, { max_mb: 8, parts: 5, per_minute: 12, html: true });
  // Only the numbers: nothing else of the owner's rides along.
  assert.deepEqual(Object.keys((told.data as { save: object }).save).sort(), ["html", "max_mb", "parts", "per_minute"]);
});
