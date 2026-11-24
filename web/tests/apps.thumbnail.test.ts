// `hq.files.thumbnail(ref, size?)`: a small picture, made in the shell with a canvas. The
// browser is faked; what must stay true is when it is asked, and what the page gets back.

import assert from "node:assert/strict";
import { test } from "node:test";
import { CALLS } from "../src/modules/apps/lib/bridge.ts";
import {
  MAX_PIXELS,
  MAX_SIDE,
  THUMB_DEFAULT_PX,
  THUMB_MAX_PX,
  THUMB_MIN_PX,
  THUMB_REFUSALS,
  clampPx,
  fitWithin,
  makeThumbnail,
  readThumbArgs,
  sniffSize,
} from "../src/modules/apps/lib/thumbnail.ts";
import type { ThumbEnv } from "../src/modules/apps/lib/thumbnail.ts";

const REF = `uploads/${"a".repeat(32)}.png`;

function png(width: number, height: number, filler = 0): Uint8Array {
  const bytes = new Uint8Array(33 + filler);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  new DataView(bytes.buffer).setUint32(16, width);
  new DataView(bytes.buffer).setUint32(20, height);
  return bytes;
}

const le = (n: number) => [n & 255, (n >> 8) & 255];

/** A GIF's first bytes: header, screen, an optional colour table and blocks, then the first image descriptor. */
function gifBytes(screen: [number, number], frame: [number, number, number, number], { table = 0, blocks = [] as number[] } = {}): Uint8Array {
  const packed = table ? 0x80 | (table - 1) : 0;
  const colours = table ? new Array<number>(3 * (1 << table)).fill(7) : [];
  return Uint8Array.from([
    ...[..."GIF89a"].map((c) => c.charCodeAt(0)), ...le(screen[0]), ...le(screen[1]), packed, 0, 0,
    ...colours, ...blocks,
    0x2c, ...le(frame[0]), ...le(frame[1]), ...le(frame[2]), ...le(frame[3]), 0, 2, 0,
  ]);
}

function gif(width: number, height: number): Uint8Array {
  return gifBytes([width, height], [0, 0, width, height]);
}

// A graphic control extension, a comment in two sub-blocks, and a looping application block.
const EXTENSIONS = [
  0x21, 0xf9, 4, 0, 10, 0, 0, 0,
  0x21, 0xfe, 3, 0x61, 0x62, 0x63, 2, 0x2c, 0x21, 0,
  0x21, 0xff, 11, ...[..."NETSCAPE2.0"].map((c) => c.charCodeAt(0)), 3, 1, 0, 0, 0,
];

function jpeg(width: number, height: number, exifLength = 40): Uint8Array {
  const parts = [0xff, 0xd8, 0xff, 0xe1, (exifLength + 2) >> 8, (exifLength + 2) & 255, ...new Array<number>(exifLength).fill(0)];
  parts.push(0xff, 0xc0, 0, 17, 8, height >> 8, height & 255, width >> 8, width & 255, 3, 0, 0, 0, 0, 0, 0, 0, 0);
  return Uint8Array.from(parts);
}

function webpLossy(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(40);
  bytes.set([..."RIFF"].map((c) => c.charCodeAt(0)), 0);
  bytes.set([..."WEBPVP8 "].map((c) => c.charCodeAt(0)), 8);
  new DataView(bytes.buffer).setUint16(26, width, true);
  new DataView(bytes.buffer).setUint16(28, height, true);
  return bytes;
}

function webpExtended(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(40);
  bytes.set([..."RIFF"].map((c) => c.charCodeAt(0)), 0);
  bytes.set([..."WEBPVP8X"].map((c) => c.charCodeAt(0)), 8);
  const w = width - 1;
  const h = height - 1;
  bytes.set([w & 255, (w >> 8) & 255, (w >> 16) & 255, h & 255, (h >> 8) & 255, (h >> 16) & 255], 24);
  return bytes;
}

function picture(bytes: Uint8Array, type: string, extra = 0): Blob {
  return new Blob([bytes as never, new Uint8Array(extra)], { type });
}

function fakeEnv(result: () => Blob | null | Promise<Blob | null> | never) {
  const calls: Array<{ px: number; type: string }> = [];
  const env: ThumbEnv = {
    render: async (_blob, px, type) => {
      calls.push({ px, type });
      return result();
    },
  };
  return { env, calls };
}

test("the thumbnail call is the shell's own and never reaches the bridge route", () => {
  assert.equal(CALLS["files.thumbnail"]?.shell, true);
});

test("a thumbnail asks for a ref and an optional size and nothing else", () => {
  assert.deepEqual(readThumbArgs(REF, 320), { ok: true, ref: REF, px: 320 });
  assert.deepEqual(readThumbArgs({ ref: REF }, 320), { ok: true, ref: REF, px: 320 });
  assert.deepEqual(readThumbArgs({ ref: REF, size: 120 }, 320), { ok: true, ref: REF, px: 120 });
  for (const args of [null, 5, [], {}, { ref: "../x.png" }, { ref: "uploads/../../x.png" }, { ref: REF, app: "other" }, { ref: REF, url: "https://example.com" }, "https://example.com/a.png"]) {
    const read = readThumbArgs(args, 320);
    assert.equal(read.ok, false, JSON.stringify(args));
    assert.equal(!read.ok && read.error.code, "invalid");
  }
  const bad = readThumbArgs({ ref: REF, size: "big" }, 320);
  assert.equal(!bad.ok && bad.error.message, THUMB_REFUSALS.badSize);
  assert.equal(readThumbArgs({ ref: REF, size: Number.NaN }, 320).ok, false);
});

test("a size is kept within the bounds and never above what the owner's setting allows", () => {
  assert.equal(readThumbArgs({ ref: REF, size: 1 }, 320).ok && (readThumbArgs({ ref: REF, size: 1 }, 320) as { px: number }).px, THUMB_MIN_PX);
  const big = readThumbArgs({ ref: REF, size: 99_999 }, 320);
  assert.equal(big.ok && big.px, 320);
  const wide = readThumbArgs({ ref: REF, size: 99_999 }, 99_999);
  assert.equal(wide.ok && wide.px, THUMB_MAX_PX);
  const half = readThumbArgs({ ref: REF, size: 100.9 }, 500);
  assert.equal(half.ok && half.px, 100);
  assert.equal(clampPx(undefined), THUMB_DEFAULT_PX);
  assert.equal(clampPx("x"), THUMB_DEFAULT_PX);
  assert.equal(clampPx(1), THUMB_MIN_PX);
  assert.equal(clampPx(1e9), THUMB_MAX_PX);
});

test("the size of a picture is read from its first bytes", () => {
  assert.deepEqual(sniffSize(png(4000, 3000)), { width: 4000, height: 3000 });
  assert.deepEqual(sniffSize(gif(640, 480)), { width: 640, height: 480 });
  assert.deepEqual(sniffSize(jpeg(1920, 1080)), { width: 1920, height: 1080 });
  assert.deepEqual(sniffSize(jpeg(1920, 1080, 5000)), { width: 1920, height: 1080 });
  assert.deepEqual(sniffSize(webpLossy(800, 600)), { width: 800, height: 600 });
  assert.deepEqual(sniffSize(webpExtended(20_000, 10)), { width: 20_000, height: 10 });
});

test("a file that is cut short, junk, or a kind not read here has no size", () => {
  assert.equal(sniffSize(new Uint8Array(0)), null);
  assert.equal(sniffSize(png(10, 10).subarray(0, 20)), null);
  assert.equal(sniffSize(jpeg(10, 10).subarray(0, 30)), null);
  assert.equal(sniffSize(Uint8Array.from([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])), null);
  assert.equal(sniffSize(new TextEncoder().encode("<svg xmlns='http://www.w3.org/2000/svg'></svg>")), null);
  // A JPEG whose segment lengths run past the end does not loop.
  assert.equal(sniffSize(Uint8Array.from([0xff, 0xd8, 0xff, 0xe1, 0xff, 0xff, 0, 0])), null);
});

test("a picture is brought within the size in proportion and never made larger", () => {
  assert.deepEqual(fitWithin(4000, 3000, 320), { width: 320, height: 240 });
  assert.deepEqual(fitWithin(3000, 4000, 320), { width: 240, height: 320 });
  assert.deepEqual(fitWithin(100, 50, 320), { width: 100, height: 50 });
  assert.deepEqual(fitWithin(20_000, 10, 320), { width: 320, height: 1 });
  assert.deepEqual(fitWithin(0, 0, 320), { width: 1, height: 1 });
});

test("a large picture comes back small, a JPEG as a JPEG and the rest as PNG", async () => {
  const small = new Blob(["tiny"], { type: "image/png" });
  const { env, calls } = fakeEnv(() => small);
  assert.equal(await makeThumbnail(picture(png(4000, 3000), "image/png", 5000), 320, env), small);
  assert.equal(await makeThumbnail(picture(jpeg(4000, 3000), "image/jpeg", 5000), 320, env), small);
  assert.equal(await makeThumbnail(picture(webpLossy(4000, 3000), "image/webp", 5000), 200, env), small);
  assert.deepEqual(calls, [{ px: 320, type: "image/png" }, { px: 320, type: "image/jpeg" }, { px: 200, type: "image/png" }]);
});

test("a picture already small enough is handed back as it is and nothing is decoded", async () => {
  const { env, calls } = fakeEnv(() => new Blob(["x"]));
  const original = picture(png(300, 200), "image/png", 5000);
  assert.equal(await makeThumbnail(original, 320, env), original);
  assert.equal(calls.length, 0);
});

test("a GIF is judged by the larger of its screen and its first frame, so a small screen cannot hide a huge frame", async () => {
  assert.deepEqual(sniffSize(gifBytes([10, 10], [0, 0, 60_000, 60_000])), { width: 60_000, height: 60_000 });
  assert.deepEqual(sniffSize(gifBytes([10, 10], [30_000, 5, 5_000, 20])), { width: 35_000, height: 25 });
  assert.deepEqual(sniffSize(gifBytes([640, 480], [0, 0, 20, 20])), { width: 640, height: 480 });
  let asked = 0;
  const env: ThumbEnv = {
    render: async () => {
      asked += 1;
      return new Blob(["x"]);
    },
  };
  for (const bomb of [gifBytes([10, 10], [0, 0, 60_000, 60_000]), gifBytes([1, 1], [0, 0, 10_000, 10_000], { table: 8, blocks: EXTENSIONS })]) {
    await assert.rejects(makeThumbnail(picture(bomb, "image/gif", 500), 320, env), (e: { code?: string }) => e.code === "too_large");
  }
  assert.equal(asked, 0);
});

test("a GIF's colour table and extension blocks are skipped to reach its first frame", () => {
  for (const table of [1, 2, 8]) {
    assert.deepEqual(sniffSize(gifBytes([100, 50], [0, 0, 300, 40], { table, blocks: EXTENSIONS })), { width: 300, height: 50 }, String(table));
  }
  // A comment whose bytes look like an image descriptor is still skipped as a comment.
  assert.deepEqual(sniffSize(gifBytes([8, 8], [0, 0, 9, 9], { blocks: [0x21, 0xfe, 10, 0x2c, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0, 0] })), { width: 9, height: 9 });
});

test("a GIF whose blocks cannot be read up to its first frame is handed back undecoded", async () => {
  const good = gifBytes([100, 100], [0, 0, 100, 100], { table: 2, blocks: EXTENSIONS });
  const unreadable = [
    good.subarray(0, 13 + 6), // cut short inside the colour table
    good.subarray(0, good.length - 5), // cut short inside the image descriptor
    Uint8Array.from([...good.subarray(0, 13 + 12), 0x3b]), // the trailer before any image
    Uint8Array.from([...good.subarray(0, 13 + 12), 0x00, 0x2c]), // a byte that starts no block
    Uint8Array.from([...good.subarray(0, 13 + 12), 0x21, 0xfe, 200, 1, 2]), // a sub-block past the end
    Uint8Array.from([...good.subarray(0, 13 + 12), 0x21, 0xfe, 3, 1, 2, 3]), // no empty sub-block
  ];
  let asked = 0;
  const env: ThumbEnv = {
    render: async () => {
      asked += 1;
      return new Blob(["x"]);
    },
  };
  for (const bytes of unreadable) {
    assert.equal(sniffSize(bytes), null);
    const blob = picture(bytes, "image/gif");
    assert.equal(await makeThumbnail(blob, 320, env), blob);
  }
  assert.equal(asked, 0);
});

test("an animated GIF comes back as one frame even when it is small", async () => {
  const frame = new Blob(["f"], { type: "image/png" });
  const { env, calls } = fakeEnv(() => frame);
  assert.equal(await makeThumbnail(picture(gif(100, 100), "image/gif", 9000), 320, env), frame);
  assert.equal(await makeThumbnail(picture(gif(900, 900), "image/gif", 9000), 320, env), frame);
  assert.equal(calls.length, 2);
});

test("a picture with too many pixels is refused before anything decodes it", async () => {
  const { env, calls } = fakeEnv(() => new Blob(["x"]));
  // A few hundred bytes that say they are 100000 by 100000: the shape of a decompression bomb.
  for (const bomb of [png(100_000, 100_000), png(MAX_SIDE + 1, 1), png(10_000, 10_000), jpeg(65_535, 65_535), gif(65_535, 65_535)]) {
    await assert.rejects(makeThumbnail(picture(bomb, "image/png"), 320, env), (error: { code: string; message: string }) => error.code === "too_large" && error.message === THUMB_REFUSALS.tooBig);
  }
  assert.equal(calls.length, 0);
  const edge = Math.floor(Math.sqrt(MAX_PIXELS));
  const fine = new Blob(["ok"], { type: "image/png" });
  const allowed = fakeEnv(() => fine);
  assert.equal(await makeThumbnail(picture(png(edge, edge), "image/png", 5000), 320, allowed.env), fine);
});

test("a kind whose size is not read here is handed back as it is, never decoded", async () => {
  const { env, calls } = fakeEnv(() => new Blob(["x"]));
  for (const type of ["image/avif", "image/heic"]) {
    const original = new Blob([new Uint8Array(4000).fill(7)], { type });
    assert.equal(await makeThumbnail(original, 320, env), original);
  }
  assert.equal(calls.length, 0);
});

test("when the browser cannot decode it, or the small one is not smaller, the page gets the original", async () => {
  const original = picture(png(4000, 3000), "image/png", 100);
  const broken = fakeEnv(() => {
    throw new Error("cannot decode");
  });
  assert.equal(await makeThumbnail(original, 320, broken.env), original);
  assert.equal(await makeThumbnail(original, 320, fakeEnv(() => null).env), original);
  assert.equal(await makeThumbnail(original, 320, fakeEnv(() => new Blob([])).env), original);
  assert.equal(await makeThumbnail(original, 320, fakeEnv(() => new Blob([new Uint8Array(100_000)])).env), original);
});
