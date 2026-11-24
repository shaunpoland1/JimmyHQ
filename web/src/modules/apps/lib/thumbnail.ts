// `hq.files.thumbnail(ref, size?)`: a small picture, made in the shell.
//
// The core has no image library, so the shell makes the small version: it fetches the
// owner's picture as before (`GET /api/apps/<id>/files/<file>`), and, before handing the
// Blob to the frame, downscales it with a canvas. Nothing is kept: the picture in the
// app's storage is untouched and the small one is made again each time the page asks (the
// page keeps its own Blob URL, as Articles and Posts do). Framework-free apart from
// `browserThumbEnv`, so the rules are tested with fakes (apps.thumbnail.test.ts).
//
// Safety, in this order:
//   1. The picture's size in pixels is read from its first bytes (PNG, JPEG, GIF, WebP)
//      before anything is decoded. One with too many pixels is refused, so a small file
//      that unpacks to a huge picture (a decompression bomb) is never decoded. A kind
//      whose size is not read here (AVIF, HEIC) is handed back as it is, never decoded.
//      A GIF is judged by the larger of its screen and its first frame (a frame may claim
//      more than the screen), and one whose blocks cannot be read up to that frame is
//      handed back undecoded too.
//   2. A picture already within the size is handed back as it is, except a GIF, which is
//      redrawn as its first frame (a canvas draws only that), so a page never gets an
//      animation where it asked for a still.
//   3. The orientation the picture's file says is respected (`from-image`).
//   4. If the browser cannot decode or encode it, or the small version is not smaller,
//      the original is handed back: a page always gets a picture it can show.

import type { BridgeError } from "./bridge.ts";
import { isUploadRef } from "./uploads.ts";

/** What the server's `apps.thumbnail_px` is when an older one says nothing. */
export const THUMB_DEFAULT_PX = 320;
export const THUMB_MIN_PX = 64;
export const THUMB_MAX_PX = 2048;
/** Most pixels (width times height) a picture may have to be made small: 64 megapixels. */
export const MAX_PIXELS = 64_000_000;
/** Longest side a picture may have, so a thin strip cannot hide inside the pixel count. */
export const MAX_SIDE = 32_768;
/** How much of the picture's start is read to learn its size. */
export const HEAD_BYTES = 1024 * 1024;

export const THUMB_REFUSALS = {
  badRef: "That is not the ref of a picture your app was given, so nothing was shown. Use the ref hq.files.upload returned.",
  badSize: "You sent files.thumbnail a size that is not a number, so nothing was shown. Send {ref, size: 200} with a whole number of pixels, or leave size out.",
  badShape: "You sent files.thumbnail something other than {ref, size?}, so nothing was shown. Send the ref hq.files.upload returned, and a size if you want one.",
  tooBig: "That picture has too many pixels to make a small version of safely, so nothing was shown. Ask the owner for a smaller one.",
} as const;

function refuse(code: BridgeError["code"], message: string): BridgeError {
  return { code, message };
}

function isObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** The longest side the owner's setting asks for, brought within bounds; the default when it is not a number. */
export function clampPx(value: unknown, fallback = THUMB_DEFAULT_PX): number {
  const base = typeof value === "number" && Number.isFinite(value) ? Math.floor(value) : fallback;
  return Math.min(THUMB_MAX_PX, Math.max(THUMB_MIN_PX, base));
}

export type ThumbArgs = { ok: true; ref: string; px: number } | { ok: false; error: BridgeError };

/** `{ref, size?}` or a bare ref. `size` is the longest side in pixels, kept within the bounds and never above `ceiling` (the owner's setting). */
export function readThumbArgs(args: unknown, ceiling: number): ThumbArgs {
  const top = clampPx(ceiling);
  if (typeof args === "string") return isUploadRef(args) ? { ok: true, ref: args, px: top } : { ok: false, error: refuse("invalid", THUMB_REFUSALS.badRef) };
  if (!isObject(args) || !Object.keys(args).every((key) => key === "ref" || key === "size")) return { ok: false, error: refuse("invalid", THUMB_REFUSALS.badShape) };
  if (!isUploadRef(args.ref)) return { ok: false, error: refuse("invalid", THUMB_REFUSALS.badRef) };
  if (args.size === undefined || args.size === null) return { ok: true, ref: args.ref, px: top };
  if (typeof args.size !== "number" || !Number.isFinite(args.size)) return { ok: false, error: refuse("invalid", THUMB_REFUSALS.badSize) };
  return { ok: true, ref: args.ref, px: Math.min(top, clampPx(args.size)) };
}

/** The size of a picture that fits `px` on its longest side, in proportion, never larger than it is and never under one pixel. */
export function fitWithin(width: number, height: number, px: number): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (!(width > 0) || !(height > 0) || longest <= px) return { width: Math.max(1, Math.floor(width)), height: Math.max(1, Math.floor(height)) };
  const scale = px / longest;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

const be16 = (b: Uint8Array, at: number) => ((b[at] ?? 0) << 8) | (b[at + 1] ?? 0);
const be32 = (b: Uint8Array, at: number) => (be16(b, at) * 65536 + be16(b, at + 2)) >>> 0;
const le16 = (b: Uint8Array, at: number) => (b[at] ?? 0) | ((b[at + 1] ?? 0) << 8);
const le24 = (b: Uint8Array, at: number) => le16(b, at) | ((b[at + 2] ?? 0) << 16);
const ascii = (b: Uint8Array, at: number, length: number) => String.fromCharCode(...Array.from(b.subarray(at, at + length)));

/**
 * A GIF's size: the larger of its logical screen and the extent of its first image (its
 * offset plus its size), read by walking its blocks: the global colour table, then any
 * extension blocks (each a label and sub-blocks ending in an empty one), up to the first
 * image descriptor. Null when the blocks cannot be read that far within `b`: cut short, a
 * trailer before any image, or a byte that starts no block.
 */
export function gifSize(b: Uint8Array): { width: number; height: number } | null {
  if (b.length < 13 || ascii(b, 0, 4) !== "GIF8") return null;
  const packed = b[10] ?? 0;
  let at = 13;
  if (packed & 0x80) at += 3 * (1 << ((packed & 0x07) + 1));
  while (at < b.length) {
    const block = b[at];
    if (block === 0x2c) {
      if (at + 10 > b.length) return null;
      const left = le16(b, at + 1);
      const top = le16(b, at + 3);
      return { width: Math.max(le16(b, 6), left + le16(b, at + 5)), height: Math.max(le16(b, 8), top + le16(b, at + 7)) };
    }
    if (block !== 0x21) return null;
    at += 2; // the introducer and the extension's label
    for (;;) {
      if (at >= b.length) return null;
      const size = b[at] ?? 0;
      at += 1 + size;
      if (size === 0) break;
    }
  }
  return null;
}

/** The width and height a PNG, JPEG, GIF or WebP file says it has, read from its first bytes; null for anything else or anything cut short. */
export function sniffSize(b: Uint8Array): { width: number; height: number } | null {
  if (b.length >= 24 && b[0] === 0x89 && ascii(b, 1, 3) === "PNG") return { width: be32(b, 16), height: be32(b, 20) };
  if (b.length >= 4 && ascii(b, 0, 4) === "GIF8") return gifSize(b);
  if (b.length >= 30 && ascii(b, 0, 4) === "RIFF" && ascii(b, 8, 4) === "WEBP") {
    const chunk = ascii(b, 12, 4);
    if (chunk === "VP8 ") return { width: le16(b, 26) & 0x3fff, height: le16(b, 28) & 0x3fff };
    if (chunk === "VP8L") {
      const bits = ((b[21] ?? 0) | ((b[22] ?? 0) << 8) | ((b[23] ?? 0) << 16) | ((b[24] ?? 0) << 24)) >>> 0;
      return { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 };
    }
    if (chunk === "VP8X") return { width: le24(b, 24) + 1, height: le24(b, 27) + 1 };
    return null;
  }
  if (b.length >= 4 && b[0] === 0xff && b[1] === 0xd8) {
    let at = 2;
    while (at + 4 <= b.length) {
      if (b[at] !== 0xff) return null;
      const marker = b[at + 1] ?? 0;
      if (marker === 0xff) {
        at += 1;
        continue;
      }
      if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
        at += 2;
        continue;
      }
      if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
        return at + 9 <= b.length ? { width: be16(b, at + 7), height: be16(b, at + 5) } : null;
      }
      at += 2 + be16(b, at + 2);
    }
  }
  return null;
}

/** Whether a picture of this size may be decoded at all. */
export function sizeAllowed(width: number, height: number): boolean {
  return width > 0 && height > 0 && width <= MAX_SIDE && height <= MAX_SIDE && width * height <= MAX_PIXELS;
}

/** What the browser gives the shell to decode and draw with. A test hands in a fake. */
export type ThumbEnv = {
  /** Decodes `blob` (its orientation respected, a GIF as its first frame), draws it within `px` on the longest side and encodes it as `type`. Null when the browser cannot. */
  render: (blob: Blob, px: number, type: "image/png" | "image/jpeg") => Promise<Blob | null>;
};

/**
 * The small version of a picture, or the picture itself when it cannot be made smaller
 * or the browser cannot make it. Throws a `too_large` BridgeError for a picture with
 * too many pixels, before decoding it.
 */
export async function makeThumbnail(blob: Blob, px: number, env: ThumbEnv): Promise<Blob> {
  const head = new Uint8Array(await blob.slice(0, HEAD_BYTES).arrayBuffer());
  const size = sniffSize(head);
  if (!size) return blob;
  if (!sizeAllowed(size.width, size.height)) throw refuse("too_large", THUMB_REFUSALS.tooBig);
  const animated = blob.type === "image/gif";
  if (Math.max(size.width, size.height) <= px && !animated) return blob;
  let made: Blob | null;
  try {
    made = await env.render(blob, px, blob.type === "image/jpeg" ? "image/jpeg" : "image/png");
  } catch {
    return blob;
  }
  if (!made || made.size === 0) return blob;
  return made.size >= blob.size && !animated ? blob : made;
}
