// Hosts the owner gives an app himself, on the app's own Settings page: his own site's
// address, for an app whose manifest declares `owner_hosts`. Pure, so each rule is tested
// (web/tests/apps.hosts.test.ts). The server checks everything again, and resolves the name.
//
//   GET    /api/apps/<id>/hosts                        -> { declared: {purpose, max, next} | null,
//                                                          hosts: [{host, added_at, secrets}] }
//   POST   /api/apps/<id>/hosts                        body { host }
//   DELETE /api/apps/<id>/hosts/<host>
//   PUT    /api/apps/<id>/hosts/<host>/secrets/<name>  body { value }
//   DELETE /api/apps/<id>/hosts/<host>/secrets/<name>
//
// A secret kept for a host is write only, like any app secret: "Set" or "Not set".

import type { SecretState } from "./secrets.ts";

const APP_ID = /^[a-z][a-z0-9_]{1,30}$/;
const SECRET_NAME = /^[a-z][a-z0-9_]{0,39}$/;
// One exact public name, as the server writes it back: lower case, dots, no port.
const HOST = /^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,63}|xn--[a-z0-9-]{1,59})$/;
const EXAMPLE = "https://blog.example.com";

export type OwnerHost = { host: string; addedAt: number; secrets: SecretState[] };
export type OwnerHosts = { declared: { purpose: string; max: number; next?: string | null } | null; hosts: OwnerHost[] };

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function hostsPath(appId: string): string | null {
  return APP_ID.test(appId) ? `/api/apps/${appId}/hosts` : null;
}

/** The address of one host of one app, or null unless both parts look exactly right. */
export function hostPath(appId: string, host: string): string | null {
  const base = hostsPath(appId);
  return base && HOST.test(host) ? `${base}/${host}` : null;
}

export function hostSecretPath(appId: string, host: string, name: string): string | null {
  const base = hostPath(appId, host);
  return base && SECRET_NAME.test(name) ? `${base}/secrets/${name}` : null;
}

function readSecretStates(raw: unknown): SecretState[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item): SecretState[] => {
    if (!isObject(item) || typeof item.name !== "string" || !SECRET_NAME.test(item.name)) return [];
    return [
      {
        name: item.name,
        label: typeof item.label === "string" && item.label ? item.label.slice(0, 60) : item.name,
        help: typeof item.help === "string" ? item.help.slice(0, 300) : "",
        set: item.set === true,
      },
    ];
  });
}

/** What the server sent, keeping only well-formed parts. Anything like a value is dropped. */
export function readHosts(raw: unknown): OwnerHosts {
  if (!isObject(raw)) return { declared: null, hosts: [] };
  const d = raw.declared;
  const declared =
    isObject(d) && typeof d.purpose === "string" && typeof d.max === "number" && Number.isInteger(d.max) && d.max >= 1
      ? { purpose: d.purpose.slice(0, 200), max: d.max, next: typeof d.next === "string" && d.next.trim() ? d.next.slice(0, 200) : null }
      : null;
  const hosts = (Array.isArray(raw.hosts) ? raw.hosts : []).flatMap((item): OwnerHost[] => {
    if (!isObject(item) || typeof item.host !== "string" || !HOST.test(item.host)) return [];
    return [{ host: item.host, addedAt: typeof item.added_at === "number" ? item.added_at : 0, secrets: readSecretStates(item.secrets) }];
  });
  return { declared, hosts };
}

/**
 * Null when what the owner typed may be sent, or the sentence that says why not. The
 * server holds the same rules and resolves the name; this only saves a round trip.
 */
export function hostProblem(typed: string, have: OwnerHosts): string | null {
  const text = typed.trim();
  if (!text) return `Type your site's address, such as ${EXAMPLE}. Nothing was added.`;
  if (text.includes("*")) return `A wildcard cannot be given: type one exact address, such as ${EXAMPLE}. Nothing was added.`;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text) && !/^https:\/\//i.test(text)) {
    return `Only an https address can be given, so an app's requests are always encrypted. Nothing was added. Type it as ${EXAMPLE}.`;
  }
  if (have.declared && have.hosts.length >= have.declared.max) {
    return `This app may be given at most ${have.declared.max} of your addresses, and has them all. Nothing was added. Remove one first.`;
  }
  return null;
}

/** The question the Remove button asks first, in the app's ConfirmDialog. */
export function removeQuestion(appName: string, host: string): { title: string; message: string } {
  return {
    title: `Remove ${host}?`,
    message: `${appName} will no longer be able to send to or read from ${host}, and what you typed in for it is cleared. Anything it does there stops until you add it again.`,
  };
}

/** The question the Add button asks first: the owner reads what he is giving. */
export function addQuestion(appName: string, typed: string): { title: string; message: string } {
  const shown = typed.trim().replace(/^https:\/\//i, "").replace(/\/$/, "").toLowerCase();
  return {
    title: `Let ${appName} reach ${shown}?`,
    message: `${appName} will be able to send data to and read data from ${shown}, exactly as it does for the hosts you agreed to when you installed it. You can remove it here at any time.`,
  };
}
