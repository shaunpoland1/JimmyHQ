// Updating an app and rolling it back, in plain words. Pure: the dialogs only draw what
// this says. The server (server/jimmyhq/apps/update.py) decides what is new and what may
// be switched to; this only reads its answers and words them.

import type { PermissionLine, Tier } from "../api.ts";
import { carriedSentence } from "./local.ts";
import { readLines, storageSentence } from "./wire.ts";

type Bag = Record<string, unknown>;

function bag(value: unknown): Bag | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Bag) : null;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function whole(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** An update JimmyHQ found and has not put in place. */
export type PendingUpdate = {
  source: "git" | "example" | string;
  /** Known once the new version was fetched and read; until then only its commit is. */
  version: string | null;
  commit: string | null;
  /** Null until known. */
  needsConsent: boolean | null;
  /** Why it could not be put in place by itself, as the server says it. */
  problem: string | null;
  foundMs: number | null;
};

export function readUpdate(value: unknown): PendingUpdate | null {
  const found = bag(value);
  const source = found ? text(found.source) : undefined;
  if (!found || !source) return null;
  return {
    source,
    version: text(found.version) ?? null,
    commit: text(found.commit) ?? null,
    needsConsent: typeof found.needs_consent === "boolean" ? found.needs_consent : null,
    problem: text(found.problem) ?? null,
    foundMs: whole(found.found_at),
  };
}

/** A version kept beside the one in use, that Roll back can return to. */
export type KeptVersion = {
  /** The server's name for it, `<version>-<hash8>`: what Roll back sends. */
  name: string;
  version: string;
  current: boolean;
  usedMs: number | null;
  hasBackup: boolean;
  backupMs: number | null;
  /** The kind of app it is, when the server says. */
  tier: Tier | null;
};

const VERSION_NAME = /^[0-9A-Za-z.+-]{1,80}-[0-9a-f]{8}$/;

export function readVersions(value: unknown): KeptVersion[] {
  if (!Array.isArray(value)) return [];
  const out: KeptVersion[] = [];
  for (const item of value) {
    const entry = bag(item);
    const name = entry ? text(entry.name) : undefined;
    if (!entry || !name || !VERSION_NAME.test(name)) continue;
    out.push({
      name,
      version: text(entry.version) ?? name,
      current: entry.current === true,
      usedMs: whole(entry.used_at),
      hasBackup: entry.has_backup === true,
      backupMs: whole(entry.backup_at),
      tier: entry.tier === "full" || entry.tier === "sandboxed" ? entry.tier : null,
    });
  }
  return out;
}

/** The versions Roll back offers: every kept one but the one in use, newest first as the server sent them. */
export function rollbackChoices(versions: readonly KeptVersion[]): KeptVersion[] {
  return versions.filter((v) => !v.current);
}

/** Step one's answer for an update. Only what is new is listed in `ask`. */
export type UpdatePlan = {
  plan: string;
  fingerprint: string;
  app: { id: string; name: string; version: string; tier: Tier };
  fromVersion: string;
  /** What this version asks for that was not agreed to before: the only lines the dialog lists. */
  ask: PermissionLine[];
  removed: PermissionLine[];
  /** [old, new] when the tier changes. */
  tierChange: [string, string] | null;
  needsConsent: boolean;
  needsTypedName: boolean;
  /** The runs-as-you warning, when the app becomes a full one. */
  warning: string | null;
  /** The sentence for a version that asks to keep more than this app may now, or null. */
  storage?: string | null;
  /** The addresses on the owner's own network the app holds, and whether this version keeps them. */
  localAddresses: { addresses: string[]; kept: boolean } | null;
};

const ORIGIN = /^https?:\/\/[a-z0-9.\-[\]:]{1,300}:\d{1,5}$/;

/** A list of origins the server sent, keeping only well-formed ones. */
export function readOrigins(raw: unknown): string[] {
  return (Array.isArray(raw) ? raw : []).flatMap((item) => (typeof item === "string" && ORIGIN.test(item) ? [item] : [])).slice(0, 32);
}

function readCarried(raw: unknown): { addresses: string[]; kept: boolean } | null {
  const body = bag(raw);
  const addresses = body ? readOrigins(body.addresses) : [];
  // Anything but a plain "kept" is read as taken back: never promised when unsure.
  return body && addresses.length > 0 ? { addresses, kept: body.kept === true } : null;
}

export function readUpdatePlan(raw: unknown): UpdatePlan {
  const body = bag(raw);
  const update = body ? bag(body.update) : null;
  const app = body ? bag(body.app) : null;
  const plan = body ? text(body.plan) : undefined;
  const fingerprint = body ? text(body.fingerprint) : undefined;
  const id = app ? text(app.id) : undefined;
  if (!body || !update || !app || !plan || !fingerprint || !id) {
    throw new Error("The server sent an update this screen cannot read, so nothing was changed.");
  }
  const tier = Array.isArray(update.tier) && update.tier.length === 2 ? [String(update.tier[0]), String(update.tier[1])] : null;
  const ask = readLines(update.ask);
  return {
    plan,
    fingerprint,
    app: { id, name: text(app.name) ?? id, version: text(app.version) ?? "", tier: app.tier === "full" ? "full" : "sandboxed" },
    fromVersion: text(update.from_version) ?? "",
    ask,
    removed: readLines(update.removed),
    tierChange: tier as [string, string] | null,
    // Never shown as "nothing new" when the server listed something, whatever else it says.
    needsConsent: update.needs_consent === true || ask.length > 0 || tier !== null,
    needsTypedName: body.needs_typed_name === true,
    warning: text(body.warning) ?? null,
    storage: storageSentence(update.storage),
    localAddresses: readCarried(update.local_addresses),
  };
}

const TIER_NAMES: Record<string, string> = { sandboxed: "a sandboxed app", full: "a full app" };

export type UpdateWords = {
  title: string;
  intro: string;
  /** Introduces the list of what is new, or says there is nothing. */
  newHeading: string;
  tier: string | null;
  removed: string | null;
  /** What happens to the addresses on the owner's own network the app holds, or null for none. */
  ownNetwork: string | null;
  button: string;
};

export function updateWords(plan: UpdatePlan): UpdateWords {
  const { name, version } = plan.app;
  const tier = plan.tierChange
    ? `It changes from ${TIER_NAMES[plan.tierChange[0]] ?? plan.tierChange[0]} to ${TIER_NAMES[plan.tierChange[1]] ?? plan.tierChange[1]}.`
    : null;
  return {
    title: `Update ${name} to ${version}?`,
    intro: `${name} ${plan.fromVersion} is stopped, its data is copied, and ${version} takes its place. You can roll back to ${plan.fromVersion} afterwards.`,
    newHeading: plan.needsConsent
      ? plan.ask.length > 0
        ? "This version asks for more. Only what is new is listed:"
        : "This version asks for more than you agreed to before."
      : "It asks for nothing new.",
    tier,
    removed: plan.removed.length > 0 ? `It no longer asks for ${plan.removed.length === 1 ? "one thing" : `${plan.removed.length} things`} it did before.` : null,
    ownNetwork: plan.localAddresses ? carriedSentence(name, plan.localAddresses.addresses, plan.localAddresses.kept) : null,
    button: plan.needsConsent ? "Agree and update" : "Update",
  };
}

/** Whether the Update button may be pressed: the name typed when it is asked for. */
export function canUpdate(plan: UpdatePlan, typed: string): boolean {
  return !plan.needsTypedName || typed.trim() === plan.app.name;
}

/** The badge's words, and a sentence for an update that is waiting. */
export function pendingSentence(name: string, update: PendingUpdate | null): string | null {
  if (!update) return null;
  const what = update.version ? `version ${update.version}` : update.commit ? `a new commit (${update.commit.slice(0, 8)})` : "a new version";
  if (update.problem) return `${name} has ${what} waiting, and it could not be put in place: ${update.problem}`;
  if (update.needsConsent) return `${name} has ${what} waiting. It asks for more, so it waits for you.`;
  return `${name} has ${what} waiting for you to press Update.`;
}

export function rollbackSentence(name: string, choice: KeptVersion, restore: boolean): string {
  return restore
    ? `${name} goes back to ${choice.version}, and its data goes back to how it was when ${choice.version} was replaced. Its data as it is now is kept as a copy.`
    : `${name} goes back to ${choice.version}. Its data stays as it is now.`;
}

/** What going back to a kept version does to the addresses on the owner's network: kept only for one of the same tier. */
export function rollbackLocalSentence(name: string, choice: KeptVersion, tierNow: Tier, addresses: readonly string[]): string | null {
  return carriedSentence(name, addresses, choice.tier === tierNow);
}

export function updatedToast(name: string, version: string): string {
  return `Updated ${name} to ${version}.`;
}

export function rolledBackToast(name: string, version: string): string {
  return `${name} is back on ${version}.`;
}

/** Where to update from, when JimmyHQ has found nothing: a folder, or a git address with a commit or branch. */
export type UpdateSource = { kind: "found" } | { kind: "folder"; path: string } | { kind: "git"; url: string; commitOrRef: string };

const COMMIT = /^[0-9a-f]{40}$/;

export function updatePlanBody(source: UpdateSource): Record<string, string> {
  if (source.kind === "found") return {};
  if (source.kind === "folder") return { folder: source.path.trim() };
  const at = source.commitOrRef.trim();
  const body: Record<string, string> = {};
  if (source.url.trim()) body.url = source.url.trim();
  if (COMMIT.test(at)) body.commit = at;
  else if (at) body.ref = at;
  return body;
}

export function updateConfirmBody(plan: Pick<UpdatePlan, "plan" | "fingerprint" | "needsTypedName">, typed: string): Record<string, string> {
  const body: Record<string, string> = { plan: plan.plan, fingerprint: plan.fingerprint };
  if (plan.needsTypedName) body.typed_name = typed.trim();
  return body;
}

export function rollbackBody(choice: Pick<KeptVersion, "name">, restore: boolean): { version: string; restore_data: boolean } {
  return { version: choice.name, restore_data: restore };
}
