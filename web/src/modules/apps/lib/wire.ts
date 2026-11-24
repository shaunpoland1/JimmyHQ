// Reads what the apps API sends into the types the Apps screen draws from. Nothing here
// trusts a field to be present: a missing optional field is a default, and a missing
// required one is an error that says so.
//
// The server's names (server/jimmyhq/apps/install.py: `_row`, `detail`, `_plan`) are read
// first. A few older spellings are still accepted, so a fixture or a newer server that
// sends the screen's own names reads the same.

import type {
  AppDetail,
  AppSource,
  AppSummary,
  Consent,
  Creates,
  InstallPlan,
  InstallSource,
  PermissionLine,
  Placement,
  Problem,
  Provenance,
  RefusalKind,
  Tier,
} from "../api.ts";

import { feedLines } from "./feeds.ts";
import { healthState } from "./health.ts";
import { readOrigins, readUpdate, readVersions } from "./update.ts";

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

/**
 * Where an app comes from. "bundled" is taken only when the server says it in so many
 * words; anything else falls back to the kind of source, so an unknown word is never
 * read as JimmyHQ's own.
 */
export function provenanceOf(value: unknown, sourceKind: string | undefined): Provenance {
  if (value === "bundled") return "bundled";
  if (value === "git" || sourceKind === "git") return "git";
  if (value === "store" || sourceKind === "store") return "store";
  if (value === "development" || sourceKind === "development") return "development";
  return "folder";
}

function tierOf(value: unknown): Tier {
  return value === "full" ? "full" : "sandboxed";
}

/** The permission lines as the server wrote them. A bare id (no sentence) is shown as it is. */
export function readLines(value: unknown): PermissionLine[] {
  if (!Array.isArray(value)) return [];
  const lines: PermissionLine[] = [];
  for (const item of value) {
    if (typeof item === "string" && item !== "") {
      lines.push({ id: item, sentence: item });
      continue;
    }
    const line = bag(item);
    if (!line) continue;
    const sentence = text(line.sentence);
    const id = text(line.id) ?? text(line.permission);
    if (!sentence || !id) continue;
    const host = typeof line.host === "string" ? line.host : null;
    lines.push({
      id,
      permission: text(line.permission) ?? id.split(":")[0],
      host,
      sentence,
      strong: line.strong === true,
      always: line.always === true,
    });
  }
  return lines;
}

function readPlacements(value: unknown): Placement[] {
  if (!Array.isArray(value)) return [];
  const places: Placement[] = [];
  for (const item of value) {
    const place = bag(item);
    const id = place ? text(place.id) : undefined;
    if (!place || !id) continue;
    // The server says only whether JimmyHQ draws the screen itself, never a file's path.
    places.push({ id, label: text(place.label) ?? id, entry: place.described === true ? "described" : text(place.entry) });
  }
  return places;
}

/** Where an app came from. The server sends the kind as a word, with `repo` and `commit` beside it. */
function readSource(value: unknown, row: Bag): AppSource | null {
  if (typeof value === "string" && value !== "") {
    return { kind: value, repo: text(row.repo) ?? null, commit: text(row.commit) ?? null, path: null };
  }
  const source = bag(value);
  const kind = source ? text(source.kind) : undefined;
  if (!source || !kind) return null;
  return { kind, repo: text(source.repo) ?? text(source.url) ?? null, commit: text(source.commit) ?? null, path: text(source.path) ?? null };
}

/** The consent in force: the newest entry of the server's `consents`, oldest first. */
function readConsent(value: unknown, consents: unknown): Consent | null {
  const newest = Array.isArray(consents) && consents.length > 0 ? consents[consents.length - 1] : value;
  const consent = bag(newest);
  if (!consent) return null;
  const given = whole(consent.at) ?? whole(consent.granted_ms) ?? whole(consent.ts);
  const permissions = Array.isArray(consent.permissions) ? consent.permissions.filter((item): item is string => typeof item === "string") : undefined;
  return { granted_ms: given, version: text(consent.version) ?? null, permissions };
}

/** What an app adds. The server says `schedule: true` for background work; the screen calls that jobs. */
function readCreates(value: unknown): Creates | null {
  const creates = bag(value);
  if (!creates) return null;
  const { schedule, ...rest } = creates;
  return (schedule === undefined || rest.jobs !== undefined ? rest : { ...rest, jobs: schedule === true }) as Creates;
}

/** One installed app. `{ app: {...} }` is accepted as well as the bare object. */
export function readApp(raw: unknown): AppDetail {
  const outer = bag(raw);
  const body = bag(outer?.app) ?? outer;
  const id = body ? text(body.id) : undefined;
  if (!body || !id) throw new Error("The server sent an app without an id.");
  const health = healthState(body.health);
  const screens = readPlacements(body.screens);
  const dock = readPlacements(body.dock);
  const wall = readPlacements(body.wall);
  // `lines` are the consent dialog's sentences (the detail has them); the list has ids only.
  const lines = readLines(body.lines);
  return {
    id,
    name: text(body.name) ?? id,
    version: text(body.version) ?? "",
    tier: tierOf(body.tier),
    summary: text(body.summary),
    author: text(body.author),
    licence: text(body.licence),
    enabled: body.enabled === true,
    attention: text(body.attention) ?? text(body.broken) ?? null,
    health,
    update_available: text(body.update_available) ?? null,
    development: body.development === true,
    described: body.described === true && tierOf(body.tier) === "sandboxed",
    running: body.running === true,
    reviewed: body.reviewed === true,
    provenance: provenanceOf(body.provenance, typeof body.source === "string" ? body.source : text(bag(body.source)?.kind)),
    screens,
    dock,
    wall,
    permissions: lines.length > 0 ? lines : readLines(body.permissions),
    source: readSource(body.source, body),
    installed_ms: whole(body.installed_at) ?? whole(body.installed_ms),
    consent: readConsent(body.consent, body.consents),
    creates: readCreates(body.adds ?? body.creates),
    data_bytes: whole(body.storage_bytes) ?? whole(body.data_bytes),
    data_dir: text(body.data_dir) ?? null,
    settings_section: text(body.settings_section) ?? null,
    has_settings: body.has_settings === true || text(body.settings_section) !== undefined,
    tree: text(body.tree) ?? null,
    update: readUpdate(body.update),
    versions: readVersions(body.versions),
    local_addresses: readOrigins(body.local_addresses),
    environment_note: text(body.environment_note) ?? null,
    feeds: feedLines(body.feeds),
    net_notes: noteLines(body.net_notes),
  };
}

/** Sentences the server wrote for the app's page: at most 32, each plain text of at most 500 characters. */
export function noteLines(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => (typeof item === "string" && item.trim() !== "" ? [item.slice(0, 500)] : [])).slice(0, 32);
}

/** The list of installed apps: `{ apps: [...] }` or a bare list. One unreadable entry is skipped. */
export function readApps(raw: unknown): AppSummary[] {
  const list = Array.isArray(raw) ? raw : bag(raw) && Array.isArray(bag(raw)?.apps) ? (bag(raw)?.apps as unknown[]) : null;
  if (!list) throw new Error("The server sent a list of apps in a shape this screen cannot read.");
  const apps: AppSummary[] = [];
  for (const item of list) {
    try {
      apps.push(readApp(item));
    } catch {
      // An entry with no id cannot be shown or acted on.
    }
  }
  return apps;
}

/**
 * Where the planned app comes from. For git the server's own address and commit are used:
 * that commit is the one it pinned. A folder's path is never sent back, so it is the one
 * that was asked for.
 */
function readInstallSource(value: unknown, asked: InstallSource | undefined): InstallSource | null {
  const source = bag(value);
  if (!source) return null;
  const url = text(source.repo) ?? text(source.url);
  const commit = text(source.commit);
  if (source.kind === "git") return url && commit ? { kind: "git", url, commit } : null;
  if (source.kind !== "folder") return null;
  const example = text(source.example);
  if (example) return { kind: "example", id: example };
  const path = text(source.path) ?? (asked?.kind === "folder" ? asked.path : undefined);
  return { kind: "folder", path: path ?? "" };
}

/** Step one's answer. The plan's id and fingerprint are what step two sends back. */
export function readPlan(raw: unknown, asked?: InstallSource): InstallPlan {
  const outer = bag(raw);
  const body = bag(outer?.plan) ?? outer;
  if (!body) throw new Error("The server sent a plan this screen cannot read.");
  const fingerprint = text(body.fingerprint);
  if (!fingerprint) throw new Error("The server sent a plan with no fingerprint, so it cannot be installed from here.");
  // `plan` is the plan's id; an older shape wrapped the whole answer in it.
  const planId = text(body.plan) ?? text(body.plan_id) ?? text(outer?.plan_id);
  if (!planId) throw new Error("The server sent a plan with no id, so it cannot be installed from here.");
  const source = readInstallSource(body.source, asked);
  if (!source) throw new Error("The server sent a plan with no source, so it cannot be installed from here.");
  const info = bag(body.manifest) ?? bag(body.app) ?? body;
  const id = text(info.id);
  if (!id) throw new Error("The server sent a plan for an app with no id.");
  const installed = bag(body.installed);
  const tier = tierOf(info.tier);
  const reviewed = body.reviewed === true;
  return {
    plan: planId,
    fingerprint,
    source,
    needs_typed_name: typeof body.needs_typed_name === "boolean" ? body.needs_typed_name : tier === "full" && !reviewed,
    app: {
      id,
      name: text(info.name) ?? id,
      version: text(info.version) ?? "",
      tier,
      summary: text(info.summary),
      author: text(info.author),
      licence: text(info.licence),
      api: text(info.api),
    },
    reviewed,
    provenance: provenanceOf(body.provenance, source.kind),
    permissions: readLines(body.permissions),
    creates: readCreates(body.adds ?? body.creates),
    installed_version: text(body.installed_version) ?? (installed ? (text(installed.version) ?? null) : null),
    // No code at all: the server says so when JimmyHQ draws every one of its screens.
    described: body.described === true && tier === "sandboxed",
    storage: storageSentence(body.storage),
    feeds: feedLines(body.feeds),
  };
}

/** The plan's storage ask (`{asked_mb, usual_mb, sentence}`) as the one sentence the dialog shows, or null. */
export function storageSentence(raw: unknown): string | null {
  const entry = bag(raw);
  const sentence = entry ? text(entry.sentence) : undefined;
  return sentence ? sentence.slice(0, 300) : null;
}

// --- refusals ---------------------------------------------------------------------------

function problemFrom(item: unknown): Problem | null {
  if (Array.isArray(item) && item.length >= 2 && typeof item[1] === "string") {
    return { field: typeof item[0] === "string" ? item[0] : "", message: item[1] };
  }
  const entry = bag(item);
  if (!entry) return null;
  const message = text(entry.message) ?? text(entry.msg) ?? text(entry.error) ?? text(entry.sentence);
  if (!message) return null;
  let field = text(entry.field) ?? text(entry.path) ?? "";
  if (!field && Array.isArray(entry.loc)) {
    field = entry.loc.filter((part) => part !== "body" && part !== "query").join(".");
  }
  return { field, message };
}

function problemsOf(value: unknown): Problem[] {
  if (!Array.isArray(value)) return [];
  return value.map(problemFrom).filter((problem): problem is Problem => problem !== null);
}

/**
 * What a failed install step said: a sentence, the manifest parser's problems when it
 * listed them (as `{field, message}` objects or `[field, sentence]` pairs, under `errors`
 * or as the whole `detail`), a code if it gave one, and which kind of refusal it is.
 */
export function readRefusal(status: number, body: unknown): { message: string; problems: Problem[]; code: string | null; kind: RefusalKind } {
  const top = bag(body);
  const detail = top?.detail;
  const inner = bag(detail);
  const problems = [...problemsOf(inner?.errors), ...problemsOf(top?.errors), ...problemsOf(detail)];
  const code = text(inner?.code) ?? text(top?.code) ?? null;
  let message =
    (typeof detail === "string" && detail) || text(inner?.message) || text(inner?.detail) || text(top?.message) || "";
  if (!message && problems.length > 0) {
    message = problems.length === 1 ? "This app's manifest has a problem." : `This app's manifest has ${problems.length} problems.`;
  }
  if (!message) message = `Request failed (${status}).`;
  return { message, problems, code, kind: refusalKind(status, code, problems.length > 0, message) };
}

/**
 * Which refusal this is. A list of manifest problems is the author's to fix. A refusal that
 * names a setting (by code, or by saying "unreviewed") is the owner's to change.
 */
export function refusalKind(status: number, code: string | null, hasProblems: boolean, message: string): RefusalKind {
  if (hasProblems) return "manifest";
  if (code && /unreviewed|setting|forbid/i.test(code)) return "setting";
  if ((status === 403 || status === 409) && /unreviewed|not been reviewed/i.test(message)) return "setting";
  return "other";
}
