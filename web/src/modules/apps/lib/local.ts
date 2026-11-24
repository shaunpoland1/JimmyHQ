// Addresses on the owner's own network he lets one app reach, on the app's page under Apps.
// Pure, so each rule is tested (web/tests/apps.local.test.ts). The server holds every rule
// again, resolves the name and is the one that decides.
//
//   GET    /api/apps/<id>/local-addresses              -> { max, warning, addresses: [{address, key, added_at, resolved}] }
//   POST   /api/apps/<id>/local-addresses/check        body { address } -> { address, key, resolved, warning, added, stored }
//   POST   /api/apps/<id>/local-addresses              body { address, resolved }: exactly what the owner was shown
//   POST   /api/apps/<id>/local-addresses/<key>/confirm body { resolved }: where it leads now, as shown
//   DELETE /api/apps/<id>/local-addresses/<key>

const APP_ID = /^[a-z][a-z0-9_]{1,30}$/;
const KEY = /^[0-9a-f]{16}$/;
// As the server writes one back: scheme, host and port, the port always written.
const ORIGIN = /^https?:\/\/[a-z0-9.\-[\]:]{1,300}:\d{1,5}$/;
// One address as the server writes it, IPv4 or IPv6, without brackets.
const IP = /^(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f]{0,4}(?::[0-9a-f.]{0,15}){2,8})$/i;
export const EXAMPLE = "http://192.0.2.10:8000";

/** `resolved`: where its host led when the owner added or last confirmed it. */
export type LocalAddress = { address: string; key: string; addedAt: number; resolved: string[] };
export type LocalAddresses = { max: number; addresses: LocalAddress[] };
/** Where an address leads now, before the owner agrees to it. `stored` is set for one the app holds already. */
export type LocalCheck = { address: string; key: string; resolved: string[]; added: boolean; stored: string[] | null };

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function localPath(appId: string): string | null {
  return APP_ID.test(appId) ? `/api/apps/${appId}/local-addresses` : null;
}

/** The address of one of them, by its key, or null unless both parts look exactly right. */
export function localKeyPath(appId: string, key: string): string | null {
  const base = localPath(appId);
  return base && KEY.test(key) ? `${base}/${key}` : null;
}

/** Where the owner's page asks where an address leads now. */
export function localCheckPath(appId: string): string | null {
  const base = localPath(appId);
  return base ? `${base}/check` : null;
}

/** Where the owner agrees to where one of them leads now. */
export function localConfirmPath(appId: string, key: string): string | null {
  const path = localKeyPath(appId, key);
  return path ? `${path}/confirm` : null;
}

function readIps(raw: unknown): string[] {
  return (Array.isArray(raw) ? raw : []).flatMap((ip) => (typeof ip === "string" && IP.test(ip) ? [ip] : [])).slice(0, 32);
}

/** What the server sent, keeping only well-formed parts. */
export function readLocal(raw: unknown): LocalAddresses {
  if (!isObject(raw)) return { max: 0, addresses: [] };
  const max = typeof raw.max === "number" && Number.isInteger(raw.max) && raw.max > 0 ? raw.max : 0;
  const addresses = (Array.isArray(raw.addresses) ? raw.addresses : []).flatMap((item): LocalAddress[] =>
    isObject(item) && typeof item.address === "string" && ORIGIN.test(item.address) && typeof item.key === "string" && KEY.test(item.key)
      ? [{ address: item.address, key: item.key, addedAt: typeof item.added_at === "number" ? item.added_at : 0, resolved: readIps(item.resolved) }]
      : [],
  );
  return { max, addresses };
}

/** The server's answer to a check, or null when it cannot be read: nothing is then offered to agree to. */
export function readCheck(raw: unknown): LocalCheck | null {
  if (!isObject(raw) || typeof raw.address !== "string" || !ORIGIN.test(raw.address)) return null;
  if (typeof raw.key !== "string" || !KEY.test(raw.key)) return null;
  const resolved = readIps(raw.resolved);
  if (resolved.length === 0) return null;
  return { address: raw.address, key: raw.key, resolved, added: raw.added === true, stored: raw.added === true ? readIps(raw.stored) : null };
}

/** The host of an origin as the server writes it, without brackets. */
export function hostOf(origin: string): string {
  return origin.replace(/^https?:\/\//, "").replace(/:\d+$/, "").replace(/^\[|\]$/g, "");
}

/** Whether the owner typed a name, which can be pointed elsewhere later, rather than an address. */
export function isName(origin: string): boolean {
  return !IP.test(hostOf(origin));
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && [...a].sort().join() === [...b].sort().join();
}

/** Whether a name now leads somewhere other than where the owner last agreed. */
export function moved(check: LocalCheck): boolean {
  return check.added && !sameSet(check.resolved, check.stored ?? []);
}

/** The line under an address on the app's page: where its name leads, as the owner agreed. */
export function leadsLine(item: LocalAddress): string | null {
  if (!isName(item.address)) return null;
  if (item.resolved.length === 0) return "Where it leads was not kept when it was added. Check it, and agree to where it leads, before the app can reach it.";
  return `Leads to ${item.resolved.join(", ")}.`;
}

/** Null when what the owner typed may be sent, or the sentence that says why not. */
export function localProblem(typed: string, have: LocalAddresses): string | null {
  const text = typed.trim();
  if (!text) return `Type the address with its port, such as ${EXAMPLE}. Nothing was added.`;
  if (text.includes("*")) return `Type one exact address, such as ${EXAMPLE}. Nothing was added.`;
  if (!/^https?:\/\//i.test(text)) return `Type the address with http:// or https:// in front, such as ${EXAMPLE}. Nothing was added.`;
  if (have.max > 0 && have.addresses.length >= have.max) return `This app may be given at most ${have.max} addresses on your own network, and has them. Nothing was added. Remove one first.`;
  return null;
}

/** The plain warning the Add button shows first, in JimmyHQ's own dialog, with where a name leads now. */
export function addWarning(appName: string, check: Pick<LocalCheck, "address" | "resolved">): { title: string; message: string } {
  const shown = check.address;
  const leads = isName(shown)
    ? ` ${hostOf(shown)} leads to ${check.resolved.join(", ")} just now. If it later leads anywhere else, ${appName} is stopped from reaching it until you agree to the new address here.`
    : ` ${hostOf(shown)} is an address, not a name, so it is used exactly as you typed it and cannot be pointed anywhere else later.`;
  return {
    title: `Let ${appName} reach ${shown}?`,
    message: `${appName} will be able to send to and read from ${shown} on your own network: exactly that address and port, and nothing else there.${leads} Anything that answers there will answer ${appName}. Only add an address you trust ${appName} with. You can remove it here at any time.`,
  };
}

/** The question when a name the owner added now leads somewhere else. */
export function movedQuestion(appName: string, check: LocalCheck): { title: string; message: string } {
  const host = hostOf(check.address);
  const was = check.stored && check.stored.length > 0 ? `When you added it, ${host} led to ${check.stored.join(", ")}. ` : "";
  return {
    title: `${host} leads somewhere else now`,
    message: `${was}It now leads to ${check.resolved.join(", ")}. ${appName} cannot reach ${check.address} until you agree. Agree only if you know why it moved, such as a computer on your network that was given a new address. ${appName} will then be able to send to and read from it there.`,
  };
}

/** What an update or a rollback does to the addresses on the owner's network, or null when there are none. */
export function carriedSentence(appName: string, addresses: readonly string[], kept: boolean): string | null {
  if (addresses.length === 0) return null;
  const listed = addresses.join(", ");
  return kept
    ? `${appName} keeps the ${addresses.length === 1 ? "address" : "addresses"} on your own network you let it reach: ${listed}. Remove any you no longer want on its page.`
    : `It becomes another kind of app, so the ${addresses.length === 1 ? "address" : "addresses"} on your own network you let ${appName} reach ${addresses.length === 1 ? "is" : "are"} taken back: ${listed}. Add ${addresses.length === 1 ? "it" : "them"} again on its page if you want it to reach ${addresses.length === 1 ? "it" : "them"}.`;
}

/** The question the Remove button asks first. */
export function removeLocalQuestion(appName: string, address: string): { title: string; message: string } {
  return {
    title: `Remove ${address}?`,
    message: `${appName} will no longer be able to reach ${address}. Anything it does there stops until you add it again.`,
  };
}
