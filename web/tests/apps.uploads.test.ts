// Pictures from the owner, from the shell's side and from the frame's: what a hand-over
// may be, where it goes, and what the page is told.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The library is plain JavaScript, as app authors receive it.
import { connect } from "../../sdk/js/bridge.js";
import { CALLS, createRelay, parseRequest, readReply } from "../src/modules/apps/lib/bridge.ts";
import type { BridgeResponse } from "../src/modules/apps/lib/bridge.ts";
import {
  BATCH_LIMIT_MS,
  GESTURE_WINDOW_MS,
  NAME_HEADER,
  OWNER_REFUSALS,
  UPLOAD_REFUSALS,
  createQueue,
  gestureAllows,
  isUploadRef,
  picturePath,
  readRefArgs,
  readUploadArgs,
  noGestureRefusal,
  readUploaded,
  uploadAllowed,
  uploadFailure,
  uploadHeaders,
  uploadPath,
} from "../src/modules/apps/lib/uploads.ts";

const REF = `uploads/${"0a".repeat(16)}.jpg`;
const MB = 1024 * 1024;
const picture = (bytes = 10, name = "morning.jpg") => new File([new Uint8Array(bytes)], name, { type: "image/jpeg" });

// --- what a hand-over may be ------------------------------------------------------------------

test("a hand-over is one File in {file} within the size the server takes", () => {
  const file = picture();
  const read = readUploadArgs({ file }, MB);
  assert.equal(read.ok, true);
  assert.equal(read.ok && read.file, file);
});

test("anything but one picked File is refused before anything is sent", () => {
  for (const odd of [null, "uploads/x.jpg", { file: "C:/pictures/x.jpg" }, { file: { name: "x.jpg", size: 1 } }, { file: picture(), app: "other" }, { url: "/api/apps/other/files" }, [picture()], picture()]) {
    const read = readUploadArgs(odd, MB);
    assert.equal(read.ok, false);
    assert.equal(!read.ok && read.error.code, "invalid");
    assert.equal(!read.ok && read.error.message, UPLOAD_REFUSALS.shape);
  }
});

test("a picture over the limit is refused in the shell and never sent", () => {
  const read = readUploadArgs({ file: picture(2 * MB) }, MB);
  assert.equal(!read.ok && read.error.code, "too_large");
  assert.match(!read.ok ? read.error.message : "", /1 MB/);
});

test("the file calls are the shell's own and never reach the bridge route", () => {
  for (const call of ["files.upload", "files.thumbnail", "files.picking"]) assert.equal(CALLS[call]?.shell, true);
  // A File cannot be JSON, so only this call takes one; every other call still refuses it.
  assert.equal(parseRequest({ id: 1, call: "files.upload", args: { file: picture() } }).ok, true);
  assert.equal(parseRequest({ id: 2, call: "storage.set", args: { key: "k", value: picture() } }).ok, false);
});

// --- where it goes ----------------------------------------------------------------------------

test("a picture goes only to the mounted app's own address, with its name percent-encoded", () => {
  assert.equal(uploadPath("water"), "/api/apps/water/files");
  for (const odd of ["../settings", "Water", "water/../x", "_installed", ""]) assert.equal(uploadPath(odd), null);
  const headers = uploadHeaders('a"b\r\nX-Evil: 1/../é.jpg');
  assert.equal(headers[NAME_HEADER], encodeURIComponent('a"b\r\nX-Evil: 1/../é.jpg'));
  assert.doesNotMatch(headers[NAME_HEADER] ?? "", /[\r\n"]/);
});

test("a picture is shown only by a ref an upload handed out, for the mounted app", () => {
  assert.equal(picturePath("water", REF), `/api/apps/water/files/${"0a".repeat(16)}.jpg`);
  for (const odd of ["uploads/../app.db", "app.db", `/${REF}`, `${REF}?x=1`, REF.replace(".jpg", ".svg"), REF.toUpperCase(), null, 5]) {
    assert.equal(picturePath("water", odd), null);
  }
  assert.equal(picturePath("../other", REF), null);
  assert.equal(readRefArgs({ ref: REF }), REF);
  assert.equal(readRefArgs(REF), REF);
  assert.equal(readRefArgs({ ref: REF, app: "other" }), null);
  assert.equal(isUploadRef("uploads/x.jpg"), false);
});

// --- what the page is told --------------------------------------------------------------------

test("the page is told the ref, name, size and type, and nothing else the server said", () => {
  const got = readUploaded({ ref: REF, name: "morning.jpg", size: 10, type: "image/jpeg", path: "/srv/x", extra: 1 });
  assert.deepEqual(got, { ref: REF, name: "morning.jpg", size: 10, type: "image/jpeg" });
  for (const odd of [null, { ref: "x", name: "a", size: 1, type: "image/jpeg" }, { ref: REF, name: "a", size: -1, type: "image/jpeg" }, { ref: REF, name: "a", size: 1, type: "text/html" }]) {
    assert.equal(readUploaded(odd), null);
  }
});

test("a refused upload keeps the server's sentence under the right code", () => {
  assert.equal(uploadFailure(413, "Too big.").code, "too_large");
  assert.equal(uploadFailure(507, "Full.").code, "quota");
  assert.equal(uploadFailure(415, "Not a picture.").code, "invalid");
  assert.equal(uploadFailure(422, "Bad name.").code, "invalid");
  assert.equal(uploadFailure(403, "Not yours.").code, "not_allowed");
  assert.equal(uploadFailure(409, "Busy.").code, "unavailable");
  assert.equal(uploadFailure(415, "Not a picture.").message, "Not a picture.");
});

// --- the owner's touch ------------------------------------------------------------------------

test("a hand-over with no touch of the owner's at all is refused where the browser can tell", () => {
  assert.equal(gestureAllows({ isActive: false, hasBeenActive: false }, null, 1000), false);
  assert.equal(gestureAllows({ isActive: false, hasBeenActive: true }, null, 1000), false);
});

test("a hand-over just after the owner's touch, or during it, goes ahead", () => {
  assert.equal(gestureAllows({ isActive: true, hasBeenActive: true }, null, 1000), true);
  const now = 10 * GESTURE_WINDOW_MS;
  assert.equal(gestureAllows({ isActive: false, hasBeenActive: true }, now - 5000, now), true);
  assert.equal(gestureAllows({ isActive: false, hasBeenActive: true }, now - GESTURE_WINDOW_MS - 1, now), false);
  // A clock that went back is not a touch in the future.
  assert.equal(gestureAllows({ isActive: false, hasBeenActive: true }, now + 5000, now), false);
});

test("where the browser cannot say, the shell cannot tell, and the upload goes ahead", () => {
  assert.equal(gestureAllows(null, null, 0), true);
  assert.equal(gestureAllows(undefined, null, 0), true);
});

// --- a batch over a slow link -----------------------------------------------------------------

const IDLE = { isActive: false, hasBeenActive: true };
const MIN = 60 * 1000;

test("a picture that arrived safely lets the next one of the batch through after the first window has closed", () => {
  const touch = 1_000_000;
  // The first picture took three minutes on a slow link; the second is handed over right after.
  assert.equal(gestureAllows(IDLE, touch, touch + 3 * MIN), false);
  assert.equal(uploadAllowed(IDLE, touch, touch + 3 * MIN, touch + 3 * MIN + 500), true);
  // And so on down the batch, each renewed by the one before.
  assert.equal(uploadAllowed(IDLE, touch, touch + 6 * MIN, touch + 6 * MIN + 500), true);
});

test("without the owner's touch no arrival opens the window, whatever the page claims", () => {
  const now = 5_000_000;
  assert.equal(uploadAllowed(IDLE, null, now - 1000, now), false);
  assert.equal(uploadAllowed({ isActive: false, hasBeenActive: false }, null, now - 1000, now), false);
  assert.equal(uploadAllowed({ isActive: false, hasBeenActive: false }, now - 1000, now - 500, now), false);
});

test("a run of uploads cannot outlive its limit after the touch, however many arrive", () => {
  const touch = 1_000_000;
  // A page that keeps one upload after another going from a single touch.
  let renewed: number | null = null;
  let lastAllowed = touch;
  for (let now = touch + 1000; now <= touch + 60 * MIN; now += 30 * 1000) {
    if (!uploadAllowed(IDLE, touch, renewed, now)) break;
    renewed = now;
    lastAllowed = now;
  }
  assert.ok(lastAllowed - touch <= BATCH_LIMIT_MS);
  assert.equal(uploadAllowed(IDLE, touch, touch + BATCH_LIMIT_MS, touch + BATCH_LIMIT_MS + 1), false);
  // A new touch opens it again.
  assert.equal(uploadAllowed(IDLE, touch + 20 * MIN, null, touch + 20 * MIN + 1000), true);
});

test("a renewal lasts no longer than the touch's own window", () => {
  const touch = 1_000_000;
  const arrived = touch + 3 * MIN;
  assert.equal(uploadAllowed(IDLE, touch, arrived, arrived + GESTURE_WINDOW_MS), true);
  assert.equal(uploadAllowed(IDLE, touch, arrived, arrived + GESTURE_WINDOW_MS + 1), false);
});

test("an arrival from before the touch, or dated in the future, renews nothing", () => {
  const touch = 1_000_000;
  const now = touch + 3 * MIN;
  assert.equal(uploadAllowed(IDLE, touch, touch - 1, now), false);
  assert.equal(uploadAllowed(IDLE, touch, now + 5000, now), false);
});

test("the renewal never refuses what the touch alone allows", () => {
  const now = 9_000_000;
  for (const activation of [null, undefined, { isActive: true, hasBeenActive: true }]) {
    assert.equal(uploadAllowed(activation, null, null, now), gestureAllows(activation, null, now));
  }
  assert.equal(uploadAllowed(IDLE, now - 5000, null, now), true);
  assert.equal(uploadAllowed(IDLE, now - GESTURE_WINDOW_MS - 1, null, now), false);
});

test("a hand-over refused for want of a touch says so to the app and, in plain words, to the owner", () => {
  const refusal = noGestureRefusal();
  assert.equal(refusal.code, "not_allowed");
  // What existing apps read is unchanged.
  assert.equal(refusal.message, UPLOAD_REFUSALS.noGesture);
  assert.equal(refusal.owner_message, OWNER_REFUSALS.noGesture);
  assert.ok(!/hq\.|the owner|your page/i.test(refusal.owner_message ?? ""));
  assert.match(refusal.owner_message ?? "", /not added.*Choose it again/);
});

test("the owner's sentence reaches the page beside the app's, from the shell or JimmyHQ's server and never from the frame", async () => {
  const posted: BridgeResponse[] = [];
  const relay = createRelay({
    appId: "posts",
    grant: { tier: "sandboxed", permissions: [] },
    send: async () => ({ ok: false, error: { code: "not_allowed", message: "From the server.", owner_message: "The server may not set this." } }),
    post: (response) => posted.push(response),
    shell: {
      "files.upload": () => {
        throw noGestureRefusal();
      },
    },
  });
  await relay.handle({ id: 1, call: "files.upload", args: { file: picture() } });
  assert.deepEqual(posted[0], { id: 1, ok: false, error: { code: "not_allowed", message: UPLOAD_REFUSALS.noGesture, owner_message: OWNER_REFUSALS.noGesture } });
  await relay.handle({ id: 2, call: "storage.get", args: { key: "k" } });
  const second = posted[1];
  assert.ok(second && !second.ok);
  // JimmyHQ's own server may give one (it wrote it): it reaches the page, cut to a sentence's length.
  assert.deepEqual(second.error, { code: "not_allowed", message: "From the server.", owner_message: "The server may not set this." });
});

test("an owner sentence from the server is kept only as text of a sensible length, and a refusal without one has none", () => {
  const long = readReply({ ok: false, error: { code: "unavailable", message: "m", owner_message: "w".repeat(900) } });
  assert.ok(!long.ok && (long.error.owner_message ?? "").length === 500);
  for (const odd of [5, null, "", {}, ["x"]]) {
    const read = readReply({ ok: false, error: { code: "unavailable", message: "m", owner_message: odd } });
    assert.ok(!read.ok && !("owner_message" in read.error));
  }
});

test("uploads from one frame are sent one after another, a failure included", async () => {
  const enqueue = createQueue();
  const order: string[] = [];
  const slow = (name: string, ms: number, fail = false) => () =>
    new Promise<string>((resolve, reject) =>
      setTimeout(() => {
        order.push(name);
        if (fail) reject(new Error(name));
        else resolve(name);
      }, ms),
    );
  const results = await Promise.allSettled([enqueue(slow("a", 20, true)), enqueue(slow("b", 1)), enqueue(slow("c", 5))]);
  assert.deepEqual(order, ["a", "b", "c"]);
  assert.deepEqual(results.map((r) => r.status), ["rejected", "fulfilled", "fulfilled"]);
});

// --- the relay and the page, joined -----------------------------------------------------------

test("the relay waits for the shell's upload and posts back what it resolved to", async () => {
  const posted: BridgeResponse[] = [];
  const sentToServer: string[] = [];
  const relay = createRelay({
    appId: "water",
    grant: { tier: "sandboxed", permissions: [] },
    send: async (_appId, call) => {
      sentToServer.push(call);
      return { ok: true, result: null };
    },
    post: (response) => posted.push(response),
    shell: {
      "files.upload": async (args) => {
        const read = readUploadArgs(args, MB);
        if (!read.ok) throw read.error;
        return { ref: REF, name: read.file.name, size: read.file.size, type: "image/jpeg" };
      },
    },
  });
  await relay.handle({ id: 1, call: "files.upload", args: { file: picture(7) } });
  await relay.handle({ id: 2, call: "files.upload", args: { file: "/etc/passwd" } });
  assert.deepEqual(posted[0], { id: 1, ok: true, result: { ref: REF, name: "morning.jpg", size: 7, type: "image/jpeg" } });
  assert.equal(posted[1]?.ok, false);
  assert.deepEqual(sentToServer, []);
});

test("the page's hq.files.upload(file) sends the File itself as {file}", () => {
  const sent: unknown[] = [];
  const listeners: ((event: unknown) => void)[] = [];
  const win = {
    parent: { postMessage: () => undefined },
    document: { documentElement: { dataset: {} }, addEventListener: () => undefined },
    addEventListener: (type: string, fn: (event: unknown) => void) => {
      if (type === "message") listeners.push(fn);
    },
  };
  const hq = connect(win);
  const port = { postMessage: (message: unknown) => sent.push(message), onmessage: null };
  listeners.forEach((fn) => fn({ source: win.parent, data: { jimmyhq: 1 }, ports: [port] }));
  const file = picture();
  void hq.files.upload(file);
  void hq.files.thumbnail(REF);
  const [upload, thumbnail] = sent as { call: string; args: Record<string, unknown> }[];
  assert.equal(upload?.call, "files.upload");
  assert.equal(upload?.args.file, file);
  assert.deepEqual(thumbnail?.args, { ref: REF });
});

test("the page's Error for a refused upload carries the owner's sentence beside the app's own", async () => {
  const listeners: ((event: unknown) => void)[] = [];
  const win = {
    parent: { postMessage: () => undefined },
    document: { documentElement: { dataset: {} }, addEventListener: () => undefined },
    addEventListener: (type: string, fn: (event: unknown) => void) => {
      if (type === "message") listeners.push(fn);
    },
  };
  const hq = connect(win);
  const port: { postMessage: (message: { id: number }) => void; onmessage: null | ((reply: { data: unknown }) => void) } = {
    postMessage: (message) => queueMicrotask(() => port.onmessage?.({ data: { id: message.id, ok: false, error: noGestureRefusal() } })),
    onmessage: null,
  };
  listeners.forEach((fn) => fn({ source: win.parent, data: { jimmyhq: 1 }, ports: [port] }));
  await assert.rejects(hq.files.upload(picture()), (error: Error & { code?: string; owner_message?: string }) => {
    assert.equal(error.code, "not_allowed");
    assert.equal(error.message, UPLOAD_REFUSALS.noGesture);
    assert.equal(error.owner_message, OWNER_REFUSALS.noGesture);
    return true;
  });
});
