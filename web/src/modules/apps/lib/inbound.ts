// Messages from the owner's devices, on a full app's page: the address a device sends to,
// the keys (names and times only: a key is in exactly one answer, the one that makes it),
// the app's own switch and the counts. Pure, so each rule is tested
// (web/tests/apps.inbound.test.ts). The server checks everything again.
//
//   GET    /api/apps/<id>/inbound               -> Inbound
//   POST   /api/apps/<id>/inbound/keys          body { label, in_address } -> { inbound, key, paths? }
//   DELETE /api/apps/<id>/inbound/keys/<key>    -> Inbound
//   PUT    /api/apps/<id>/inbound/open          body { open } -> Inbound

const APP_ID = /^[a-z][a-z0-9_]{1,30}$/;
const NAME = /^[a-z][a-z0-9_]{0,30}$/;
const KEY_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
export const LABEL_MAX = 60;
const KB_MAX = 1024;

/** Said wherever a key that travels in an address is shown. */
export const ADDRESS_WARNING = "Anyone who sees this address can send this app messages: the key is part of it. Give it only to the sender, and if anyone else sees it, remove the key and make a new one.";

export type InboundName = { name: string; label: string; help: string; array: boolean; maxKb: number | null };
export type InboundKey = { id: string; label: string; created: number; lastUsed: number | null; uses: number; inAddress: boolean };
export type Inbound = {
  declared: InboundName[];
  maxKb: number;
  granted: boolean;
  everywhere: boolean;
  open: boolean;
  path: string;
  keys: InboundKey[];
  keysMax: number;
  received: number;
  failed: number;
  lastError: string | null;
  lastErrorAt: number | null;
  waiting: number;
};
export type MadeKey = { inbound: Inbound; key: string; paths: { name: string; path: string }[] };

const NONE: Inbound = {
  declared: [],
  maxKb: 0,
  granted: false,
  everywhere: true,
  open: false,
  path: "",
  keys: [],
  keysMax: 0,
  received: 0,
  failed: 0,
  lastError: null,
  lastErrorAt: null,
  waiting: 0,
};

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
}

function time(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function text(value: unknown, most: number): string {
  return typeof value === "string" ? value.slice(0, most) : "";
}

export function inboundPath(appId: string): string | null {
  return APP_ID.test(appId) ? `/api/apps/${appId}/inbound` : null;
}

/** The address of one key of one app, or null unless both parts look exactly right. */
export function keyPath(appId: string, keyId: string): string | null {
  const base = inboundPath(appId);
  return base && KEY_ID.test(keyId) ? `${base}/keys/${keyId}` : null;
}

/** Where a device sends one kind of message: this page's own origin and the app's path. */
export function sendAddress(origin: string, appId: string, name: string): string | null {
  if (!APP_ID.test(appId) || !NAME.test(name)) return null;
  let base: URL;
  try {
    base = new URL(origin);
  } catch {
    return null;
  }
  if (base.protocol !== "http:" && base.protocol !== "https:") return null;
  return `${base.origin}/api/in/${appId}/${name}`;
}

function kb(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= KB_MAX ? value : null;
}

/** Where a sender that can only be given an address sends: the page's origin and the path the
 * server gave with the key in it, or null unless the path is exactly that app's, name's and key's. */
export function keyAddress(origin: string, appId: string, name: string, key: string, path: string): string | null {
  const plain = sendAddress(origin, appId, name);
  if (!plain || !/^[A-Za-z0-9_-]{8,200}$/.test(key) || path !== `/api/in/${appId}/${name}/k/${key}`) return null;
  return `${plain}/k/${key}`;
}

/** What the page says about one name's size, or null when there is nothing to say. */
export function sizeNote(item: InboundName, ownerKb: number): string | null {
  if (item.maxKb === null || ownerKb <= 0) return null;
  if (item.maxKb > ownerKb) return `The app expects messages of up to ${item.maxKb} KB, and you allow ${ownerKb} KB: larger ones are refused. Raise it under Settings, Apps if you want them.`;
  return `At most ${item.maxKb} KB a message.`;
}

/** What the server sent, keeping only well-formed parts. Anything that looks like a key is dropped. */
export function readInbound(raw: unknown): Inbound {
  if (!isObject(raw)) return NONE;
  const declared = Array.isArray(raw.declared)
    ? raw.declared.flatMap((item): InboundName[] =>
        isObject(item) && typeof item.name === "string" && NAME.test(item.name)
          ? [{ name: item.name, label: text(item.label, 60) || item.name, help: text(item.help, 300), array: item.array === true, maxKb: kb(item.max_kb) }]
          : [],
      )
    : [];
  const keys = Array.isArray(raw.keys)
    ? raw.keys.flatMap((item): InboundKey[] =>
        isObject(item) && typeof item.id === "string" && KEY_ID.test(item.id) && typeof item.label === "string"
          ? [{ id: item.id, label: item.label.slice(0, LABEL_MAX), created: time(item.created) ?? 0, lastUsed: time(item.last_used), uses: count(item.uses), inAddress: item.in_address === true }]
          : [],
      )
    : [];
  return {
    declared,
    maxKb: kb(raw.max_kb) ?? 0,
    granted: raw.granted === true,
    everywhere: raw.everywhere !== false,
    open: raw.open === true,
    path: typeof raw.path === "string" && raw.path.startsWith("/api/in/") ? raw.path : "",
    keys,
    keysMax: count(raw.keys_max),
    received: count(raw.received),
    failed: count(raw.failed),
    lastError: typeof raw.last_error === "string" && raw.last_error ? raw.last_error.slice(0, 300) : null,
    lastErrorAt: time(raw.last_error_at),
    waiting: count(raw.waiting),
  };
}

export function readMadeKey(raw: unknown): MadeKey {
  const key = isObject(raw) && typeof raw.key === "string" ? raw.key : "";
  const sent = isObject(raw) && isObject(raw.paths) ? raw.paths : {};
  const paths = Object.entries(sent).flatMap(([name, path]) => (NAME.test(name) && typeof path === "string" && key && path.endsWith(`/k/${key}`) ? [{ name, path }] : []));
  return { inbound: readInbound(isObject(raw) ? raw.inbound : null), key, paths };
}

/** The label as it will be kept, or the sentence that says why it cannot be. */
export function labelProblem(typed: string, have: Inbound): string | null {
  const label = typed.split(/\s+/).filter(Boolean).join(" ");
  if (!label) return "Give the key a name, such as “Home hub”, so you know which device holds it.";
  if (label.length > LABEL_MAX) return `A key's name is at most ${LABEL_MAX} letters.`;
  if (have.keys.some((key) => key.label.toLowerCase() === label.toLowerCase())) return `This app already has a key called “${label}”. Give each device's key its own name.`;
  if (have.keysMax > 0 && have.keys.length >= have.keysMax) return `This app has ${have.keysMax} keys, the most it may have. Remove one you no longer use.`;
  return null;
}

/** The plain question before a key is removed. */
export function removeQuestion(appName: string, label: string): { title: string; message: string } {
  return {
    title: `Remove the key “${label}”?`,
    message: `A device that sends with it is refused from now on, and ${appName} hears nothing more from it. This cannot be undone: to let the device in again, make it a new key.`,
  };
}

/** One line for the counts: how many came, failed and wait. */
export function countsLine(state: Inbound): string {
  const parts = [state.received === 1 ? "1 message taken" : `${state.received} messages taken`];
  if (state.failed > 0) parts.push(`${state.failed} the app failed on`);
  if (state.waiting > 0) parts.push(`${state.waiting} waiting for the app`);
  return `${parts.join(", ")}.`;
}

/** Why nothing gets in, when nothing does; null when the door is open. */
export function closedReason(state: Inbound): string | null {
  if (!state.granted) return "This app was not given “inbound” when it was installed. Update it to agree to it.";
  if (!state.everywhere) return "Messages from devices are switched off for every app, under Settings, Apps.";
  if (state.keys.length === 0) return "Closed: make a key for a device to open it.";
  if (!state.open) return "Closed: every message is refused until you switch it on. This door was switched off when the app was removed, or by you. Its keys still work once you switch it on.";
  return null;
}
