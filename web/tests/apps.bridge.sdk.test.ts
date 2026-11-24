// The library an app's page includes (sdk/js/bridge.js), alone and joined to
// the shell's relay over a real MessageChannel.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The library is plain JavaScript, as app authors receive it.
import { connect, hq as pageHq } from "../../sdk/js/bridge.js";
import { CALLS, FRAME_ALLOW, FRAME_REFERRER_POLICY, FRAME_SANDBOX, createRelay, readyEvent, themeEvent } from "../src/modules/apps/lib/bridge.ts";
import type { Grant } from "../src/modules/apps/lib/bridge.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

type Listener = (event: { source: unknown; data: unknown; ports: unknown[] }) => void;

function fakeWindow() {
  const listeners: Listener[] = [];
  const hellos: { data: unknown; target: unknown }[] = [];
  const parent = { postMessage: (data: unknown, target: unknown) => hellos.push({ data, target }) };
  const win = {
    parent,
    document: { documentElement: { dataset: {} as Record<string, string> } },
    addEventListener: (type: string, fn: Listener) => {
      if (type === "message") listeners.push(fn);
    },
  };
  const deliver = (event: { source: unknown; data: unknown; ports: unknown[] }) => listeners.forEach((fn) => fn(event));
  return { win, parent, hellos, deliver };
}

/** A page, a shell relay and a server that remembers, joined as AppFrame joins them. */
function mounted(grant: Grant = { tier: "sandboxed", permissions: ["tasks.write"] }, shell?: Record<string, (args: unknown) => unknown>) {
  const page = fakeWindow();
  const hq = connect(page.win);
  const channel = new MessageChannel();
  const store = new Map<string, unknown>();
  const sent: { appId: string; call: string; args: Record<string, unknown> }[] = [];
  const relay = createRelay({
    appId: "water",
    grant,
    shell,
    post: (response) => channel.port1.postMessage(response),
    send: async (appId, call, raw) => {
      const args = raw as Record<string, unknown>;
      sent.push({ appId, call, args });
      if (call === "storage.get") return { ok: true, result: store.get(String(args.key)) ?? null };
      if (call === "storage.set") return { ok: true, result: void store.set(String(args.key), args.value) };
      if (call === "settings.get") return args.key === "goal" ? { ok: true, result: 8 } : { ok: false, error: { code: "not_found", message: "You asked for a setting this app did not declare. Nothing was read. Declare it in the manifest." } };
      if (call === "tasks.create") return { ok: true, result: { id: "t1" } };
      if (call === "ui.limits") return { ok: true, result: { save_max_mb: 8, save_parts: 5, save_per_minute: 12, save_html: true, call_max_kb: 1024, picture_px: 2048 } };
      return { ok: false, error: { code: "quota", message: "You are over your limit. Nothing was done. Try again later.", retry_after_s: 30 } };
    },
  });
  channel.port1.on("message", (data: unknown) => void relay.handle(data));
  const handshake = () => {
    page.deliver({ source: page.parent, data: { jimmyhq: 1, port: channel.port2 }, ports: [channel.port2] });
    channel.port1.postMessage(readyEvent({ id: "water", version: "1.0.0", permissions: grant.permissions }, "dark", "UTC"));
  };
  const close = () => {
    channel.port1.close();
    channel.port2.close();
  };
  return { hq, page, channel, sent, handshake, close };
}

test("outside a browser the library exports nothing to call, so importing it is harmless", () => {
  assert.equal(pageHq, undefined);
});

test("the page says hello to its parent and puts nothing of its own in the message", () => {
  const page = fakeWindow();
  connect(page.win);
  assert.deepEqual(page.hellos, [{ data: { jimmyhq: 1, hello: true }, target: "*" }]);
});

/** Waits until `done()` is true, looking every few milliseconds, and gives up after `ms`. */
async function until(done: () => boolean, ms = 2000): Promise<void> {
  const end = Date.now() + ms;
  while (!done() && Date.now() < end) await new Promise((resolve) => setTimeout(resolve, 5));
}

test("the page takes a port only from its parent, and only the first one", async () => {
  const page = fakeWindow();
  const hq = connect(page.win);
  const stranger = new MessageChannel();
  const shell = new MessageChannel();
  const late = new MessageChannel();
  const seen: string[] = [];
  stranger.port1.on("message", () => seen.push("stranger"));
  shell.port1.on("message", () => seen.push("shell"));
  late.port1.on("message", () => seen.push("late"));

  page.deliver({ source: { other: "window" }, data: { jimmyhq: 1 }, ports: [stranger.port2] });
  page.deliver({ source: page.parent, data: { jimmyhq: 2 }, ports: [stranger.port2] });
  page.deliver({ source: page.parent, data: { jimmyhq: 1 }, ports: [] });
  page.deliver({ source: page.parent, data: "jimmyhq", ports: [stranger.port2] });
  page.deliver({ source: page.parent, data: { jimmyhq: 1 }, ports: [shell.port2] });
  page.deliver({ source: page.parent, data: { jimmyhq: 1 }, ports: [late.port2] });

  try {
    void hq.storage.keys().catch(() => undefined);
    // Wait for the message itself, not for a fixed time: a busy machine delivers it late.
    await until(() => seen.length > 0);
    await new Promise((resolve) => setTimeout(resolve, 20)); // room for a wrong second one
    assert.deepEqual(seen, ["shell"]);
  } finally {
    // An open port keeps the whole test run alive, so they are closed even when this fails.
    for (const channel of [stranger, shell, late]) {
      channel.port1.close();
      channel.port2.close();
    }
  }
});

test("the library offers exactly the calls the shell allows", () => {
  const { hq, close } = mounted();
  const offered: string[] = [];
  for (const [group, value] of Object.entries(hq as Record<string, unknown>)) {
    if (group === "ready" || group === "on") continue;
    for (const name of Object.keys(value as object)) offered.push(`${group}.${name}`);
  }
  assert.deepEqual(offered.sort(), Object.keys(CALLS).sort());
  close();
});

test("calls made before the bridge is ready wait and are then sent in order", async () => {
  const { hq, sent, handshake, close } = mounted();
  const first = hq.storage.set("day", 1);
  const second = hq.storage.get("day");
  assert.equal(sent.length, 0);
  handshake();
  await first;
  assert.equal(await second, 1);
  assert.deepEqual(sent.map((item) => item.call), ["storage.set", "storage.get"]);
  close();
});

test("ready resolves to the app's id, version, theme, time zone, week start and permissions", async () => {
  const { hq, handshake, close } = mounted();
  handshake();
  const ready = await hq.ready;
  close();
  assert.deepEqual(ready, { app: "water", version: "1.0.0", theme: "dark", timezone: "UTC", week_start: "monday", permissions: ["tasks.write"] });
});

test("the water example's calls work end to end: short forms become named arguments and the app id is the shell's", async () => {
  const { hq, sent, handshake, close } = mounted();
  handshake();
  await hq.ready;
  assert.equal(await hq.storage.get("2026-01-01"), null);
  assert.equal(await hq.settings.get("goal"), 8);
  await hq.storage.set("2026-01-01", 1);
  assert.equal(await hq.storage.get({ key: "2026-01-01" }), 1);
  assert.deepEqual(await hq.tasks.create({ title: "Keep drinking water" }), { id: "t1" });
  assert.deepEqual(sent.map((item) => [item.appId, item.call, item.args]), [
    ["water", "storage.get", { key: "2026-01-01" }],
    ["water", "settings.get", { key: "goal" }],
    ["water", "storage.set", { key: "2026-01-01", value: 1 }],
    ["water", "storage.get", { key: "2026-01-01" }],
    ["water", "tasks.create", { title: "Keep drinking water" }],
  ]);
  close();
});

test("a refusal rejects with an Error that carries the code, the sentence and the wait", async () => {
  const { hq, sent, handshake, close } = mounted();
  handshake();
  // Refused by the shell: the app never asked for this permission.
  await assert.rejects(hq.model.ask({ prompt: "hi" }), (error: Error & { code?: string }) => {
    assert.ok(error instanceof Error);
    assert.equal(error.code, "not_allowed");
    assert.match(error.message, /"model\.ask" permission, so nothing was done/);
    return true;
  });
  assert.equal(sent.length, 0);
  // Refused by the server.
  await assert.rejects(hq.settings.get("other"), (error: Error & { code?: string }) => error.code === "not_found" && /Nothing was read/.test(error.message));
  await assert.rejects(hq.records.list({ table: "book" }), (error: Error & { code?: string; retry_after_s?: number }) => error.code === "quota" && error.retry_after_s === 30);
  close();
});

test("data that cannot be sent rejects the call instead of throwing", async () => {
  const { hq, handshake, close } = mounted();
  handshake();
  await assert.rejects(hq.storage.set("k", () => 1), (error: Error & { code?: string }) => error.code === "invalid");
  close();
});

test("events reach their listeners, a listener can stop listening, and the theme is set on the page", async () => {
  const { hq, page, channel, handshake, close } = mounted();
  const themes: unknown[] = [];
  const stop = hq.on("theme", (data: unknown) => themes.push(data));
  hq.on("theme", () => {
    throw new Error("a broken listener must not stop the others");
  });
  const quiet = console.error;
  console.error = () => undefined;
  handshake();
  await hq.ready;
  assert.equal(page.win.document.documentElement.dataset.theme, "dark");
  try {
    channel.port1.postMessage(themeEvent("light"));
    await until(() => themes.length > 0);
    stop();
    channel.port1.postMessage(themeEvent("dark"));
    await until(() => page.win.document.documentElement.dataset.theme === "dark");
    assert.deepEqual(themes, [{ theme: "light" }]);
    assert.equal(page.win.document.documentElement.dataset.theme, "dark");
  } finally {
    console.error = quiet;
    close();
  }
});

test("an answer nobody is waiting for, or junk on the port, is ignored", async () => {
  const { hq, channel, handshake, close } = mounted();
  handshake();
  for (const junk of [null, "x", [], { id: 999, ok: true, result: 1 }, { ok: true }]) channel.port1.postMessage(junk);
  assert.equal(await hq.settings.get("goal"), 8);
  close();
});

test("hq.ui.limits() asks the server with no arguments and gives back the owner's numbers", async () => {
  const { hq, sent, handshake, close } = mounted({ tier: "sandboxed", permissions: [] });
  handshake();
  const told = await hq.ui.limits();
  close();
  assert.deepEqual(sent.at(-1), { appId: "water", call: "ui.limits", args: {} });
  assert.deepEqual(told, { save_max_mb: 8, save_parts: 5, save_per_minute: 12, save_html: true, call_max_kb: 1024, picture_px: 2048 });
});

test("saving a Blob sends its bytes and its type, and saving text or bytes is sent as the page gave it", async () => {
  const seen: Record<string, unknown>[] = [];
  const { hq, handshake, close } = mounted({ tier: "sandboxed", permissions: [] }, { "ui.save": (args) => (seen.push(args as Record<string, unknown>), { name: "x", bytes: 1 }) });
  handshake();
  const png = Uint8Array.from([0x89, 0x50, 0x4e, 0x47]);
  await hq.ui.save({ name: "scene", blob: new Blob([png], { type: "image/png" }) });
  await hq.ui.save({ name: "scene", type: "image/webp", blob: new Blob([png], { type: "image/png" }) });
  await hq.ui.save({ name: "n", text: "hello" });
  await hq.ui.save({ name: "p", type: "image/png", bytes: png.buffer.slice(0) });
  close();
  const first = seen[0] as { bytes: ArrayBuffer; type: string; blob?: unknown };
  assert.equal(first.type, "image/png");
  assert.equal("blob" in first, false);
  assert.deepEqual([...new Uint8Array(first.bytes)], [...png]);
  assert.equal(seen[1]?.type, "image/webp"); // the page's own type wins over the Blob's
  assert.deepEqual(seen[2], { name: "n", text: "hello" });
  assert.deepEqual([...new Uint8Array(seen[3]?.bytes as ArrayBuffer)], [...png]);
});

test("the library has no dependencies and stays small", () => {
  const source = read("../../sdk/js/bridge.js");
  assert.equal(/^\s*import\s/m.test(source), false);
  assert.equal(/\brequire\(/.test(source), false);
  assert.ok(source.split("\n").length < 230);
});

test("the water example is under 40 lines and uses only calls the library has", () => {
  const app = read("../../apps/examples/water/app.js");
  const page = read("../../apps/examples/water/index.html");
  assert.ok(app.trim().split("\n").length + page.trim().split("\n").length < 40);
  assert.match(app, /^import \{ hq \} from "\.\/_bridge\.js";/);
  const used = [...app.matchAll(/hq\.([a-z]+)\.([a-z]+)\(/g)].map((match) => `${match[1]}.${match[2]}`);
  assert.ok(used.length >= 3);
  for (const call of used) assert.ok(Object.hasOwn(CALLS, call), call);
  // The page obeys the policy its files are served with: no inline script, nothing from elsewhere.
  assert.equal(/<script(?![^>]*\bsrc=)[^>]*>/.test(page), false);
  assert.equal(/https?:\/\//.test(page + app), false);
});

test("nothing in the frame's code evaluates text, writes markup, opens a dialog or moves the window", () => {
  const dir = new URL("../src/modules/apps/", import.meta.url);
  const files = ["AppFrame.tsx", "useInstalledApps.ts", ...readdirSync(new URL("lib/", dir)).filter((name) => name === "bridge.ts" || name === "installed.ts").map((name) => `lib/${name}`)];
  const sources = [...files.map((name) => readFileSync(new URL(name, dir), "utf8")), read("../../sdk/js/bridge.js")];
  const banned = [/\beval\(/, /new Function\(/, /dangerouslySetInnerHTML/, /\.innerHTML\b/, /\.outerHTML\b/, /insertAdjacentHTML/, /document\.write/, /\balert\(/, /\bconfirm\(/, /\bprompt\(/, /location\.(href|assign|replace)/, /window\.open\(/, /sandbox="[^"]*allow-same-origin/, /setTimeout\(\s*["'`]/];
  for (const source of sources) for (const pattern of banned) assert.equal(pattern.test(source), false, String(pattern));
});

test("AppFrame mounts the frame with the constants, and the browser test mounts it the same way", () => {
  const frame = read("../src/modules/apps/AppFrame.tsx");
  for (const attribute of ["sandbox={FRAME_SANDBOX}", "allow={FRAME_ALLOW}", "referrerPolicy={FRAME_REFERRER_POLICY}", "title={title}"]) assert.ok(frame.includes(attribute), attribute);
  const browser = read("./browser/escape1.html");
  assert.ok(browser.includes(`frame.setAttribute("sandbox", "${FRAME_SANDBOX}")`));
  assert.ok(browser.includes(`frame.setAttribute("allow", "${FRAME_ALLOW}")`));
  assert.ok(browser.includes(`frame.setAttribute("referrerpolicy", "${FRAME_REFERRER_POLICY}")`));
});

test("an argument passed on as undefined is left out, since undefined is not JSON", async () => {
  const seen: [string, unknown][] = [];
  const m = mounted(undefined, {
    "files.picture": (args) => (seen.push(["files.picture", args]), { url: "blob:x" }),
    "files.thumbnail": (args) => (seen.push(["files.thumbnail", args]), { url: "blob:y" }),
  });
  m.handshake();
  try {
    const size: number | undefined = undefined;
    await m.hq.files.picture("ref-1", size);
    await m.hq.files.thumbnail("ref-1", 64);
    assert.deepEqual(seen, [
      ["files.picture", { ref: "ref-1" }],
      ["files.thumbnail", { ref: "ref-1", size: 64 }],
    ]);
  } finally {
    m.close();
  }
});
