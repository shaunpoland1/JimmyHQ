// `hq.files.picture(ref, size?)`: one of the app's own pictures, large, for a full view. It
// is the thumbnail's path with larger limits; what must stay true is that the page cannot
// ask for more pixels, bytes or pictures than the owner allows, nor for anything but its
// own kept pictures.

import assert from "node:assert/strict";
import { test } from "node:test";
import { CALLS, readMount } from "../src/modules/apps/lib/bridge.ts";
import {
  PICTURE_DEFAULT_PX,
  PICTURE_MAX_PX,
  PICTURE_REFUSALS,
  PICTURE_WINDOW_MS,
  clampPicturePx,
  declaredTooHeavy,
  readCapped,
  readPictureArgs,
  takePicture,
} from "../src/modules/apps/lib/picture.ts";
import { MAX_PIXELS, makeThumbnail, readThumbArgs } from "../src/modules/apps/lib/thumbnail.ts";
import type { ThumbEnv } from "../src/modules/apps/lib/thumbnail.ts";

const REF = `uploads/${"b".repeat(32)}.png`;
const MB = 1024 * 1024;

function png(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(33);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  new DataView(bytes.buffer).setUint32(16, width);
  new DataView(bytes.buffer).setUint32(20, height);
  return bytes;
}

function pieces(chunks: Uint8Array[]) {
  let at = 0;
  let cancelled = false;
  return {
    source: {
      read: async () => (at < chunks.length ? { done: false, value: chunks[at++] } : { done: true }),
      cancel: () => {
        cancelled = true;
      },
    },
    wasCancelled: () => cancelled,
    readSoFar: () => at,
  };
}

test("the full view is the shell's own call and never reaches the bridge route", () => {
  assert.equal(CALLS["files.picture"]?.shell, true);
  assert.equal(CALLS["files.picture"]?.needs, undefined);
});

test("a full view asks for a ref and an optional size and nothing else", () => {
  assert.deepEqual(readPictureArgs(REF, 2048), { ok: true, ref: REF, px: 2048 });
  assert.deepEqual(readPictureArgs({ ref: REF, size: 1200 }, 2048), { ok: true, ref: REF, px: 1200 });
  for (const args of [null, 5, [], {}, { ref: "../x.png" }, { ref: "uploads/../../app.db" }, { ref: "app.db" }, { ref: REF, app: "other" }, { ref: REF, url: "https://example.com" }, "https://example.com/a.png", `uploads/${"b".repeat(32)}.svg`]) {
    const read = readPictureArgs(args, 2048);
    assert.equal(read.ok, false, JSON.stringify(args));
    assert.equal(!read.ok && read.error.code, "invalid");
  }
  const bad = readPictureArgs({ ref: REF, size: "huge" }, 2048);
  assert.equal(!bad.ok && bad.error.message, PICTURE_REFUSALS.badSize);
});

test("a page never gets a full view larger than the owner's setting, whatever size it asks for", () => {
  assert.deepEqual(readPictureArgs({ ref: REF, size: 100_000 }, 2048), { ok: true, ref: REF, px: 2048 });
  assert.deepEqual(readPictureArgs({ ref: REF, size: 100_000 }, 100_000), { ok: true, ref: REF, px: PICTURE_MAX_PX });
  assert.deepEqual(readPictureArgs({ ref: REF, size: 1 }, 2048), { ok: true, ref: REF, px: 64 });
  assert.equal(clampPicturePx("2048"), PICTURE_DEFAULT_PX);
  assert.equal(clampPicturePx(Number.NaN), PICTURE_DEFAULT_PX);
});

test("thumbnails keep their own smaller ceiling beside the full view", () => {
  assert.deepEqual(readThumbArgs({ ref: REF, size: 4000 }, 320), { ok: true, ref: REF, px: 320 });
});

test("the full view's limits come from the mount answer, with the server's defaults for an older one", () => {
  const answer = { mount: "m".repeat(43), entry: "index.html", version: "1.0.0", tier: "sandboxed", permissions: [] };
  assert.deepEqual(readMount({ ...answer, picture_px: 4096, picture_max_mb: 3, pictures_per_minute: 7 })?.picture, { px: 4096, maxBytes: 3 * MB, perMinute: 7 });
  assert.deepEqual(readMount(answer)?.picture, { px: 2048, maxBytes: 25 * MB, perMinute: 60 });
  assert.deepEqual(readMount({ ...answer, picture_px: 99_999, picture_max_mb: -1, pictures_per_minute: "lots" })?.picture, { px: PICTURE_MAX_PX, maxBytes: 25 * MB, perMinute: 60 });
});

test("a picture the server says is heavier than the owner allows is not read at all", () => {
  assert.equal(declaredTooHeavy(String(5 * MB + 1), 5 * MB), true);
  assert.equal(declaredTooHeavy(String(5 * MB), 5 * MB), false);
  assert.equal(declaredTooHeavy(null, 5 * MB), false);
  assert.equal(declaredTooHeavy("-3", 5 * MB), false);
});

test("a body that runs past the byte limit is stopped and cancelled, and the page is told what to raise", async () => {
  const many = pieces([new Uint8Array(400), new Uint8Array(400), new Uint8Array(400), new Uint8Array(400)]);
  await assert.rejects(readCapped(many.source, 1000), (error: { code: string; message: string; owner_message?: string }) => {
    assert.equal(error.code, "too_large");
    assert.match(error.message, /Largest picture an app's screen may show in full \(MB\)/);
    assert.ok(error.owner_message);
    return true;
  });
  assert.equal(many.wasCancelled(), true);
  assert.equal(many.readSoFar(), 3);
  const fits = pieces([new Uint8Array(500), new Uint8Array(500)]);
  assert.equal((await readCapped(fits.source, 1000)).length, 2);
});

test("a page that asks for full views in a loop is held to the owner's number a minute", () => {
  let log: number[] = [];
  const start = 1_000_000;
  for (let n = 0; n < 3; n += 1) {
    const taken = takePicture(log, start + n, 3);
    assert.equal(taken.ok, true);
    log = taken.log;
  }
  const refused = takePicture(log, start + 10, 3);
  assert.equal(refused.ok, false);
  assert.equal(!refused.ok && refused.error.code, "quota");
  assert.equal(!refused.ok && refused.error.retry_after_s, 60);
  assert.match(!refused.ok ? refused.error.message : "", /Pictures an app's screen may show in full a minute/);
  // A refusal is not counted, and a minute later there is room again.
  assert.equal(refused.log.length, 3);
  assert.equal(takePicture(refused.log, start + PICTURE_WINDOW_MS + 1, 3).ok, true);
});

test("a full view goes through the same pixel check as a thumbnail, before anything is decoded", async () => {
  let rendered = 0;
  const env: ThumbEnv = {
    render: async () => {
      rendered += 1;
      return new Blob([new Uint8Array(10)], { type: "image/png" });
    },
  };
  const side = Math.ceil(Math.sqrt(MAX_PIXELS)) + 10;
  await assert.rejects(makeThumbnail(new Blob([png(side, side) as never], { type: "image/png" }), 2048, env), { code: "too_large" });
  assert.equal(rendered, 0);
  const large = new Blob([png(6000, 4000) as never, new Uint8Array(500)], { type: "image/png" });
  await makeThumbnail(large, 2048, env);
  assert.equal(rendered, 1);
});
