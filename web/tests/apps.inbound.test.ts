// Messages from the owner's devices on a full app's page: where requests go, the address a
// device is shown, what is read back (never a key, except from the answer that makes one),
// and the sentences the owner reads.

import assert from "node:assert/strict";
import { test } from "node:test";
import { ADDRESS_WARNING, closedReason, countsLine, inboundPath, keyAddress, keyPath, labelProblem, readInbound, readMadeKey, removeQuestion, sendAddress, sizeNote } from "../src/modules/apps/lib/inbound.ts";

const KEY_ID = "01J9ZK3V7T8Q2W4E6R8T0Y2X4H";
const SENT = {
  declared: [{ name: "event", label: "Home events", help: "What your home says happened." }],
  granted: true,
  everywhere: true,
  open: true,
  path: "/api/in/lights/",
  keys: [{ id: KEY_ID, label: "Home hub", created: 1800000000000, last_used: null, uses: 0 }],
  keys_max: 5,
  received: 3,
  failed: 1,
  last_error: "The app could not do that.",
  last_error_at: 1800000000500,
  waiting: 2,
};

test("requests go only to one app's own inbound address, and a key only by its id", () => {
  assert.equal(inboundPath("lights"), "/api/apps/lights/inbound");
  assert.equal(keyPath("lights", KEY_ID), `/api/apps/lights/inbound/keys/${KEY_ID}`);
  for (const id of ["../x", "", "01j9zk3v7t8q2w4e6r8t0y2x4h", `${KEY_ID}/x`]) assert.equal(keyPath("lights", id), null, id);
  assert.equal(inboundPath("_installed"), null);
  assert.equal(inboundPath("Lights"), null);
});

test("the address a device is shown is the page's own origin with the app's path", () => {
  assert.equal(sendAddress("https://hq.example.com", "lights", "event"), "https://hq.example.com/api/in/lights/event");
  assert.equal(sendAddress("http://192.0.2.4:8440", "lights", "event"), "http://192.0.2.4:8440/api/in/lights/event");
  assert.equal(sendAddress("https://hq.example.com/some/page?x=1", "lights", "event"), "https://hq.example.com/api/in/lights/event");
  assert.equal(sendAddress("https://hq.example.com", "lights", "../x"), null);
  assert.equal(sendAddress("ftp://hq.example.com", "lights", "event"), null);
  assert.equal(sendAddress("not a url", "lights", "event"), null);
});

test("what the server sends is read defensively and a key never appears in it", () => {
  const read = readInbound({ ...SENT, key: "sk-test-SECRET-0123456789", keys: [...SENT.keys, { id: "bad", label: "x" }, "junk"] });
  assert.deepEqual(read.keys, [{ id: KEY_ID, label: "Home hub", created: 1800000000000, lastUsed: null, uses: 0, inAddress: false }]);
  assert.equal(JSON.stringify(read).includes("SECRET"), false);
  assert.equal(read.keysMax, 5);
  assert.equal(read.lastError, "The app could not do that.");
  assert.deepEqual(readInbound(null).declared, []);
  assert.deepEqual(readInbound({ declared: [{ name: "Bad Name" }, { name: "event" }] }).declared, [{ name: "event", label: "event", help: "", array: false, maxKb: null }]);
  assert.equal(readInbound({ path: "https://elsewhere.example.com/" }).path, "");
});

test("the answer that makes a key is the one place a key is read from", () => {
  const made = readMadeKey({ inbound: SENT, key: "sk-test-SECRET-0123456789" });
  assert.equal(made.key, "sk-test-SECRET-0123456789");
  assert.equal(made.inbound.keys.length, 1);
  assert.equal(readMadeKey({ inbound: SENT }).key, "");
});

test("a key's name is checked before it is sent", () => {
  const state = readInbound(SENT);
  assert.match(labelProblem("   ", state) ?? "", /Give the key a name/);
  assert.match(labelProblem("x".repeat(61), state) ?? "", /at most 60/);
  assert.match(labelProblem(" home  HUB ", state) ?? "", /already has a key called “home HUB”/);
  assert.equal(labelProblem("Doorbell", state), null);
  assert.match(labelProblem("Doorbell", { ...state, keysMax: 1 }) ?? "", /the most it may have/);
});

test("the page says why nothing gets in, and how many came", () => {
  const state = readInbound(SENT);
  assert.equal(closedReason(state), null);
  assert.match(closedReason({ ...state, open: false }) ?? "", /until you switch it on/);
  assert.match(closedReason({ ...state, open: false }) ?? "", /switched off when the app was removed, or by you\. Its keys still work once you switch it on/);
  assert.match(closedReason({ ...state, keys: [] }) ?? "", /make a key/);
  assert.match(closedReason({ ...state, everywhere: false }) ?? "", /every app/);
  assert.match(closedReason({ ...state, granted: false }) ?? "", /was not given/);
  assert.equal(countsLine(state), "3 messages taken, 1 the app failed on, 2 waiting for the app.");
  assert.equal(countsLine({ ...state, received: 1, failed: 0, waiting: 0 }), "1 message taken.");
  assert.match(removeQuestion("Lights", "Home hub").message, /refused from now on/);
});

test("a key made for an address comes with each name's address, and only the right ones are kept", () => {
  const key = `jhqi_${"A".repeat(43)}`;
  const made = readMadeKey({
    inbound: SENT,
    key,
    paths: { event: `/api/in/lights/event/k/${key}`, "Bad Name": `/api/in/lights/x/k/${key}`, other: "/api/in/lights/other/k/somethingelse" },
  });
  assert.deepEqual(made.paths, [{ name: "event", path: `/api/in/lights/event/k/${key}` }]);
  assert.deepEqual(readMadeKey({ inbound: SENT, key }).paths, []);
  assert.equal(keyAddress("https://hq.example.com/apps/lights", "lights", "event", key, `/api/in/lights/event/k/${key}`), `https://hq.example.com/api/in/lights/event/k/${key}`);
  assert.equal(keyAddress("https://hq.example.com", "lights", "event", key, `/api/in/other/event/k/${key}`), null);
  assert.equal(keyAddress("https://hq.example.com", "lights", "event", "a/b", "/api/in/lights/event/k/a/b"), null);
  assert.match(ADDRESS_WARNING, /Anyone who sees this address can send/);
});

test("a key, a name's list and size, and the owner's limit are read from what the server sent", () => {
  const read = readInbound({
    ...SENT,
    max_kb: 16,
    declared: [...SENT.declared, { name: "talk", label: "Talks", array: true, max_kb: 256 }, { name: "odd", label: "Odd", max_kb: 0 }],
    keys: [{ ...SENT.keys[0], in_address: true }],
  });
  assert.equal(read.maxKb, 16);
  assert.deepEqual(
    read.declared.map((item) => [item.name, item.array, item.maxKb]),
    [["event", false, null], ["talk", true, 256], ["odd", false, null]],
  );
  assert.deepEqual(read.keys.map((item) => item.inAddress), [true]);
  assert.deepEqual(readInbound(SENT).keys.map((item) => item.inAddress), [false]);
  const [plain, talk] = read.declared;
  assert.ok(plain && talk);
  assert.match(sizeNote(talk, 16) ?? "", /up to 256 KB, and you allow 16 KB.*Settings, Apps/);
  assert.equal(sizeNote({ ...talk, maxKb: 8 }, 16), "At most 8 KB a message.");
  assert.equal(sizeNote(plain, 16), null);
});
