// Pictures the owner gives an app, from the shell's side. Framework-free, so each rule is
// tested from the attacker's side (web/tests/apps.uploads.test.ts).
//
// The app's page lets the owner pick files in its own frame and hands each File over the
// bridge (`hq.files.upload(file)`). The frame never sends anything itself: the shell
// checks the hand-over here, then posts the bytes as the owner's session to
//
//   POST /api/apps/<id>/files     body: the bytes; X-JimmyHQ-File-Name: the name, percent-encoded
//
// with the app id it mounted, never one the frame names, and hands `{ref, name, size,
// type}` back. The server reads what the picture is from its bytes and is the authority;
// these checks refuse early and keep junk off the wire.
//
// `hq.files.thumbnail(ref)` gives the page back one of its own pictures as a Blob, which it
// can show with URL.createObjectURL: the shell fetches `GET /api/apps/<id>/files/<file>`
// for the mounted app only, so the frame reaches nothing it could not before.

import type { BridgeError, ErrorCode } from "./bridge.ts";

const APP_ID = /^[a-z][a-z0-9_]{1,30}$/;
const REF = /^uploads\/([0-9a-f]{32}\.(?:png|jpg|gif|webp|avif|heic))$/;
export const NAME_HEADER = "X-JimmyHQ-File-Name";
/** How long after the owner last touched the app's page a picture may still be handed over. */
export const GESTURE_WINDOW_MS = 2 * 60 * 1000;
/**
 * How long after the owner's touch a run of uploads may go on at the longest. A picture
 * that arrives renews the window for the next one, so a slow link does not cut a batch
 * in half; past this, only a new touch opens it again.
 */
export const BATCH_LIMIT_MS = 10 * 60 * 1000;
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/heic"] as const;

export const UPLOAD_REFUSALS = {
  shape: "You sent files.upload something that is not one file, so nothing was kept. Send {file} with a File the owner picked.",
  tooLarge: (mb: number) => `That picture is larger than the ${mb} MB an app may be given, so nothing was kept. Choose a smaller one.`,
  noGesture: "The owner has not touched your page just now, so no picture was taken. Call hq.files.upload from the owner's own pick of a file.",
  badRef: "That is not the ref of a picture your app was given, so nothing was shown. Use the ref hq.files.upload returned.",
} as const;

/** The same refusals said to the owner, for the page to show as they are. */
export const OWNER_REFUSALS = {
  noGesture: "This picture was not added: JimmyHQ takes a picture only right after you choose it, and this page had not been touched just now. Choose it again.",
} as const;

/** The refusal for a hand-over with no recent touch: one sentence for the app's code, one for the owner. */
export function noGestureRefusal(): BridgeError {
  return { code: "not_allowed", message: UPLOAD_REFUSALS.noGesture, owner_message: OWNER_REFUSALS.noGesture };
}

function refuse(code: ErrorCode, message: string): BridgeError {
  return { code, message };
}

function isObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** What the shell needs of a picked file: a Blob with a name. A File is one. */
export type Picked = Blob & { name: string };

export function isPicked(value: unknown): value is Picked {
  return typeof Blob !== "undefined" && value instanceof Blob && typeof (value as { name?: unknown }).name === "string";
}

export type UploadArgs = { ok: true; file: Picked } | { ok: false; error: BridgeError };

/**
 * The one file a `files.upload` hands over: `{file}` and nothing else, a real File (never
 * a string, an address or a path), within the size the server will take. The name is the
 * frame's to give, so it is only ever sent as a percent-encoded header.
 */
export function readUploadArgs(args: unknown, maxBytes: number): UploadArgs {
  if (!isObject(args) || Object.keys(args).length !== 1 || !isPicked(args.file)) return { ok: false, error: refuse("invalid", UPLOAD_REFUSALS.shape) };
  if (args.file.size > maxBytes) return { ok: false, error: refuse("too_large", UPLOAD_REFUSALS.tooLarge(Math.floor(maxBytes / (1024 * 1024)))) };
  return { ok: true, file: args.file };
}

/** The other two file calls take `{ref}` only, or a bare ref. */
export function readRefArgs(args: unknown): string | null {
  const ref = isObject(args) && Object.keys(args).length === 1 ? args.ref : args;
  return typeof ref === "string" && REF.test(ref) ? ref : null;
}

export function isUploadRef(ref: unknown): ref is string {
  return typeof ref === "string" && REF.test(ref);
}

export function uploadPath(appId: string): string | null {
  return APP_ID.test(appId) ? `/api/apps/${appId}/files` : null;
}

/** Where the owner's page reads one of an app's pictures, or null unless both parts are exactly right. */
export function picturePath(appId: string, ref: unknown): string | null {
  if (!APP_ID.test(appId) || typeof ref !== "string") return null;
  const found = REF.exec(ref);
  return found ? `/api/apps/${appId}/files/${found[1]}` : null;
}

/** The header that carries the picture's name: percent-encoded, so no character can break it. */
export function uploadHeaders(name: string): Record<string, string> {
  return { [NAME_HEADER]: encodeURIComponent(name.slice(0, 1000)), "Content-Type": "application/octet-stream" };
}

export type Uploaded = { ref: string; name: string; size: number; type: string };

/** The server's answer, or null when it is not one. Only these four things reach the frame. */
export function readUploaded(raw: unknown): Uploaded | null {
  if (!isObject(raw) || !isUploadRef(raw.ref) || typeof raw.name !== "string" || typeof raw.size !== "number" || typeof raw.type !== "string") return null;
  if (!(IMAGE_TYPES as readonly string[]).includes(raw.type) || !Number.isSafeInteger(raw.size) || raw.size < 0) return null;
  return { ref: raw.ref, name: raw.name.slice(0, 120), size: raw.size, type: raw.type };
}

/** The bridge error for a refused upload: the status picks the code, the server's sentence is kept. */
export function uploadFailure(status: number, detail: string): BridgeError {
  const code: ErrorCode =
    status === 413 ? "too_large" : status === 507 ? "quota" : status === 415 || status === 422 ? "invalid" : status === 404 ? "not_found" : status === 401 || status === 403 ? "not_allowed" : status === 409 || status === 0 || status >= 502 ? "unavailable" : "internal";
  const text = detail.slice(0, 500);
  return refuse(code, text || "The picture could not be kept, so nothing was. Try again.");
}

/** What the browser tells the shell about the owner's touch (`navigator.userActivation`). */
export type Activation = { isActive: boolean; hasBeenActive: boolean } | null | undefined;

/**
 * Whether a hand-over can have come from the owner's own pick. A touch inside the frame
 * counts for the shell too (the browser passes it up to the page that holds the frame).
 * Where the browser cannot say, the shell cannot tell, and the upload goes ahead.
 */
export function gestureAllows(activation: Activation, lastSeenAt: number | null, now: number, windowMs = GESTURE_WINDOW_MS): boolean {
  if (!activation) return true;
  if (activation.isActive) return true;
  if (!activation.hasBeenActive) return false;
  return lastSeenAt !== null && now - lastSeenAt >= 0 && now - lastSeenAt <= windowMs;
}

/**
 * Whether a hand-over may go ahead: on the owner's touch (`gestureAllows`), or as the next
 * picture of a batch that touch began. A picture that arrived safely (`renewedAt`) opens
 * the window again for the one after it, and nothing else does: not a refused upload, not
 * one that failed, not one from before the touch. The run ends `limitMs` after the
 * touch whatever arrived, so one touch never lets a page keep uploading for good.
 */
export function uploadAllowed(
  activation: Activation,
  touchedAt: number | null,
  renewedAt: number | null,
  now: number,
  windowMs = GESTURE_WINDOW_MS,
  limitMs = BATCH_LIMIT_MS,
): boolean {
  if (gestureAllows(activation, touchedAt, now, windowMs)) return true;
  if (!activation || !activation.hasBeenActive) return false;
  if (touchedAt === null || renewedAt === null) return false;
  if (renewedAt < touchedAt || now < renewedAt) return false;
  if (now - touchedAt > limitMs) return false;
  return now - renewedAt <= windowMs;
}

/** Runs the uploads of one frame one after another, as the server takes one at a time per app. */
export function createQueue() {
  let tail: Promise<unknown> = Promise.resolve();
  return function enqueue<T>(work: () => Promise<T>): Promise<T> {
    const next = tail.then(work, work);
    tail = next.catch(() => undefined);
    return next;
  };
}
