// `hq.files.picture(ref, size?)`: one of the app's own pictures, large, for a full view.
//
// It is the small version's path (lib/thumbnail.ts) with larger limits, nothing new: the
// shell fetches the kept picture for the app it mounted (`GET /api/apps/<id>/files/<file>`,
// the owner's session, a ref of the upload shape only), reads its size in pixels from its
// first bytes before anything is decoded (too many pixels is refused, so a small file that
// unpacks to a huge picture is never decoded in JimmyHQ's own page), and draws it within
// the owner's `apps.picture_px` with a canvas. What changes from a thumbnail, and why:
//
//   - The longest side is `apps.picture_px` (2048 by default) instead of `apps.thumbnail_px`.
//   - Bytes: a kept picture larger than `apps.picture_max_mb` is not read at all. The
//     length the server declares is checked first, then the body is read with a cap, so
//     a program that wrote a huge file under its uploads cannot make the shell hold it.
//   - Rate: one open screen may ask for at most `apps.pictures_per_minute` full views a
//     minute, and they are made one at a time, so a page cannot keep the owner's browser
//     decoding large pictures in a loop.
//
// Framework-free, so each rule is tested with fakes (web/tests/apps.picture.test.ts).

import type { BridgeError } from "./bridge.ts";
import { THUMB_MIN_PX } from "./thumbnail.ts";
import { isUploadRef } from "./uploads.ts";

/** What the server's `apps.picture_px` is when an older one says nothing. */
export const PICTURE_DEFAULT_PX = 2048;
export const PICTURE_MAX_PX = 8192;
export const PICTURE_DEFAULT_MAX_MB = 25;
export const PICTURE_DEFAULT_PER_MINUTE = 60;
export const PICTURE_WINDOW_MS = 60_000;

export const PICTURE_REFUSALS = {
  badRef: "That is not the ref of a picture your app keeps, so nothing was shown. Use a ref of the shape uploads/<32 hex>.<ending>.",
  badSize: "You sent files.picture a size that is not a number, so nothing was shown. Send {ref, size: 1600} with a whole number of pixels, or leave size out.",
  badShape: "You sent files.picture something other than {ref, size?}, so nothing was shown. Send the picture's ref, and a size if you want one.",
  tooHeavy: (mb: number) =>
    `That picture weighs more than the ${mb} MB a screen may show in full, so nothing was shown. Show it with files.thumbnail, or ask the owner to raise "Largest picture an app's screen may show in full (MB)" under Settings, Apps.`,
  tooHeavyOwner: (mb: number) =>
    `This picture is larger than the ${mb} MB an app may show in full. Raise "Largest picture an app's screen may show in full (MB)" under Settings, Apps, to see it.`,
  tooOften: (most: number) =>
    `You asked for more than ${most} full-size pictures in a minute, so nothing was shown. Wait and ask again, or ask the owner to raise "Pictures an app's screen may show in full a minute" under Settings, Apps.`,
} as const;

function refuse(code: BridgeError["code"], message: string, extra: Partial<BridgeError> = {}): BridgeError {
  return { code, message, ...extra };
}

function isObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** The longest side for a full view, brought within bounds; the default when it is not a number. */
export function clampPicturePx(value: unknown, fallback = PICTURE_DEFAULT_PX): number {
  const base = typeof value === "number" && Number.isFinite(value) ? Math.floor(value) : fallback;
  return Math.min(PICTURE_MAX_PX, Math.max(THUMB_MIN_PX, base));
}

export type PictureArgs = { ok: true; ref: string; px: number } | { ok: false; error: BridgeError };

/** `{ref, size?}` or a bare ref. `size` may ask for fewer pixels than the owner's `apps.picture_px`, never more. */
export function readPictureArgs(args: unknown, ceiling: number): PictureArgs {
  const top = clampPicturePx(ceiling);
  if (typeof args === "string") return isUploadRef(args) ? { ok: true, ref: args, px: top } : { ok: false, error: refuse("invalid", PICTURE_REFUSALS.badRef) };
  if (!isObject(args) || !Object.keys(args).every((key) => key === "ref" || key === "size")) return { ok: false, error: refuse("invalid", PICTURE_REFUSALS.badShape) };
  if (!isUploadRef(args.ref)) return { ok: false, error: refuse("invalid", PICTURE_REFUSALS.badRef) };
  if (args.size === undefined || args.size === null) return { ok: true, ref: args.ref, px: top };
  if (typeof args.size !== "number" || !Number.isFinite(args.size)) return { ok: false, error: refuse("invalid", PICTURE_REFUSALS.badSize) };
  return { ok: true, ref: args.ref, px: Math.min(top, clampPicturePx(args.size)) };
}

/** The refusal for a picture over the owner's byte limit, for the page and for the owner. */
export function tooHeavy(maxBytes: number): BridgeError {
  const mb = Math.max(1, Math.round(maxBytes / (1024 * 1024)));
  return refuse("too_large", PICTURE_REFUSALS.tooHeavy(mb), { owner_message: PICTURE_REFUSALS.tooHeavyOwner(mb) });
}

/** Whether the length the server declares is already over the limit (an absent or unreadable one is checked while reading). */
export function declaredTooHeavy(declared: string | null, maxBytes: number): boolean {
  if (declared === null || !/^\d{1,15}$/.test(declared.trim())) return false;
  return Number(declared.trim()) > maxBytes;
}

/** Something that hands over a body a piece at a time: a fetch Response's body reader, or a fake. */
export type Pieces = { read: () => Promise<{ done: boolean; value?: Uint8Array }>; cancel: () => Promise<unknown> | void };

/** The whole body, read a piece at a time and stopped (and cancelled) as soon as it passes `maxBytes`. */
export async function readCapped(pieces: Pieces, maxBytes: number): Promise<Uint8Array[]> {
  const out: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await pieces.read();
    if (done) return out;
    if (!value) continue;
    size += value.byteLength;
    if (size > maxBytes) {
      await Promise.resolve(pieces.cancel()).catch(() => undefined);
      throw tooHeavy(maxBytes);
    }
    out.push(value);
  }
}

/** The full views one open screen asked for lately: when each was asked, oldest first. */
export type PictureLog = readonly number[];

export type PictureTake = { ok: true; log: number[] } | { ok: false; log: number[]; error: BridgeError };

/** Count one full view against `perMinute`, in a sliding minute. A refusal says when asking again can work. */
export function takePicture(log: PictureLog, now: number, perMinute: number): PictureTake {
  const recent = log.filter((at) => at > now - PICTURE_WINDOW_MS && at <= now);
  const most = Math.max(1, Math.floor(perMinute));
  if (recent.length >= most) {
    const oldest = recent[0] ?? now;
    const retry = Math.max(1, Math.ceil((oldest + PICTURE_WINDOW_MS - now) / 1000));
    return { ok: false, log: recent, error: refuse("quota", PICTURE_REFUSALS.tooOften(most), { retry_after_s: retry }) };
  }
  return { ok: true, log: [...recent, now] };
}
