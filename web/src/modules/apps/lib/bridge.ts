// The shell's side of the app bridge: every rule that stands between an app's
// page and the owner's signed-in session. Framework-free so each rule is tested
// from the attacker's side (web/tests/apps.bridge*.test.ts).
//
// An app's page runs in a sandboxed frame with an opaque origin and holds no
// credential. All it can do is post messages. This file decides which of those
// messages become a request to the server, made by the shell as the owner's
// session but on behalf of `app:<id>`. The server checks permissions and quotas
// again and is the authority; these checks refuse early and keep junk off the wire.

import { SAVE_DEFAULT_MAX_MB, SAVE_DEFAULT_PARTS, SAVE_DEFAULT_PER_MINUTE, SAVE_MAX_PARTS } from "./saving.ts";
import type { SaveLimits } from "./saving.ts";
import { clampPx } from "./thumbnail.ts";
import { PICTURE_DEFAULT_MAX_MB, PICTURE_DEFAULT_PER_MINUTE, clampPicturePx } from "./picture.ts";

// ---------------------------------------------------------------- the frame

/** Exactly what the frame may do: run scripts and use forms. Never `allow-same-origin`. */
export const FRAME_SANDBOX = "allow-scripts allow-forms";
/** No camera, microphone, location or any other feature. */
export const FRAME_ALLOW = "";
export const FRAME_REFERRER_POLICY = "no-referrer";
/** What `event.origin` reads for a sandboxed frame without `allow-same-origin`. */
export const OPAQUE_ORIGIN = "null";
/** The handshake's version number: `{jimmyhq: 1, port}`. */
export const BRIDGE_VERSION = 1;

const APP_ID = /^[a-z][a-z0-9_]{1,30}$/;
const MOUNT = /^[A-Za-z0-9_-]{16,128}$/;
const PATH_PART = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$/;

export function isAppId(value: unknown): value is string {
  return typeof value === "string" && APP_ID.test(value);
}

/**
 * The frame's address, `/appfiles/<mount>/<entry>`, or null when either part is
 * not what the server should have sent. An absolute address, a scheme, a
 * backslash, `..`, a query or a fragment never reach the `src` attribute.
 * A described screen has no file and is never framed: JimmyHQ draws it itself.
 */
export function frameSrc(mount: unknown, entry: unknown): string | null {
  if (typeof mount !== "string" || !MOUNT.test(mount)) return null;
  if (typeof entry !== "string" || entry.length > 300) return null;
  const parts = entry.split("/");
  if (!parts.every((part) => PATH_PART.test(part) && !part.includes(".."))) return null;
  return `/appfiles/${mount}/${parts.join("/")}`;
}

/**
 * True only for a window message sent by this frame's own window. A sandboxed
 * frame's origin is opaque, so the origin proves nothing by itself: the window
 * identity is the check, and the origin must also be the opaque one (anything
 * else means the frame is no longer the sandboxed page we mounted).
 */
export function isFromFrame(event: { source: unknown; origin: unknown }, frameWindow: unknown): boolean {
  return frameWindow !== null && frameWindow !== undefined && event.source === frameWindow && event.origin === OPAQUE_ORIGIN;
}

/** The only window message the shell listens for: `{jimmyhq: 1, hello: true}`. */
export function isHello(data: unknown): boolean {
  if (!isPlainObject(data)) return false;
  const keys = Object.keys(data);
  return keys.length === 2 && data.jimmyhq === BRIDGE_VERSION && data.hello === true;
}

// ---------------------------------------------------------------- the protocol

/** `cancelled` is the shell's alone: the owner cancelled something the page asked for (`speech.dictate`). */
export const ERROR_CODES = ["not_allowed", "quota", "invalid", "not_found", "too_large", "unavailable", "internal", "cancelled"] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];
/**
 * `message` is written for the app's code. `owner_message`, when the shell or JimmyHQ's
 * server has one, says the same thing to the owner in words they can act on; only those
 * set it, never the frame, and never from what an app's own program said.
 */
export type BridgeError = { code: ErrorCode; message: string; retry_after_s?: number; owner_message?: string };

export type RequestId = string | number;
export type BridgeRequest = { id: RequestId; call: string; args: unknown };
export type BridgeResponse = { id: RequestId; ok: true; result: unknown } | { id: RequestId; ok: false; error: BridgeError };
export type BridgeEvent = { event: string; data: unknown };

export const EVENTS = ["ready", "theme", "settings.changed", "records.changed", "bench.decided", "visible", "reload"] as const;

export type Tier = "sandboxed" | "full";
/**
 * What the owner agreed to for one app. It comes from the installed list, never from the
 * frame. `ownNetwork` lists the addresses on the owner's own network he let this app reach,
 * as `scheme://host:port` (set only when there are any); it is never passed to the frame.
 * `ownerHosts` lists the hosts he gave the app himself on its page (set only when there are
 * any): `net.fetch` may reach them over https exactly as it reaches a `net:` host.
 */
export type Grant = { tier: Tier; permissions: readonly string[]; ownNetwork?: readonly string[]; ownerHosts?: readonly string[] };

type CallRule = {
  /** The permission the call needs; `net` means any `net:<host>`. */
  needs?: string;
  /** Full tier only. */
  full?: boolean;
  /** Answered by the shell itself and never sent to the server. */
  shell?: boolean;
  /** Its arguments carry a File, checked by the shell's own handler instead of as JSON. */
  blob?: boolean;
};

/** Every call an app may make. A name that is not a key here never leaves the browser. */
export const CALLS: Readonly<Record<string, CallRule>> = Object.freeze({
  "storage.get": {},
  "storage.set": {},
  "storage.delete": {},
  "storage.keys": {},
  "records.list": {},
  "records.get": {},
  "records.create": {},
  "records.update": {},
  "records.delete": {},
  "tasks.list": { needs: "tasks.read" },
  "tasks.create": { needs: "tasks.write" },
  "tasks.complete": { needs: "tasks.write" },
  "bench.file": { needs: "bench.file" },
  "settings.get": {},
  "model.ask": { needs: "model.ask" },
  "model.embed": { needs: "model.ask" },
  "memory.recall": { needs: "memory.recall" },
  "web.search": { needs: "web" },
  "web.read": { needs: "web" },
  "net.fetch": { needs: "net" },
  // The hosts the owner gave this app himself: no permission, and nothing can be added by it.
  "hosts.list": {},
  "app.call": { full: true },
  "ui.toast": { shell: true },
  "ui.badge": { shell: true },
  // The owner's limits for what an app may save and how big an answer may be, as plain numbers.
  // The server answers it, from the settings as they are now: no permission, nothing else told.
  "ui.limits": {},
  // Opens the calling app's own page under Settings: no address comes from the app.
  "ui.settings": { shell: true },
  // Hands the owner a file (text, a web page or a picture's bytes), on the owner's touch: the
  // shell makes it (lib/saving.ts). Its bytes cannot be JSON, so the handler checks them whole.
  "ui.save": { shell: true, blob: true },
  // Pictures the owner picks: the shell sends them itself (lib/uploads.ts).
  "files.upload": { shell: true, blob: true },
  "files.thumbnail": { shell: true },
  // The same picture large, for a full view: the thumbnail's path with larger limits (lib/picture.ts).
  "files.picture": { shell: true },
  // Sent by the bridge script when the owner opens a file picker in the page.
  "files.picking": { shell: true },
  // Sent by the bridge script when the owner clicks or presses a key in the page: a hint that a
  // new touch began while the browser's activation of the last one is still alive (lib/touch.ts).
  "ui.touch": { shell: true },
  // The owner dictates on the shell's own surface; the page gets only the words (lib/dictate.ts).
  "speech.dictate": { needs: "speech.dictate", shell: true },
  "speech.available": { needs: "speech.dictate", shell: true },
});

export function isCall(name: unknown): boolean {
  return typeof name === "string" && Object.hasOwn(CALLS, name);
}

export type Limits = {
  /** Largest request, counted as UTF-8 bytes of its JSON. */
  maxBytes: number;
  /** Deepest nesting of lists and objects in `args`. */
  maxDepth: number;
  /** Requests a second. The server's quota is the authority; this stops a flood reaching it. */
  callsPerSecond: number;
  /** Requests waiting on the server at once. */
  maxInFlight: number;
  /** Refusals in one second after which the shell stops answering until the flood ends. */
  silentAfter: number;
};

export const DEFAULT_LIMITS: Limits = Object.freeze({
  maxBytes: 1024 * 1024 + 4096,
  maxDepth: 32,
  callsPerSecond: 20,
  maxInFlight: 32,
  silentAfter: 100,
});

export const MAX_ID_LENGTH = 64;

// ---------------------------------------------------------------- refusal sentences

export const REFUSALS = {
  malformed: "Your message was not a bridge request, so nothing was done. Send {id, call, args} and nothing else.",
  badId: "Your request had no usable id, so nothing was done. Use a number, or text of 1 to 64 characters.",
  notJson: "Your request held something that is not plain JSON, so nothing was done. Send only text, numbers, true, false, null, lists and objects.",
  tooDeep: "Your request was nested too deeply, so nothing was done. Flatten the data and send it again.",
  tooLarge: "Your request was too large, so nothing was done. Send less at a time.",
  duplicate: "You already have a request waiting with this id, so nothing was done. Give each request its own id.",
  flood: "You are calling too fast, so nothing was done. Wait a moment and send it again.",
  busy: "You have too many requests waiting, so nothing was done. Wait for some to finish and send it again.",
  unreachable: "Your request could not reach JimmyHQ, so nothing was done. Try again in a moment.",
  internal: "Your request failed inside JimmyHQ, so nothing was done. Try again, and tell the owner if it keeps happening.",
  unknownCall: (call: string) => `You called "${call}", which is not a bridge call, so nothing was done. Check the name against the app guide.`,
  needsPermission: (call: string, permission: string) =>
    `You called ${call} without the "${permission}" permission, so nothing was done. Add "${permission}" to the manifest's permissions and ask the owner to install the app again.`,
  needsHost: (host: string) =>
    `You asked for ${host} without the "net:${host}" permission, so nothing was done. Add "net:${host}" to the manifest's permissions and ask the owner to install the app again.`,
  needsHttps: "You asked for an address that is not https, so nothing was done. Use an https address on a host your manifest names.",
  needsFull: (call: string) => `You called ${call}, which only a full app may use, so nothing was done. Sandboxed apps keep their data with storage and records.`,
} as const;

function refuse(code: ErrorCode, message: string, retryAfterS?: number): BridgeError {
  return retryAfterS === undefined ? { code, message } : { code, message, retry_after_s: retryAfterS };
}

// ---------------------------------------------------------------- shape and size

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function utf8Length(text: string): number {
  let bytes = 0;
  for (let i = 0; i < text.length; i += 1) {
    const unit = text.charCodeAt(i);
    bytes += unit < 0x80 ? 1 : unit < 0x800 ? 2 : unit >= 0xd800 && unit <= 0xdfff ? 2 : 3;
  }
  return bytes;
}

export type JsonCheck = { ok: true; bytes: number } | { ok: false; error: BridgeError };

/**
 * Walks a value that arrived by structured clone and accepts it only if it is
 * plain JSON within the size and depth limits. A message can carry things JSON
 * cannot (a Map, a Blob, a Date, a cycle, a sparse list); each is refused here,
 * before anything is serialised, and the walk stops as soon as the budget is spent.
 */
export function checkJson(value: unknown, limits: Pick<Limits, "maxBytes" | "maxDepth"> = DEFAULT_LIMITS): JsonCheck {
  let bytes = 0;
  let failure: BridgeError | null = null;
  const fail = (code: ErrorCode, message: string) => {
    failure = failure ?? refuse(code, message);
    return false;
  };
  const spend = (count: number) => {
    bytes += count;
    return bytes <= limits.maxBytes || fail("too_large", REFUSALS.tooLarge);
  };
  const walk = (item: unknown, depth: number): boolean => {
    if (item === null) return spend(4);
    switch (typeof item) {
      case "boolean":
        return spend(5);
      case "number":
        return Number.isFinite(item) ? spend(String(item).length) : fail("invalid", REFUSALS.notJson);
      case "string":
        // A string longer than the budget is refused without reading it.
        return item.length > limits.maxBytes ? fail("too_large", REFUSALS.tooLarge) : spend(utf8Length(item) + 2);
      case "object":
        break;
      default:
        return fail("invalid", REFUSALS.notJson);
    }
    if (depth >= limits.maxDepth) return fail("invalid", REFUSALS.tooDeep);
    if (Array.isArray(item)) {
      if (!spend(2 + item.length)) return false;
      for (let i = 0; i < item.length; i += 1) {
        if (!Object.hasOwn(item, i)) return fail("invalid", REFUSALS.notJson);
        if (!walk(item[i], depth + 1)) return false;
      }
      return true;
    }
    if (!isPlainObject(item)) return fail("invalid", REFUSALS.notJson);
    if (Object.getOwnPropertySymbols(item).length > 0) return fail("invalid", REFUSALS.notJson);
    if (!spend(2)) return false;
    for (const key of Object.keys(item)) {
      if (!spend(utf8Length(key) + 4)) return false;
      if (!walk(item[key], depth + 1)) return false;
    }
    return true;
  };
  if (walk(value, 0)) return { ok: true, bytes };
  return { ok: false, error: failure ?? refuse("invalid", REFUSALS.notJson) };
}

/** The request's id if it has a usable one, so a refusal can be matched to its request. */
export function requestId(raw: unknown): RequestId | null {
  if (!isPlainObject(raw) || !Object.hasOwn(raw, "id")) return null;
  const id = raw.id;
  if (typeof id === "string") return id.length >= 1 && id.length <= MAX_ID_LENGTH ? id : null;
  if (typeof id === "number") return Number.isSafeInteger(id) && id >= 0 ? id : null;
  return null;
}

export type ParsedRequest = { ok: true; request: BridgeRequest } | { ok: false; id: RequestId | null; error: BridgeError };

const REQUEST_KEYS = new Set(["id", "call", "args"]);

/**
 * Checks one message from the frame: a plain object with only `id`, `call` and
 * `args`; a usable id; a call from the allowlist; `args` that is plain JSON
 * within the limits. Anything else is refused with a sentence. A field such as
 * `app`, `app_id` or `url` makes the whole message malformed: the frame does
 * not get to say who it is or where its request goes.
 */
export function parseRequest(raw: unknown, limits: Pick<Limits, "maxBytes" | "maxDepth"> = DEFAULT_LIMITS): ParsedRequest {
  const id = requestId(raw);
  if (!isPlainObject(raw)) return { ok: false, id: null, error: refuse("invalid", REFUSALS.malformed) };
  if (id === null) return { ok: false, id, error: refuse("invalid", REFUSALS.badId) };
  if (Object.getOwnPropertySymbols(raw).length > 0 || !Object.keys(raw).every((key) => REQUEST_KEYS.has(key))) {
    return { ok: false, id, error: refuse("invalid", REFUSALS.malformed) };
  }
  const call = raw.call;
  if (typeof call !== "string") return { ok: false, id, error: refuse("invalid", REFUSALS.malformed) };
  if (!isCall(call)) {
    // The name is echoed, so keep it short and free of anything but plain characters.
    const shown = call.slice(0, 40).replace(/[^A-Za-z0-9._-]/g, "?");
    return { ok: false, id, error: refuse("invalid", REFUSALS.unknownCall(shown)) };
  }
  const args = Object.hasOwn(raw, "args") && raw.args !== undefined ? raw.args : {};
  // A File cannot be JSON; the shell's handler for the call checks it whole.
  if (CALLS[call]?.blob) return { ok: true, request: { id, call, args } };
  const size = checkJson(args, limits);
  if (!size.ok) return { ok: false, id, error: size.error };
  return { ok: true, request: { id, call, args } };
}

// ---------------------------------------------------------------- permissions

/** The lower-case host of an https address, or null when the address is not https. */
export function httpsHost(url: unknown): string | null {
  if (typeof url !== "string" || url.length > 4096) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username !== "" || parsed.password !== "") return null;
    return parsed.hostname.toLowerCase();
  } catch {
    return null;
  }
}

/** An address as `scheme://host:port`, the port always written, or null when it is not http or https. */
export function originOf(url: unknown): string | null {
  if (typeof url !== "string" || url.length > 4096) return null;
  try {
    const parsed = new URL(url);
    if ((parsed.protocol !== "https:" && parsed.protocol !== "http:") || parsed.username !== "" || parsed.password !== "") return null;
    const port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
    return `${parsed.protocol}//${parsed.hostname.toLowerCase()}:${port}`;
  } catch {
    return null;
  }
}

/**
 * Null when the app's grant covers the call, or the refusal to send back.
 * This is the early check: it saves a round trip and tells the author what to
 * change. The server makes the same check and its answer is the one that counts.
 */
export function refusalFor(call: string, args: unknown, grant: Grant): BridgeError | null {
  if (!isCall(call)) return refuse("invalid", REFUSALS.unknownCall(call.slice(0, 40).replace(/[^A-Za-z0-9._-]/g, "?")));
  const rule = CALLS[call] as CallRule;
  if (rule.full && grant.tier !== "full") return refuse("not_allowed", REFUSALS.needsFull(call));
  if (rule.needs === undefined) return null;
  if (rule.needs !== "net") {
    return grant.permissions.includes(rule.needs) ? null : refuse("not_allowed", REFUSALS.needsPermission(call, rule.needs));
  }
  // The hosts the owner agreed to at install, and those he gave the app himself since.
  const hosts = [
    ...grant.permissions.filter((permission) => permission.startsWith("net:")).map((permission) => permission.slice(4).toLowerCase()),
    ...(grant.ownerHosts ?? []),
  ];
  const url = isPlainObject(args) ? args.url : undefined;
  // Exactly an address on the owner's own network he let this app reach (the server checks it again).
  const origin = originOf(url);
  if (origin !== null && (grant.ownNetwork ?? []).includes(origin)) return null;
  if (typeof url !== "string") {
    // The server refuses a missing address; here only the permission is checked.
    return hosts.length > 0 ? null : refuse("not_allowed", REFUSALS.needsPermission(call, "net:<host>"));
  }
  const host = httpsHost(url);
  if (host === null) return refuse("not_allowed", REFUSALS.needsHttps);
  return hosts.includes(host) ? null : refuse("not_allowed", REFUSALS.needsHost(host.slice(0, 80).replace(/[^a-z0-9.-]/g, "?")));
}

// ---------------------------------------------------------------- the rate limit

export type Take = { ok: true } | { ok: false; retry_after_s: number; silent: boolean };

/**
 * A token bucket: `callsPerSecond` requests a second, with a burst of the same
 * size. When refusals pile up past `silentAfter` in one second the answer is
 * `silent`: the shell stops replying at all, so a flood costs it nothing.
 */
export function createLimiter(limits: Pick<Limits, "callsPerSecond" | "silentAfter">, now: () => number = Date.now) {
  const capacity = Math.max(1, limits.callsPerSecond);
  let tokens = capacity;
  let last = now();
  let windowStart = last;
  let refused = 0;
  return {
    take(): Take {
      const at = now();
      const elapsed = Math.max(0, at - last);
      last = at;
      tokens = Math.min(capacity, tokens + (elapsed / 1000) * capacity);
      if (at - windowStart >= 1000) {
        windowStart = at;
        refused = 0;
      }
      if (tokens >= 1) {
        tokens -= 1;
        return { ok: true };
      }
      refused += 1;
      return { ok: false, retry_after_s: Math.max(1, Math.ceil((1 - tokens) / capacity)), silent: refused > limits.silentAfter };
    },
  };
}

// ---------------------------------------------------------------- talking to the server

/** `/api/apps/<id>/bridge/<call>`, or null unless both parts are exactly an app id and a listed call. */
export function bridgePath(appId: string, call: string): string | null {
  if (!isAppId(appId) || !isCall(call) || CALLS[call]?.shell) return null;
  return `/api/apps/${appId}/bridge/${call}`;
}

/**
 * The body of a relayed call. The app is the one in the address and the call is named
 * twice so the server can refuse a request whose body and address disagree.
 */
export function bridgeBody(call: string, args: unknown): { jimmyhq: number; call: string; args: unknown } {
  return { jimmyhq: BRIDGE_VERSION, call, args: args ?? {} };
}

/** `/api/apps/<id>/mount`, or null for anything that is not an app id. */
export function mountPath(appId: string): string | null {
  return isAppId(appId) ? `/api/apps/${appId}/mount` : null;
}

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === "string" && (ERROR_CODES as readonly string[]).includes(value);
}

/** The bridge error for a failed HTTP request: the status picks the code, the server's sentence is kept. */
export function errorFromFailure(status: number, detail: string): BridgeError {
  const code: ErrorCode =
    status === 0 || status === 502 || status === 503 || status === 504
      ? "unavailable"
      : status === 401 || status === 403
        ? "not_allowed"
        : status === 404
          ? "not_found"
          : status === 413
            ? "too_large"
            : status === 429
              ? "quota"
              : status === 400 || status === 422
                ? "invalid"
                : "internal";
  if (code === "unavailable") return refuse(code, REFUSALS.unreachable);
  if (code === "internal") return refuse(code, REFUSALS.internal);
  return refuse(code, detail.slice(0, 500) || REFUSALS.internal);
}

export type Outcome = { ok: true; result: unknown } | { ok: false; error: BridgeError };

/**
 * Reads the server's answer to a bridge call: `{ok: true, result}` or
 * `{ok: false, error: {code, message, retry_after_s?, owner_message?}}`. Anything else is an
 * internal error; an unknown code becomes `internal` and extra fields are dropped.
 */
export function readReply(raw: unknown): Outcome {
  if (!isPlainObject(raw) || typeof raw.ok !== "boolean") return { ok: false, error: refuse("internal", REFUSALS.internal) };
  if (raw.ok) return { ok: true, result: raw.result === undefined ? null : raw.result };
  const error = isPlainObject(raw.error) ? raw.error : {};
  const code = isErrorCode(error.code) ? error.code : "internal";
  const message = typeof error.message === "string" && error.message ? error.message.slice(0, 500) : REFUSALS.internal;
  const retry = typeof error.retry_after_s === "number" && Number.isFinite(error.retry_after_s) && error.retry_after_s >= 0 ? error.retry_after_s : undefined;
  const failure = refuse(code, message, retry);
  if (typeof error.owner_message === "string" && error.owner_message) failure.owner_message = error.owner_message.slice(0, 500);
  return { ok: false, error: failure };
}

// ---------------------------------------------------------------- the relay

export type RelayOptions = {
  /** The app this frame was mounted for. Set by the shell; no message can change it. */
  appId: string;
  grant: Grant;
  /** Sends one call to the server as `app:<appId>` and resolves to its raw answer. May throw `{status, detail}`. */
  send: (appId: string, call: string, args: unknown) => Promise<unknown>;
  /** Posts one response to the frame. */
  post: (response: BridgeResponse) => void;
  /** The calls the shell answers itself (`ui.*`, `files.*`, `speech.*`). Each returns the result, or a promise of it, or throws a BridgeError. */
  shell?: Readonly<Record<string, (args: unknown) => unknown>>;
  limits?: Partial<Limits>;
  now?: () => number;
};

export type Relay = {
  /** Handles one message from the frame's port. Never throws. */
  handle: (raw: unknown) => Promise<void>;
  /** The frame is gone: nothing more is sent to the server and nothing more is posted. */
  close: () => void;
  readonly closed: boolean;
  readonly inFlight: number;
};

function failureOf(thrown: unknown): BridgeError {
  if (isPlainObject(thrown) && isErrorCode(thrown.code) && typeof thrown.message === "string") {
    const error = refuse(thrown.code, thrown.message);
    return typeof thrown.owner_message === "string" && thrown.owner_message ? { ...error, owner_message: thrown.owner_message.slice(0, 500) } : error;
  }
  if (thrown !== null && typeof thrown === "object" && "status" in thrown && typeof thrown.status === "number") {
    const detail = "detail" in thrown && typeof thrown.detail === "string" ? thrown.detail : "";
    return errorFromFailure(thrown.status, detail);
  }
  return refuse("internal", REFUSALS.internal);
}

/**
 * The path every message from a frame takes, in order: rate limit, shape and
 * size, permission, one answer per id, then the server. The app id is the one
 * the relay was made with. Each accepted request gets exactly one response;
 * a message that cannot be matched to a request gets none.
 */
export function createRelay(options: RelayOptions): Relay {
  const limits: Limits = { ...DEFAULT_LIMITS, ...options.limits };
  const limiter = createLimiter(limits, options.now);
  const waiting = new Set<RequestId>();
  let closed = false;

  const reply = (response: BridgeResponse) => {
    if (closed) return;
    try {
      options.post(response);
    } catch {
      // The port closed under us: the frame is gone and there is nobody to tell.
    }
  };

  const run = async (request: BridgeRequest): Promise<Outcome> => {
    const local = CALLS[request.call]?.shell ? options.shell?.[request.call] : undefined;
    if (CALLS[request.call]?.shell) {
      if (!local) return { ok: false, error: refuse("unavailable", REFUSALS.unreachable) };
      return { ok: true, result: (await local(request.args)) ?? null };
    }
    return readReply(await options.send(options.appId, request.call, request.args));
  };

  return {
    get closed() {
      return closed;
    },
    get inFlight() {
      return waiting.size;
    },
    close() {
      closed = true;
      waiting.clear();
    },
    async handle(raw: unknown): Promise<void> {
      if (closed) return;
      const turn = limiter.take();
      if (!turn.ok) {
        const id = turn.silent ? null : requestId(raw);
        if (id !== null) reply({ id, ok: false, error: refuse("quota", REFUSALS.flood, turn.retry_after_s) });
        return;
      }
      let parsed: ParsedRequest;
      try {
        parsed = parseRequest(raw, limits);
      } catch {
        // Reading the message itself failed. There is nothing to answer.
        return;
      }
      if (!parsed.ok) {
        if (parsed.id !== null) reply({ id: parsed.id, ok: false, error: parsed.error });
        return;
      }
      const request = parsed.request;
      const refusal = refusalFor(request.call, request.args, options.grant);
      if (refusal) return reply({ id: request.id, ok: false, error: refusal });
      if (waiting.has(request.id)) return reply({ id: request.id, ok: false, error: refuse("invalid", REFUSALS.duplicate) });
      if (waiting.size >= limits.maxInFlight) return reply({ id: request.id, ok: false, error: refuse("quota", REFUSALS.busy, 1) });

      waiting.add(request.id);
      let outcome: Outcome;
      try {
        outcome = await run(request);
      } catch (thrown) {
        outcome = { ok: false, error: failureOf(thrown) };
      }
      // After close the set is empty: an answer that arrives late is dropped.
      if (!waiting.delete(request.id)) return;
      reply(outcome.ok ? { id: request.id, ok: true, result: outcome.result } : { id: request.id, ok: false, error: outcome.error });
    },
  };
}

// ---------------------------------------------------------------- what the shell tells the frame

export const WEEK_STARTS = ["monday", "sunday", "saturday"] as const;
export type WeekStart = (typeof WEEK_STARTS)[number];

/**
 * What the server answers when the shell mounts a screen (`POST /api/apps/<id>/mount`):
 * where the frame's files are, what the owner granted, and the two things an app is told
 * about the owner. The grant comes from here, the owner-only answer, and never from the frame.
 */
export type Mounted = {
  /** The frame's address, already checked. */
  src: string;
  version: string;
  grant: Grant;
  /** An IANA name, or "" when the owner has not set one. */
  timezone: string;
  weekStart: WeekStart;
  /** The largest picture the owner may give this app, in bytes (`apps.upload_max_mb`). */
  uploadMaxBytes: number;
  /** The longest side of the small version `files.thumbnail` makes, in pixels (`apps.thumbnail_px`). */
  thumbnailPx: number;
  /** The full view `files.picture` makes: longest side (`apps.picture_px`), largest kept picture it reads (`apps.picture_max_mb`) and how many a minute (`apps.pictures_per_minute`). */
  picture: { px: number; maxBytes: number; perMinute: number };
  /** The longest recording `speech.dictate` makes for this app, in seconds (`apps.dictate_max_s`). */
  dictateMaxS: number;
  /** The manifest says private: its words may only go to a speech engine on the owner's own machines. */
  private: boolean;
  /** What `hq.ui.save` may do: the largest file (`apps.save_max_mb`), files per touch (`apps.save_parts`), web pages (`apps.save_html`), files a minute (`apps.save_per_minute`). */
  save: SaveLimits;
};

/** What the server takes when an older one sends no limit: its default, 25 MB. */
export const DEFAULT_UPLOAD_MAX_MB = 25;
/** The longest app dictation when an older server sends no limit: its default, 5 minutes. */
export const DEFAULT_DICTATE_MAX_S = 300;

function wholeAbove0(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : fallback;
}

const LOCAL_ORIGIN = /^https?:\/\/[a-z0-9.\-[\]:]{1,300}:\d{1,5}$/;

/** The owner's local addresses from the mount answer, as `{ownNetwork}` only when there are any. */
function localOf(raw: unknown): { ownNetwork?: string[] } {
  const listed = Array.isArray(raw) ? raw.filter((item): item is string => typeof item === "string" && LOCAL_ORIGIN.test(item)).slice(0, 16) : [];
  return listed.length > 0 ? { ownNetwork: listed } : {};
}

const OWNER_HOST = /^[a-z0-9][a-z0-9.\-]{0,252}$/;

/** The hosts the owner gave the app, from the mount answer, as `{ownerHosts}` only when there are any. */
function ownerHostsOf(raw: unknown): { ownerHosts?: string[] } {
  const listed = Array.isArray(raw)
    ? raw.filter((item): item is string => typeof item === "string").map((item) => item.toLowerCase()).filter((item) => OWNER_HOST.test(item)).slice(0, 16)
    : [];
  return listed.length > 0 ? { ownerHosts: listed } : {};
}

/** Reads the mount answer, or null when it is not one the frame can be opened from. */
export function readMount(raw: unknown): Mounted | null {
  if (!isPlainObject(raw)) return null;
  const src = frameSrc(raw.mount, raw.entry);
  if (!src) return null;
  const permissions = Array.isArray(raw.permissions) ? raw.permissions.filter((item): item is string => typeof item === "string").slice(0, 64) : [];
  const zone = typeof raw.timezone === "string" && /^[A-Za-z0-9_+\-/]{1,64}$/.test(raw.timezone) ? raw.timezone : "";
  const week = (WEEK_STARTS as readonly unknown[]).includes(raw.week_start) ? (raw.week_start as WeekStart) : "monday";
  return {
    src,
    version: typeof raw.version === "string" ? raw.version.slice(0, 40) : "",
    grant: { tier: raw.tier === "full" ? "full" : "sandboxed", permissions, ...localOf(raw.local_addresses), ...ownerHostsOf(raw.owner_hosts) },
    timezone: zone,
    weekStart: week,
    uploadMaxBytes:
      (typeof raw.upload_max_mb === "number" && Number.isSafeInteger(raw.upload_max_mb) && raw.upload_max_mb > 0 ? raw.upload_max_mb : DEFAULT_UPLOAD_MAX_MB) * 1024 * 1024,
    thumbnailPx: clampPx(raw.thumbnail_px),
    picture: {
      px: clampPicturePx(raw.picture_px),
      maxBytes: wholeAbove0(raw.picture_max_mb, PICTURE_DEFAULT_MAX_MB) * 1024 * 1024,
      perMinute: wholeAbove0(raw.pictures_per_minute, PICTURE_DEFAULT_PER_MINUTE),
    },
    dictateMaxS:
      typeof raw.dictate_max_s === "number" && Number.isSafeInteger(raw.dictate_max_s) && raw.dictate_max_s > 0 ? raw.dictate_max_s : DEFAULT_DICTATE_MAX_S,
    // Anything but a plain false is taken as private: the stricter rule is the safe guess.
    private: raw.private !== false,
    save: {
      maxBytes: wholeAbove0(raw.save_max_mb, SAVE_DEFAULT_MAX_MB) * 1024 * 1024,
      maxParts: Math.min(wholeAbove0(raw.save_parts, SAVE_DEFAULT_PARTS), SAVE_MAX_PARTS),
      // An older server says nothing: the default is on, as the server's is. Only a plain false turns it off.
      html: raw.save_html !== false,
      perMinute: wholeAbove0(raw.save_per_minute, SAVE_DEFAULT_PER_MINUTE),
    },
  };
}

/** What the owner lets an app save with `ui.save`, as `hq.ready` tells the page: the settings `apps.save_max_mb`, `apps.save_parts`, `apps.save_per_minute` and `apps.save_html`. */
export type ReadySave = { max_mb: number; parts: number; per_minute: number; html: boolean };

export type ReadyData = {
  app: string;
  version: string;
  theme: "light" | "dark";
  timezone: string;
  week_start: WeekStart;
  permissions: string[];
  save?: ReadySave;
};

/** The save limits as the page is told them: whole megabytes, so a page can show "at most 8 MB". */
export function readySave(limits: { maxBytes: number; maxParts: number; perMinute: number; html: boolean }): ReadySave {
  return { max_mb: Math.max(1, Math.floor(limits.maxBytes / (1024 * 1024))), parts: limits.maxParts, per_minute: limits.perMinute, html: limits.html };
}

/**
 * Everything the frame learns about where it is running: its own id, version
 * and permissions, the theme, and the owner's time zone and the day their week
 * starts on, and, when given, what the owner lets it save (`save`). Nothing else about
 * the owner, and nothing about the session, the address of the shell or any other app.
 */
export function readyEvent(
  app: { id: string; version: string; permissions: readonly string[] },
  theme: "light" | "dark",
  timezone: string,
  weekStart: WeekStart = "monday",
  save?: { maxBytes: number; maxParts: number; perMinute: number; html: boolean },
): BridgeEvent {
  const data: ReadyData = { app: app.id, version: app.version, theme, timezone, week_start: weekStart, permissions: [...app.permissions] };
  if (save) data.save = readySave(save);
  return { event: "ready", data };
}

export function themeEvent(theme: "light" | "dark"): BridgeEvent {
  return { event: "theme", data: { theme } };
}

export function visibleEvent(visible: boolean): BridgeEvent {
  return { event: "visible", data: { visible } };
}

/**
 * The event an app's frame is owed for something on the live feed, or null.
 * Today that is one thing: a change to one of the app's own settings. The
 * value is not sent; the app asks for it with `settings.get`.
 */
export function eventFromFeed(appId: string, event: { kind: string; payload?: Record<string, unknown> }): BridgeEvent | null {
  if (event.kind !== "setting.updated" && event.kind !== "setting.reset") return null;
  const key = event.payload?.key;
  const prefix = `app.${appId}.`;
  if (typeof key !== "string" || !key.startsWith(prefix) || key.length === prefix.length) return null;
  return { event: "settings.changed", data: { key: key.slice(prefix.length) } };
}

// ---------------------------------------------------------------- calls the shell answers

export const TOAST_MAX_LENGTH = 200;
export const BADGE_MAX = 99;

/**
 * The words of a toast an app asked for, always led by the app's name so the
 * owner knows who is speaking. The text is shown as text, never as markup;
 * control characters are removed and it is cut to one short line.
 */
export function toastText(appName: string, args: unknown): string {
  const raw = isPlainObject(args) ? args.message : args;
  if (typeof raw !== "string") throw refuse("invalid", "You sent a toast with no message, so nothing was shown. Send {message: \"...\"}.");
  // eslint-disable-next-line no-control-regex
  const clean = raw.replace(/[\u0000-\u001f\u007f-\u009f‎‏‪-‮⁦-⁩]/g, " ").replace(/\s+/g, " ").trim();
  if (!clean) throw refuse("invalid", "You sent a toast with no message, so nothing was shown. Send {message: \"...\"}.");
  const cut = clean.length > TOAST_MAX_LENGTH ? `${clean.slice(0, TOAST_MAX_LENGTH - 1)}…` : clean;
  return `${appName}: ${cut}`;
}

/** The count for an app's sidebar badge: a whole number from 0 to 99, where 0 clears it. */
export function badgeCount(args: unknown): number {
  const raw = isPlainObject(args) ? args.count : args;
  if (raw === null || raw === undefined) return 0;
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 0) {
    throw refuse("invalid", "You sent a badge that is not a count, so nothing changed. Send {count: 3}, or {count: 0} to clear it.");
  }
  return Math.min(BADGE_MAX, Math.floor(raw));
}
