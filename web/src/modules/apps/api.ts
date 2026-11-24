// Typed client for the apps API (`/api/apps`). Every response type of the Apps screen
// lives here; `lib/wire.ts` reads what the server sends into these types, defensively, and
// `lib/requests.ts` holds every address and request body.
//
// All owner-only, JSON:
//   GET  /api/apps/_installed            -> { apps: [...] }
//   GET  /api/apps/_examples             -> { examples: [...] }   the apps that ship, with their group
//   GET  /api/apps/<id>                  -> one app, with what was consented to
//   POST /api/apps/<id>/enable|disable   -> the app
//   POST /api/apps/<id>/remove           body { keep_data, erase_record? }
//   POST /api/apps/<id>/update/plan      body {} | { folder } | { url?, commit | ref } -> what is new
//   POST /api/apps/<id>/update/confirm   body { plan, fingerprint, typed_name? } -> the app
//   POST /api/apps/<id>/update/check     -> { update }
//   POST /api/apps/<id>/rollback         body { version, restore_data } -> the app
//   POST /api/apps/_install/plan         body { folder } | { url, commit } | { example }
//   POST /api/apps/_install/confirm      body { plan, fingerprint, typed_name? } -> the app
//   POST /api/apps/_install/cancel       body { plan }
// A refusal is a status and one sentence in `detail`.

import { ApiError, reportUnauthorized, request } from "../../api.ts";
import { APPS_PATHS, cancelBody, confirmBody, planBody, removeBody } from "./lib/requests.ts";
import { readApp, readApps, readPlan, readRefusal } from "./lib/wire.ts";
import { readFirstParty } from "./lib/examples.ts";
import type { Example } from "./lib/examples.ts";
import { readHealth } from "./lib/health.ts";
import type { Health, HealthState } from "./lib/health.ts";
import { emittedPath, readEmitted } from "./lib/emitted.ts";
import { readUpdate, readUpdatePlan, rollbackBody, updateConfirmBody, updatePlanBody } from "./lib/update.ts";
import { readSecrets, secretBody, secretPath, secretsPath } from "./lib/secrets.ts";
import type { SecretState } from "./lib/secrets.ts";
import { hostPath, hostSecretPath, hostsPath, readHosts } from "./lib/hosts.ts";
import type { OwnerHosts } from "./lib/hosts.ts";
import { localCheckPath, localConfirmPath, localKeyPath, localPath, readCheck, readLocal } from "./lib/local.ts";
import type { LocalAddresses, LocalCheck } from "./lib/local.ts";
import { inboundPath, keyPath, readInbound, readMadeKey } from "./lib/inbound.ts";
import type { Inbound, MadeKey } from "./lib/inbound.ts";
import { picturePath, readUploaded, uploadFailure, uploadHeaders, uploadPath } from "./lib/uploads.ts";
import type { Picked, Uploaded } from "./lib/uploads.ts";
import type { BridgeError } from "./lib/bridge.ts";
import { makeThumbnail } from "./lib/thumbnail.ts";
import { declaredTooHeavy, readCapped, tooHeavy } from "./lib/picture.ts";
import type { ThumbEnv } from "./lib/thumbnail.ts";
import type { KeptVersion, PendingUpdate, UpdatePlan, UpdateSource } from "./lib/update.ts";

export type { KeptVersion, PendingUpdate, UpdatePlan, UpdateSource };

export type { Health, HealthState };

export type Tier = "sandboxed" | "full";

/**
 * Where an app comes from, as the server worked it out. "bundled" means its files are the
 * example that ships inside JimmyHQ, checked by the server by place and by content; it
 * is never taken from what an app says about itself.
 */
export type Provenance = "bundled" | "folder" | "git" | "store" | "development";

/** One sentence of the consent dialog, as `permissions.describe` writes it. */
export type PermissionLine = {
  /** As the manifest writes it ("tasks.write", "net:api.example.com"). */
  id: string;
  /** The row of the permission table ("net", "tasks.write", "always.full"). */
  permission?: string;
  host?: string | null;
  sentence: string;
  /** The dialog sets this line apart as a warning. */
  strong?: boolean;
  /** A line JimmyHQ adds itself; the app never asks for it. */
  always?: boolean;
};

/** A screen, a side-panel tab or a Wall tile an app adds. `entry` is "described" for a described app. */
export type Placement = { id: string; label: string; entry?: string };

export type AppSource = {
  kind: "folder" | "git" | "store" | "development" | string;
  /** For a git source: the address. */
  repo?: string | null;
  commit?: string | null;
  /** For a folder source: where it was copied from. */
  path?: string | null;
};

export type AppSummary = {
  id: string;
  name: string;
  version: string;
  tier: Tier;
  summary?: string;
  author?: string;
  licence?: string;
  enabled: boolean;
  /** Why it needs attention (failed to start, a file is missing); null or absent when well. */
  attention?: string | null;
  /** How a full app's program is doing (server/jimmyhq/apps/runtime.py `health`). */
  health?: HealthState | null;
  /** The newer version, when one is waiting for the owner's click. */
  update_available?: string | null;
  development?: boolean;
  /** True for an app with no code at all: only records and views. */
  described?: boolean;
  /** True when a full app's program is running. Absent means it is not. */
  running?: boolean;
  reviewed?: boolean;
  /** Where it came from, as the server worked it out. */
  provenance?: Provenance;
  screens?: Placement[];
  dock?: Placement[];
  wall?: Placement[];
  permissions?: PermissionLine[];
  source?: AppSource | null;
  installed_ms?: number | null;
};

/** What the owner agreed to, and when. */
export type Consent = {
  granted_ms: number | null;
  version?: string | null;
  /** The permission ids that were agreed to. */
  permissions?: string[];
};

/** What an app adds. Each part is a list of names or `{label}` objects, or a count. */
export type CreatedItem = string | { id?: string; label?: string; name?: string; kind?: string };
export type Creates = {
  screens?: CreatedItem[];
  dock?: CreatedItem[];
  wall?: CreatedItem[];
  settings?: CreatedItem[] | number;
  secrets?: CreatedItem[];
  tables?: CreatedItem[];
  cards?: CreatedItem[];
  memory_sources?: CreatedItem[];
  tools?: CreatedItem[];
  jobs?: CreatedItem[] | number | boolean;
};

export type AppDetail = AppSummary & {
  consent?: Consent | null;
  creates?: Creates | null;
  /** How much its own data takes, when the server can say. */
  data_bytes?: number | null;
  /** Where its data lives, as the server words it. */
  data_dir?: string | null;
  /** The settings section holding its settings, when it declares any. */
  settings_section?: string | null;
  has_settings?: boolean;
  tree?: string | null;
  /** The update JimmyHQ found and has not put in place. */
  update?: PendingUpdate | null;
  /** The versions kept, the one in use first. */
  versions?: KeptVersion[];
  /** The addresses on the owner's own network it may reach: named when a rollback would take them back. */
  local_addresses?: string[];
  /** Why a full app runs with JimmyHQ's own Python, when it does. */
  environment_note?: string | null;
  /** Who reads what: the feeds it offers other apps and the ones it reads, as sentences. */
  feeds?: string[];
  /** Where the app expects more of a host than the owner allows now (`net_expects`), as sentences. */
  net_notes?: string[];
};

export type InstallSource =
  | { kind: "folder"; path: string }
  | { kind: "git"; url: string; commit: string }
  /** One of the example apps that ship with JimmyHQ, by its id. */
  | { kind: "example"; id: string };

/** Step one's answer: what the app is, what it asks for and what installing would create. */
export type InstallPlan = {
  /** The server's name for this plan. Step two sends it back with the fingerprint. */
  plan: string;
  /** Step two sends this back: it installs exactly what was shown. */
  fingerprint: string;
  /** Where it comes from, with the commit the server pinned. */
  source: InstallSource;
  /** The server wants the app's name typed before it installs (an unreviewed full app). */
  needs_typed_name: boolean;
  app: {
    id: string;
    name: string;
    version: string;
    tier: Tier;
    summary?: string;
    author?: string;
    licence?: string;
    api?: string;
  };
  reviewed: boolean;
  /** Where it comes from, as the server worked it out. Only the server can say "bundled". */
  provenance: Provenance;
  permissions: PermissionLine[];
  creates: Creates | null;
  /** The version already installed under this id, if any. */
  installed_version: string | null;
  /** True for an app with no code at all. */
  described: boolean;
  /** The sentence for an app that asks to keep more than the usual storage, or null. */
  storage?: string | null;
  /** The installed apps that will read the feeds it offers, as sentences. */
  feeds?: string[];
};

export type Problem = { field: string; message: string };

export type RefusalKind = "manifest" | "setting" | "other";

/** A refusal of an install step, with the parser's problems when it gave a list. */
export class InstallRefused extends ApiError {
  problems: Problem[];
  code: string | null;
  kind: RefusalKind;

  constructor(status: number, detail: string, problems: Problem[], code: string | null, kind: RefusalKind) {
    super(status, detail);
    this.name = "InstallRefused";
    this.problems = problems;
    this.code = code;
    this.kind = kind;
  }
}

export type RemoveChoice = {
  keepData: boolean;
  /** Also erase what the app wrote on the record. Left out, the server erases it exactly when the data is deleted. */
  eraseRecord?: boolean;
};

// The install steps read the whole error body: the parser's list of problems is not in
// the one-line `detail` that `request` keeps.
async function sendInstall(path: string, body: unknown): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server.");
  }
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    if (response.status === 401) reportUnauthorized();
    const refusal = readRefusal(response.status, parsed);
    throw new InstallRefused(response.status, refusal.message, refusal.problems, refusal.code, refusal.kind);
  }
  if (parsed === null) throw new ApiError(response.status, "The server sent an unreadable response.");
  return parsed;
}

/** Reads a refusal's one sentence without ever reading anything else of the answer. */
async function detailOf(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    return body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string" ? (body as { detail: string }).detail : "";
  } catch {
    return "";
  }
}

/**
 * Sends one picture the owner picked, as the owner's session, to the app the shell mounted.
 * Resolves to what the page is told, or throws the BridgeError the page is given.
 */
export async function uploadPicture(appId: string, file: Picked): Promise<Uploaded> {
  const path = uploadPath(appId);
  if (!path) throw { code: "not_found", message: "There is no app with that id. Nothing was kept." } satisfies BridgeError;
  let response: Response;
  try {
    response = await fetch(path, { method: "POST", credentials: "same-origin", headers: { Accept: "application/json", ...uploadHeaders(file.name) }, body: file });
  } catch {
    throw uploadFailure(0, "JimmyHQ could not be reached, so the picture was not kept. Try again in a moment.");
  }
  if (!response.ok) {
    if (response.status === 401) reportUnauthorized();
    throw uploadFailure(response.status, await detailOf(response));
  }
  const got = readUploaded(await response.json().catch(() => null));
  if (!got) throw uploadFailure(500, "");
  return got;
}

/** One of an app's own pictures, as a small Blob (`px` on its longest side, at most) for the page to show. */
export async function pictureBlob(appId: string, ref: string, px: number, env: ThumbEnv): Promise<Blob> {
  const path = picturePath(appId, ref);
  if (!path) throw { code: "invalid", message: "That is not the ref of a picture your app was given, so nothing was shown." } satisfies BridgeError;
  let response: Response;
  try {
    response = await fetch(path, { credentials: "same-origin" });
  } catch {
    throw uploadFailure(0, "JimmyHQ could not be reached, so the picture was not shown. Try again in a moment.");
  }
  if (!response.ok) throw uploadFailure(response.status, await detailOf(response));
  return makeThumbnail(await response.blob(), px, env);
}

/**
 * One of an app's own pictures, large (`px` on its longest side, at most), for a full view.
 * The same path as `pictureBlob`, with a cap on the bytes read: over `maxBytes` the body is
 * not read, or reading stops, and `too_large` is thrown (lib/picture.ts).
 */
export async function fullPictureBlob(appId: string, ref: string, px: number, maxBytes: number, env: ThumbEnv): Promise<Blob> {
  const path = picturePath(appId, ref);
  if (!path) throw { code: "invalid", message: "That is not the ref of a picture your app keeps, so nothing was shown." } satisfies BridgeError;
  let response: Response;
  try {
    response = await fetch(path, { credentials: "same-origin" });
  } catch {
    throw uploadFailure(0, "JimmyHQ could not be reached, so the picture was not shown. Try again in a moment.");
  }
  if (!response.ok) throw uploadFailure(response.status, await detailOf(response));
  if (declaredTooHeavy(response.headers.get("content-length"), maxBytes)) {
    await response.body?.cancel().catch(() => undefined);
    throw tooHeavy(maxBytes);
  }
  const type = response.headers.get("content-type") ?? "application/octet-stream";
  const reader = response.body?.getReader();
  const parts = reader ? await readCapped({ read: () => reader.read(), cancel: () => reader.cancel() }, maxBytes) : [];
  return makeThumbnail(new Blob(parts as unknown as Uint8Array<ArrayBuffer>[], { type }), px, env);
}

export const secretsApi = {
  list: async (appId: string): Promise<SecretState[]> => {
    const path = secretsPath(appId);
    return path ? readSecrets(await request<unknown>("GET", path)) : [];
  },
  set: async (appId: string, name: string, value: string): Promise<void> => {
    const path = secretPath(appId, name);
    if (!path) throw new ApiError(404, "That secret is not one this app declares. Nothing was saved.");
    await request<unknown>("PUT", path, { body: secretBody(value) });
  },
  clear: async (appId: string, name: string): Promise<void> => {
    const path = secretPath(appId, name);
    if (!path) throw new ApiError(404, "That secret is not one this app declares. Nothing was cleared.");
    await request<unknown>("DELETE", path);
  },
};

export const hostsApi = {
  list: async (appId: string): Promise<OwnerHosts> => {
    const path = hostsPath(appId);
    return path ? readHosts(await request<unknown>("GET", path)) : { declared: null, hosts: [] };
  },
  add: async (appId: string, host: string): Promise<OwnerHosts> => {
    const path = hostsPath(appId);
    if (!path) throw new ApiError(404, "That is not an installed app. Nothing was added.");
    return readHosts(await request<unknown>("POST", path, { body: { host: host.trim() } }));
  },
  remove: async (appId: string, host: string): Promise<OwnerHosts> => {
    const path = hostPath(appId, host);
    if (!path) throw new ApiError(404, "That address is not one this app was given. Nothing was removed.");
    return readHosts(await request<unknown>("DELETE", path));
  },
  setSecret: async (appId: string, host: string, name: string, value: string): Promise<void> => {
    const path = hostSecretPath(appId, host, name);
    if (!path) throw new ApiError(404, "That secret is not one this app keeps for that address. Nothing was saved.");
    await request<unknown>("PUT", path, { body: secretBody(value) });
  },
  clearSecret: async (appId: string, host: string, name: string): Promise<void> => {
    const path = hostSecretPath(appId, host, name);
    if (!path) throw new ApiError(404, "That secret is not one this app keeps for that address. Nothing was cleared.");
    await request<unknown>("DELETE", path);
  },
};

/** Addresses on the owner's own network he lets one app reach (lib/local.ts). */
export const localApi = {
  list: async (appId: string): Promise<LocalAddresses> => {
    const path = localPath(appId);
    return path ? readLocal(await request<unknown>("GET", path)) : { max: 0, addresses: [] };
  },
  /** Where an address leads now. Nothing is added or changed. */
  check: async (appId: string, address: string): Promise<LocalCheck> => {
    const path = localCheckPath(appId);
    if (!path) throw new ApiError(404, "That is not an installed app. Nothing was added.");
    const read = readCheck(await request<unknown>("POST", path, { body: { address: address.trim() } }));
    if (!read) throw new ApiError(502, "The server's answer about that address could not be read, so nothing was added. Try again.");
    return read;
  },
  /** Add exactly what the owner was shown: the server refuses it if the name leads elsewhere by now. */
  add: async (appId: string, check: LocalCheck): Promise<LocalAddresses> => {
    const path = localPath(appId);
    if (!path) throw new ApiError(404, "That is not an installed app. Nothing was added.");
    return readLocal(await request<unknown>("POST", path, { body: { address: check.address, resolved: check.resolved } }));
  },
  /** Agree to where a name the app holds leads now, as the owner was shown. */
  confirm: async (appId: string, check: LocalCheck): Promise<LocalAddresses> => {
    const path = localConfirmPath(appId, check.key);
    if (!path) throw new ApiError(404, "That address is not one this app was let reach. Nothing was changed.");
    return readLocal(await request<unknown>("POST", path, { body: { resolved: check.resolved } }));
  },
  remove: async (appId: string, key: string): Promise<LocalAddresses> => {
    const path = localKeyPath(appId, key);
    if (!path) throw new ApiError(404, "That address is not one this app was let reach. Nothing was removed.");
    return readLocal(await request<unknown>("DELETE", path));
  },
};

export const inboundApi = {
  get: async (appId: string): Promise<Inbound> => {
    const path = inboundPath(appId);
    return readInbound(path ? await request<unknown>("GET", path) : null);
  },
  makeKey: async (appId: string, label: string, inAddress = false): Promise<MadeKey> => {
    const path = inboundPath(appId);
    if (!path) throw new ApiError(404, "That is not an installed app. No key was made.");
    return readMadeKey(await request<unknown>("POST", `${path}/keys`, { body: { label: label.trim(), in_address: inAddress } }));
  },
  removeKey: async (appId: string, keyId: string): Promise<Inbound> => {
    const path = keyPath(appId, keyId);
    if (!path) throw new ApiError(404, "That key is not one of this app's. Nothing was removed.");
    return readInbound(await request<unknown>("DELETE", path));
  },
  setOpen: async (appId: string, open: boolean): Promise<Inbound> => {
    const path = inboundPath(appId);
    if (!path) throw new ApiError(404, "That is not an installed app. Nothing was changed.");
    return readInbound(await request<unknown>("PUT", `${path}/open`, { body: { open } }));
  },
};

export const appsApi = {
  list: async (): Promise<AppSummary[]> => readApps(await request<unknown>("GET", APPS_PATHS.installed)),
  /** The first-party apps that ship in this copy of JimmyHQ, whether or not they are installed. */
  firstParty: async (): Promise<Example[]> => readFirstParty(await request<unknown>("GET", APPS_PATHS.examples)),
  detail: async (id: string): Promise<AppDetail> => readApp(await request<unknown>("GET", APPS_PATHS.detail(id))),
  enable: async (id: string): Promise<AppSummary> => readApp(await request<unknown>("POST", APPS_PATHS.enable(id))),
  disable: async (id: string): Promise<AppSummary> => readApp(await request<unknown>("POST", APPS_PATHS.disable(id))),
  remove: (id: string, choice: RemoveChoice) => request<unknown>("POST", APPS_PATHS.remove(id), { body: removeBody(choice) }),
  plan: async (source: InstallSource): Promise<InstallPlan> => readPlan(await sendInstall(APPS_PATHS.plan, planBody(source)), source),
  install: async (plan: InstallPlan, typedName = ""): Promise<AppSummary> => readApp(await sendInstall(APPS_PATHS.confirm, confirmBody(plan, typedName))),
  /** Throws the fetched app away when the owner says no. Best effort: an unanswered plan runs out by itself. */
  cancel: (plan: InstallPlan) => request<unknown>("POST", APPS_PATHS.cancel, { body: cancelBody(plan) }).catch(() => undefined),
  planUpdate: async (id: string, source: UpdateSource): Promise<UpdatePlan> =>
    readUpdatePlan(await request<unknown>("POST", APPS_PATHS.updatePlan(id), { body: updatePlanBody(source) })),
  confirmUpdate: async (plan: UpdatePlan, typed = ""): Promise<AppDetail> =>
    readApp(await request<unknown>("POST", APPS_PATHS.updateConfirm(plan.app.id), { body: updateConfirmBody(plan, typed) })),
  cancelUpdate: (plan: UpdatePlan) => request<unknown>("POST", APPS_PATHS.cancel, { body: { plan: plan.plan } }).catch(() => undefined),
  checkUpdate: async (id: string): Promise<PendingUpdate | null> => {
    const answer = await request<unknown>("POST", APPS_PATHS.updateCheck(id));
    return readUpdate(answer && typeof answer === "object" ? (answer as { update?: unknown }).update : null);
  },
  rollback: async (id: string, choice: KeptVersion, restore: boolean): Promise<AppDetail> =>
    readApp(await request<unknown>("POST", APPS_PATHS.rollback(id), { body: rollbackBody(choice, restore) })),
  health: async (id: string): Promise<Health> => readHealth(await request<unknown>("GET", `${APPS_PATHS.health(id)}?lines=40`)),
  restart: async (id: string): Promise<Health> => readHealth(await request<unknown>("POST", APPS_PATHS.restart(id))),
  /** What a full app's program emitted for its open pages since `after`. */
  emitted: async (id: string, after: number) => {
    const path = emittedPath(id, after);
    if (!path) return { events: [], next: after };
    return readEmitted(await request<unknown>("GET", path), after);
  },
};
