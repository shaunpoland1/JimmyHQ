// `hq.ui.save({name, type, text | bytes})`: the page hands the owner a file to keep.
//
// An app's frame is sandboxed without downloads, so the page cannot offer one itself. It
// asks, and the shell, outside the frame, makes the Blob and triggers the browser's save.
// Framework-free, so each rule is tested from the attacker's side (apps.saving.test.ts).
//
// What a page can and cannot do with it:
//   - Only within SAVE_WINDOW_MS of a touch of the owner's inside the app's frame (as far
//     as the shell can tell: lib/touch.ts). One touch may save a few files in a row, up to
//     `apps.save_parts` (default 5), for an export split into parts: the first within
//     SAVE_WINDOW_MS of the touch, each next within SAVE_WINDOW_MS of the one before, and
//     the run over within SAVE_RUN_MS of the touch. The owner is shown each file as it is
//     saved (`savedLine`), and a browser asks its own question about several downloads.
//     A new touch starts a new run. Past the count, only a new touch saves more.
//   - However many touches there are, one app saves at most `apps.save_per_minute`
//     (default 12) files in any minute, counted for the app across all its frames in the
//     tab (`SaveLedger`), so a page that keeps the owner clicking, or reloads itself,
//     cannot pour files into the downloads.
//   - A file is at most `apps.save_max_mb` (default 8 MB), counted in bytes, text or bytes.
//   - Text is a string, written as it is. Bytes (`bytes`: an ArrayBuffer or a typed array)
//     are only for a picture (PNG, JPEG or WebP), and are checked to start like one, so a
//     `.png` is never a web page or a program in disguise. Bytes are copied before anything
//     is checked, so the page cannot change them afterwards.
//   - `type` is one of SAVE_TYPES (text), SAVE_PICTURES (bytes), or, when the owner allows
//     it (`apps.save_html`), HTML; anything else is plain text. It chooses the file's
//     ending, never what the browser is told the file is: the Blob is always
//     `application/octet-stream`, handed over as a download and never navigated to or
//     opened, so nothing saved is ever shown in the shell's own origin.
//   - HTML was once refused (a saved `.html` opened later from the disk would run whatever
//     script the page put in it, as a local file, with the network and without the frame's
//     sandbox). It is allowed now with this in place of the refusal (`htmlWithPolicy`):
//     the file starts with a Content-Security-Policy that blocks every script, form and
//     outside load, and every `<meta` and `<link` the page wrote is renamed so a refresh
//     or a prefetch it hid there does nothing. What is left: a link in the page still goes
//     where it points when the owner clicks it, which is why the owner is told each time
//     that a web page was saved, and the first time an app saves one, in plain words,
//     that it is a web page saved with its scripts switched off. A Blob has no response headers, so the policy is a meta
//     tag, and a meta tag cannot give a `sandbox` rule.
//   - `name` is made safe to use as a file name on any system, and always ends with the
//     ending the type chose. The owner is shown it in quotes, shortened, after JimmyHQ's
//     own words (`savedLine`), so a name written to look like a message from JimmyHQ
//     reads as what it is: a file name the app chose. No address, no path and no navigation: the shell makes the
//     object address, clicks it and revokes it, and the page never sees it.

import type { BridgeError } from "./bridge.ts";

/** What a page may save as text, by type: the ending its file gets. Anything else is "text/plain". */
export const SAVE_TYPES: Readonly<Record<string, string>> = Object.freeze({
  "text/plain": "txt",
  "text/markdown": "md",
  "text/csv": "csv",
  "application/json": "json",
});
/** HTML, which a page may save only when the owner has allowed it (`apps.save_html`). */
export const SAVE_HTML_TYPE = "text/html";
/** What a page may save as bytes, by type: the ending its file gets. */
export const SAVE_PICTURES: Readonly<Record<string, string>> = Object.freeze({
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
});
/** Other endings a name may already carry for a type, so `a.jpeg` is `a.jpg` and not `a.jpeg.jpg`. */
const ALSO_ENDS: Readonly<Record<string, readonly string[]>> = Object.freeze({ html: ["htm"], jpg: ["jpeg"] });
export const SAVE_DEFAULT_TYPE = "text/plain";
/** The most one file may weigh when the server says nothing: `apps.save_max_mb` (8). */
export const SAVE_DEFAULT_MAX_MB = 8;
export const SAVE_DEFAULT_MAX_BYTES = SAVE_DEFAULT_MAX_MB * 1024 * 1024;
/** How many files one touch may save when the server says nothing: `apps.save_parts` (5). */
export const SAVE_DEFAULT_PARTS = 5;
/** The most files one touch may ever save, whatever the server says: the setting's own maximum. */
export const SAVE_MAX_PARTS = 10;
/** How many files one app may save in any minute when the server says nothing: `apps.save_per_minute` (12). */
export const SAVE_DEFAULT_PER_MINUTE = 12;
/** The span `apps.save_per_minute` counts over. */
export const SAVE_MINUTE_MS = 60_000;
/** The longest file name the owner is shown in full; a longer one is shortened in the middle. */
export const SAVE_SHOWN_NAME_MAX = 48;
/** The longest a file name may be before its ending, in characters. */
export const SAVE_NAME_MAX = 100;
/** The name a file gets when the page gave none worth keeping. */
export const SAVE_DEFAULT_NAME = "file";
/** How long after the owner's touch, or after the file before it, a save may be made. */
export const SAVE_WINDOW_MS = 5000;
/** How long after the owner's touch a run of files may go on at the longest. */
export const SAVE_RUN_MS = 30_000;

/** What the owner's settings allow, as the server's mount answer says (`readMount`). */
export type SaveLimits = { maxBytes: number; maxParts: number; html: boolean; perMinute: number };
export const DEFAULT_SAVE_LIMITS: SaveLimits = Object.freeze({ maxBytes: SAVE_DEFAULT_MAX_BYTES, maxParts: SAVE_DEFAULT_PARTS, html: true, perMinute: SAVE_DEFAULT_PER_MINUTE });

export const SAVE_REFUSALS = {
  shape: "You sent ui.save something that is not {name?, type?, text} or {name?, type?, bytes}, so nothing was saved. Send {text: \"...\"} with a string, or {bytes} with an ArrayBuffer or Uint8Array of a picture.",
  noText: "You sent ui.save no text to put in the file, so nothing was saved. Send {text: \"...\"} with at least one character.",
  noBytes: "You sent ui.save a picture with no bytes in it, so nothing was saved. Send {bytes} with at least one byte.",
  tooLarge: (limit: string) =>
    `That file is larger than the ${limit} an app may save, so nothing was saved. Save a smaller file, or split it into parts and save them one after another from the same click.`,
  notPicture: "You sent ui.save bytes without a picture type, so nothing was saved. Bytes are for a picture: use type \"image/png\", \"image/jpeg\" or \"image/webp\". Text goes in {text}.",
  pictureAsText: "You sent ui.save a picture type with text, so nothing was saved. A picture goes in {bytes}: an ArrayBuffer or Uint8Array, such as the bytes of canvas.toBlob().",
  notThatPicture: "Those bytes do not start like the picture type you named, so nothing was saved. Send the picture's own bytes with its real type (PNG, JPEG or WebP).",
  noGesture: "The owner has not touched your page just now, so no file was saved. Call hq.ui.save from the owner's own click on a button.",
  tooMany: (parts: number) =>
    `You already saved ${parts} files for the owner's last touch, the most one click may hand over (the owner sets it as "Most files an app may offer from one click", apps.save_parts, under Settings, Apps), so nothing was saved. Save the rest from the owner's next click.`,
  tooFast: (perMinute: number) =>
    `You already saved ${perMinute} files in the last minute, the most one app may hand over in a minute (the owner sets it as "Most files an app may offer in one minute", apps.save_per_minute, under Settings, Apps), so nothing was saved. Wait a minute, then save the rest from the owner's next click.`,
} as const;

/** The same said to the owner, for the page to show as they are. */
export const SAVE_OWNER_REFUSALS = {
  noGesture: "That took too long, or the click was not in the app, so the file was not saved. Press the button again.",
  tooMany: (parts: number) => `That was more than the ${parts} files one click may save, so it was not saved. Press the button again for the rest.`,
  tooFast: (perMinute: number) => `This app has already saved ${perMinute} files in the last minute, the most it may, so that one was not saved. Wait a minute and press the button again.`,
} as const;

function refuse(code: BridgeError["code"], message: string, extra: Partial<BridgeError> = {}): BridgeError {
  return { code, message, ...extra };
}

function isObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function baseType(given: unknown): string {
  return typeof given === "string" ? (given.split(";", 1)[0]?.trim().toLowerCase() ?? "") : "";
}

/**
 * The text type to save as: the page's, if it is on the list (parameters and case ignored),
 * HTML only when `html` is true, else plain text.
 */
export function saveType(given: unknown, html = false): string {
  const base = baseType(given);
  if (html && base === SAVE_HTML_TYPE) return SAVE_HTML_TYPE;
  return Object.hasOwn(SAVE_TYPES, base) ? base : SAVE_DEFAULT_TYPE;
}

/** The picture type the page named, or null. `image/jpg` is taken as JPEG. */
export function pictureType(given: unknown): string | null {
  const base = baseType(given);
  const type = base === "image/jpg" ? "image/jpeg" : base;
  return Object.hasOwn(SAVE_PICTURES, type) ? type : null;
}

function endingOf(type: string): string {
  return SAVE_TYPES[type] ?? SAVE_PICTURES[type] ?? (type === SAVE_HTML_TYPE ? "html" : "txt");
}

// Windows' device names, which it takes as the device whatever ending follows and with
// spaces or dots after the name ("nul .txt", "CON..txt"), and with a superscript digit too ("COM¹").
const RESERVED = /^(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])$/i;
// Control characters (Cc), every format character (Cf: the direction marks and isolates,
// the Arabic letter mark U+061C, the invisible operators U+2060 to U+2064, the soft hyphen,
// the Mongolian vowel separator, the zero-width characters and the byte order mark), the
// rest of what Unicode says is drawn as nothing, line and paragraph separators, and every
// character a file name may not hold somewhere.
const UNSAFE = /[\p{Cc}\p{Cf}\p{Default_Ignorable_Code_Point}\p{Zl}\p{Zp}\\/:*?"<>|]+/gu;

/**
 * A file name that is safe on any system: one step (no path), no control or direction
 * characters, no leading dot (so never hidden) and no trailing dot or space, not a
 * device name, cut to SAVE_NAME_MAX, and ending with the ending of `type`.
 */
export function saveName(given: unknown, type: string): string {
  const ending = endingOf(type);
  let stem = typeof given === "string" ? given.normalize("NFC").replace(UNSAFE, "-").replace(/\s+/g, " ").trim() : "";
  for (const own of [ending, ...(ALSO_ENDS[ending] ?? [])]) {
    if (stem.toLowerCase().endsWith(`.${own}`)) {
      stem = stem.slice(0, -(own.length + 1));
      break;
    }
  }
  stem = Array.from(stem).slice(0, SAVE_NAME_MAX).join("");
  stem = stem.replace(/^[-.\s]+/, "").replace(/[-.\s]+$/, "");
  // The part Windows reads as the device: up to the first dot, without spaces or dots after it.
  const device = (stem.split(".")[0] ?? "").replace(/[\s.]+$/, "");
  if (!stem || RESERVED.test(device)) stem = stem ? `${SAVE_DEFAULT_NAME}-${stem}` : SAVE_DEFAULT_NAME;
  return `${stem}.${ending}`;
}

/**
 * What the shell saves: the clean name, the type, and the content exactly as the page gave
 * it (a string for text, a copy of the bytes for a picture), and how many bytes that is.
 */
export type SaveRequest = { name: string; type: string; body: string | Uint8Array; bytes: number };

export type SaveArgs = { ok: true; save: SaveRequest } | { ok: false; error: BridgeError };

function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** "8 MB", "1.5 MB", "300 KB": a limit in words. */
export function sizeWords(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    const mb = bytes / (1024 * 1024);
    return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Whether the first bytes are those of the picture type. */
export function looksLike(type: string, bytes: Uint8Array): boolean {
  const starts = (...head: number[]) => head.every((value, at) => bytes[at] === value);
  if (type === "image/png") return starts(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a);
  if (type === "image/jpeg") return starts(0xff, 0xd8, 0xff);
  if (type === "image/webp") {
    const tag = (from: number, word: string) => [...word].every((char, at) => bytes[from + at] === char.charCodeAt(0));
    return bytes.length >= 12 && tag(0, "RIFF") && tag(8, "WEBP");
  }
  return false;
}

/** A page's bytes: an ArrayBuffer or a typed array. Anything else, and anything shared, is not taken. */
function isBytes(value: unknown): value is ArrayBuffer | ArrayBufferView {
  return value instanceof ArrayBuffer || ArrayBuffer.isView(value);
}

function lengthOf(value: ArrayBuffer | ArrayBufferView): number {
  return value.byteLength;
}

function copyOf(value: ArrayBuffer | ArrayBufferView): Uint8Array {
  // `slice` makes a new, ordinary buffer: a page that still holds the old one cannot change this.
  return value instanceof ArrayBuffer ? new Uint8Array(value).slice() : new Uint8Array(value.buffer, value.byteOffset, value.byteLength).slice();
}

/**
 * What the tab remembers of each app's saves, for every frame of it: when its files of the
 * last minute were saved (`apps.save_per_minute`), and whether it has saved a web page yet.
 */
export type SaveLedger = { times: Map<string, number[]>; pages: Set<string> };

export function newLedger(): SaveLedger {
  return { times: new Map(), pages: new Set() };
}

/** How many files the app saved in the minute before `now`. */
export function savesInMinute(ledger: SaveLedger, appId: string, now: number): number {
  const recent = (ledger.times.get(appId) ?? []).filter((at) => at <= now && now - at < SAVE_MINUTE_MS);
  if (recent.length) ledger.times.set(appId, recent);
  else ledger.times.delete(appId);
  return recent.length;
}

/** Notes a file saved now. True when it is the app's first web page in this tab. */
export function noteSaved(ledger: SaveLedger, appId: string, save: SaveRequest, now: number): boolean {
  ledger.times.set(appId, [...(ledger.times.get(appId) ?? []), now]);
  if (save.type !== SAVE_HTML_TYPE || ledger.pages.has(appId)) return false;
  ledger.pages.add(appId);
  return true;
}

/** One touch's run of saves: the touch it belongs to, how many files so far and when the last one was. */
export type SaveRun = { touchedAt: number | null; count: number; lastAt: number | null };

export function newRun(): SaveRun {
  return { touchedAt: null, count: 0, lastAt: null };
}

/** Whether one thing may be done now: within SAVE_WINDOW_MS of a touch, and none done since that touch (dictation uses this too). */
export function saveAllowed(touchedAt: number | null, doneAt: number | null, now: number): "ok" | "noGesture" | "tooSoon" {
  if (touchedAt === null || now < touchedAt || now - touchedAt > SAVE_WINDOW_MS) return "noGesture";
  if (doneAt !== null && doneAt >= touchedAt) return "tooSoon";
  return "ok";
}

/**
 * Whether a save may be made now. The first of a touch: within SAVE_WINDOW_MS of it. The
 * next ones of the same touch: no more than `maxParts` in all, each within SAVE_WINDOW_MS
 * of the one before, and the run within SAVE_RUN_MS of the touch.
 */
export function saveRunAllowed(touchedAt: number | null, run: SaveRun, now: number, maxParts: number): "ok" | "noGesture" | "tooMany" {
  if (touchedAt === null || now < touchedAt) return "noGesture";
  if (run.touchedAt === touchedAt && run.count > 0 && run.lastAt !== null) {
    if (run.count >= maxParts) return "tooMany";
    return now - run.lastAt <= SAVE_WINDOW_MS && now - touchedAt <= SAVE_RUN_MS && now >= run.lastAt ? "ok" : "noGesture";
  }
  return now - touchedAt <= SAVE_WINDOW_MS ? "ok" : "noGesture";
}

/** The run once a save was made at `now` for the touch `touchedAt`. */
export function recordSave(run: SaveRun, touchedAt: number, now: number): SaveRun {
  return { touchedAt, count: run.touchedAt === touchedAt ? run.count + 1 : 1, lastAt: now };
}

/**
 * Reads `{name?, type?, text}` or `{name?, type?, bytes}` and nothing else. The owner's
 * touch (`touchedAt`, from lib/touch.ts) and the run of saves are checked here
 * (`saveAllowed`), so the page learns why nothing happened and the shell never has to guess.
 */
export function readSaveArgs(args: unknown, touchedAt: number | null, run: SaveRun, now: number, limits: SaveLimits = DEFAULT_SAVE_LIMITS, lastMinute = 0): SaveArgs {
  const fail = (error: BridgeError): SaveArgs => ({ ok: false, error });
  if (!isObject(args) || !Object.keys(args).every((key) => key === "name" || key === "type" || key === "text" || key === "bytes")) return fail(refuse("invalid", SAVE_REFUSALS.shape));
  const hasText = Object.hasOwn(args, "text");
  const hasBytes = Object.hasOwn(args, "bytes");
  if (hasText === hasBytes || (hasText && typeof args.text !== "string") || (hasBytes && !isBytes(args.bytes))) return fail(refuse("invalid", SAVE_REFUSALS.shape));
  const tooLarge = refuse("too_large", SAVE_REFUSALS.tooLarge(sizeWords(limits.maxBytes)));

  // What it is, and its size, before anything is copied or counted in full.
  let type: string;
  let size: number;
  if (hasText) {
    const text = args.text as string;
    if (pictureType(args.type) !== null) return fail(refuse("invalid", SAVE_REFUSALS.pictureAsText));
    if (!text) return fail(refuse("invalid", SAVE_REFUSALS.noText));
    // A string of more units than the limit has at least that many bytes.
    if (text.length > limits.maxBytes) return fail(tooLarge);
    type = saveType(args.type, limits.html);
    size = utf8Length(text);
  } else {
    const picture = pictureType(args.type);
    if (picture === null) return fail(refuse("invalid", SAVE_REFUSALS.notPicture));
    type = picture;
    size = lengthOf(args.bytes as ArrayBuffer | ArrayBufferView);
    if (size === 0) return fail(refuse("invalid", SAVE_REFUSALS.noBytes));
  }
  if (size > limits.maxBytes) return fail(tooLarge);

  const allowed = saveRunAllowed(touchedAt, run, now, limits.maxParts);
  if (allowed === "noGesture") return fail(refuse("not_allowed", SAVE_REFUSALS.noGesture, { owner_message: SAVE_OWNER_REFUSALS.noGesture }));
  if (allowed === "tooMany") return fail(refuse("quota", SAVE_REFUSALS.tooMany(limits.maxParts), { owner_message: SAVE_OWNER_REFUSALS.tooMany(limits.maxParts) }));
  if (lastMinute >= limits.perMinute) return fail(refuse("quota", SAVE_REFUSALS.tooFast(limits.perMinute), { owner_message: SAVE_OWNER_REFUSALS.tooFast(limits.perMinute) }));

  if (hasText) return { ok: true, save: { name: saveName(args.name, type), type, body: args.text as string, bytes: size } };
  const bytes = copyOf(args.bytes as ArrayBuffer | ArrayBufferView);
  if (!looksLike(type, bytes)) return fail(refuse("invalid", SAVE_REFUSALS.notThatPicture));
  return { ok: true, save: { name: saveName(args.name, type), type, body: bytes, bytes: bytes.length } };
}

/**
 * The policy a saved web page starts with: no script (inline, remote or in a link), no
 * outside load of any kind (pictures, fonts and media may only be `data:`), no form
 * submitted anywhere and no `<base>`.
 */
export const HTML_POLICY = "default-src 'none'; img-src data:; font-src data:; media-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'";

/**
 * A saved web page made safe to be a file: it starts with the HTML5 doctype, a character
 * set and `HTML_POLICY`, ahead of everything the page wrote, so the policy is in force
 * before any of it is read. Every `<meta` and `<link` of the page's is renamed (`<x-meta`):
 * a `<meta http-equiv="refresh">` would take whoever opens the file to an address of the
 * page's choosing without a click, and a `<link rel="dns-prefetch">` would send a name out
 * of the machine, and the policy cannot stop either. (A tag name ends at a space, `/` or
 * `>`, and no character reference or NUL byte can spell `meta` otherwise, so the match is
 * on the text.) The page's own doctype comes second and the browser ignores it.
 */
export function htmlWithPolicy(text: string): string {
  const renamed = text.replace(/<(meta|link)/gi, "<x-$1");
  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${HTML_POLICY}">${renamed}`;
}

/**
 * A file name as the owner is shown it: quotes of its own turned plain, and a long one
 * shortened in the middle so its ending still shows.
 */
export function shownName(name: string): string {
  const chars = Array.from(name.replace(/[“”„‟«»"]/g, "'"));
  if (chars.length <= SAVE_SHOWN_NAME_MAX) return chars.join("");
  const tail = 14;
  return `${chars.slice(0, SAVE_SHOWN_NAME_MAX - tail - 1).join("")}…${chars.slice(-tail).join("")}`;
}

/**
 * The sentence the owner sees as a file is saved: JimmyHQ's own words first (which app,
 * how big, which of a run), then the name the app chose, in quotes and shortened, so it
 * can never read as a message from JimmyHQ. A web page carries a word of care: in full
 * the first time the app saves one in this tab (`firstPage`), short after that.
 */
export function savedLine(appName: string, save: SaveRequest, partNumber: number, maxParts: number, firstPage = false): string {
  const part = partNumber > 1 ? `, file ${partNumber} of at most ${maxParts} from this click` : "";
  let care = "";
  if (save.type === SAVE_HTML_TYPE) {
    care = firstPage
      ? " This is a web page: JimmyHQ saved it with its scripts switched off, but its links still work if you click them. Open it only if you trust the app."
      : " A web page, saved with its scripts switched off.";
  }
  return `Saving a file from ${appName} to your downloads (${sizeWords(save.bytes)}${part}). File name: “${shownName(save.name)}”.${care}`;
}

/** What the browser gives the shell to save a file with. A test hands in fakes. */
export type SaveEnv = {
  makeBlob: (body: string | Uint8Array) => Blob;
  createUrl: (blob: Blob) => string;
  revokeUrl: (url: string) => void;
  /** Makes a link to `url` that downloads as `name`, clicks it, and takes it away again. */
  click: (url: string, name: string) => void;
  /** Runs `work` a little later, once the browser has started the save. */
  later: (work: () => void) => void;
};

/**
 * Hands the file to the browser: an object address is made, clicked as a download and
 * revoked. The Blob is `application/octet-stream`, so even if a browser ignored the
 * download it would not show the content as a page. A web page gets its policy first.
 */
export function deliver(save: SaveRequest, env: SaveEnv): void {
  const body = save.type === SAVE_HTML_TYPE && typeof save.body === "string" ? htmlWithPolicy(save.body) : save.body;
  const url = env.createUrl(env.makeBlob(body));
  try {
    env.click(url, save.name);
  } finally {
    env.later(() => env.revokeUrl(url));
  }
}
