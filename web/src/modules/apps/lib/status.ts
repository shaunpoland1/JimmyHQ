// What an installed app is, in plain words: its tier, whether it is on, and where its
// screens appear. Pure; the cards and the detail view only draw what this says.

import type { AppDetail, AppSummary, Placement } from "../api.ts";

export type TierKey = "described" | "sandboxed" | "full";

export type TierWords = {
  key: TierKey;
  /** The badge: two or three words. */
  label: string;
  /** One sentence for the owner. */
  sentence: string;
};

export const TIER_WORDS: Record<TierKey, TierWords> = {
  described: { key: "described", label: "Records and views", sentence: "Only records and views. It has no code of its own." },
  sandboxed: {
    key: "sandboxed",
    label: "Sandboxed",
    sentence: "A page in a box. It can only do what is listed below.",
  },
  full: {
    key: "full",
    label: "Full",
    sentence: "A program that runs as you. It can do anything you can do on this computer.",
  },
};

/** What a full app that ships inside JimmyHQ is told: it is ours, and it still runs as a program here. */
export const SHIPS_WITH_JIMMYHQ_SENTENCE = "This app ships with JimmyHQ. It runs as a program on this computer, so it could do anything you can.";

type TierFacts = Pick<AppSummary, "tier" | "described" | "screens"> & { provenance?: AppSummary["provenance"] };

/**
 * Which of the three kinds this app is. A sandboxed app is "described" when the server says
 * so, or when every screen it has is the core's own renderer (`entry: "described"`).
 */
export function tierKey(app: TierFacts): TierKey {
  if (app.tier === "full") return "full";
  if (app.described === true) return "described";
  const screens = app.screens ?? [];
  if (screens.length > 0 && screens.every((screen) => screen.entry === "described")) return "described";
  return "sandboxed";
}

export function tierWords(app: TierFacts): TierWords {
  const words = TIER_WORDS[tierKey(app)];
  // Still true, and said as what it is: the JimmyHQ project's own program, not a stranger's.
  return words.key === "full" && app.provenance === "bundled" ? { ...words, sentence: SHIPS_WITH_JIMMYHQ_SENTENCE } : words;
}

export type StateKind = "on" | "off" | "attention";
export type Tone = "ok" | "warn" | "bad" | "none";

export type AppState = {
  kind: StateKind;
  /** "On", "Off" or "Needs attention". */
  label: string;
  tone: Tone;
  /** For "attention": why, in a sentence the owner can act on. */
  reason: string | null;
};

type StateFacts = Pick<AppSummary, "enabled" | "attention" | "health">;

const CRASHING = "It kept stopping soon after it started, so JimmyHQ is holding it back. Open it here and press Restart to start it again.";
const SILENT = "Its program has not asked for work for a while. Open it here and press Restart.";

/** On, off, or needs attention (with the reason). A switched-off app is off whatever else is wrong. */
export function appState(app: StateFacts): AppState {
  if (!app.enabled) return { kind: "off", label: "Off", tone: "none", reason: null };
  if (app.attention) return { kind: "attention", label: "Needs attention", tone: "bad", reason: app.attention };
  if (app.health === "crashing") return { kind: "attention", label: "Needs attention", tone: "bad", reason: CRASHING };
  if (app.health === "not_responding") return { kind: "attention", label: "Needs attention", tone: "warn", reason: SILENT };
  return { kind: "on", label: "On", tone: "ok", reason: null };
}

export const BUNDLED_BADGE = "Comes with JimmyHQ";
export const NOT_REVIEWED_BADGE = "Not reviewed";

/** How a badge is drawn: an app that comes with JimmyHQ stands out, one nobody reviewed is a warning, the rest are plain. */
export function badgeTone(text: string): "accent" | "warn" | "plain" {
  if (text === BUNDLED_BADGE) return "accent";
  return text === NOT_REVIEWED_BADGE ? "warn" : "plain";
}

/** The small badges beside the name, besides the tier and the state. An app that comes with JimmyHQ says so instead of "Not reviewed". */
export function badges(app: Pick<AppSummary, "update_available" | "development" | "reviewed" | "provenance">): string[] {
  const list: string[] = [];
  if (app.development) list.push("Development");
  if (app.update_available) list.push(`Update available: ${app.update_available}`);
  if (app.provenance === "bundled") list.push(BUNDLED_BADGE);
  else if (app.reviewed !== true) list.push(NOT_REVIEWED_BADGE);
  return list;
}

/** Who made an app: the JimmyHQ project for one that comes with JimmyHQ, otherwise what the app says, marked as unchecked. */
export function madeByLine(author: string | undefined, bundled: boolean): string {
  if (bundled) return "The JimmyHQ project";
  return author ? `${author}, as the app says. JimmyHQ cannot check this.` : "The app does not say who made it.";
}

/** The "About this app" lines of the detail screen: who made it, whether anyone reviewed it, and where it came from. */
export function aboutLines(app: Pick<AppDetail, "author" | "reviewed" | "provenance" | "source">): { madeBy: string; review: string; source: string } {
  const bundled = app.provenance === "bundled";
  const madeBy = madeByLine(app.author, bundled);
  const review = bundled ? "It comes with JimmyHQ" : app.reviewed ? "Reviewed and listed in the store" : "Nobody has reviewed it";
  return { madeBy, review, source: sourceText(app) };
}

/** Where an installed app came from, in a short phrase. */
export function sourceText(app: Pick<AppDetail, "provenance" | "source">): string {
  const source = app.source;
  if (app.provenance === "bundled") return "JimmyHQ itself, copied into your apps";
  if (!source) return "Not recorded";
  if (source.kind === "git") return `${source.repo ?? "A git address"}${source.commit ? ` at commit ${source.commit}` : ""}`;
  if (source.kind === "folder") return source.path ? `A folder on this computer, ${source.path}, copied into JimmyHQ` : "A folder on this computer, copied into JimmyHQ";
  if (source.kind === "development") return "A folder linked for development";
  return source.kind === "store" ? "The store" : source.kind;
}

export function switchLabel(app: Pick<AppSummary, "name" | "enabled">): string {
  return `${app.name} is ${app.enabled ? "on" : "off"}. Press to switch ${app.enabled ? "off" : "on"}.`;
}

export function switchedToast(app: Pick<AppSummary, "name">, enabled: boolean): string {
  return enabled ? `Switched on ${app.name}.` : `Switched off ${app.name}. Its data is kept.`;
}

/** Where an app puts its screens, one phrase per place. Empty when it adds none. */
export function whereLines(app: Pick<AppSummary, "screens" | "dock" | "wall">): string[] {
  const quote = (items: readonly Placement[]) => items.map((item) => `“${item.label}”`).join(", ");
  const lines: string[] = [];
  const screens = app.screens ?? [];
  const dock = app.dock ?? [];
  const wall = app.wall ?? [];
  if (screens.length > 0) lines.push(`${screens.length === 1 ? "A screen" : "Screens"} in the sidebar: ${quote(screens)}`);
  if (dock.length > 0) lines.push(`${dock.length === 1 ? "A tab" : "Tabs"} in the side panel: ${quote(dock)}`);
  if (wall.length > 0) lines.push(`${wall.length === 1 ? "A tile" : "Tiles"} on the Wall: ${quote(wall)}`);
  return lines;
}

/** The sentence a remove dialog opens with. */
export function removeIntro(app: Pick<AppSummary, "name" | "tier">): string {
  return app.tier === "full"
    ? `${app.name} stops, and its code and its program are deleted.`
    : `${app.name}'s screens go away and its code is deleted.`;
}

/** What is kept when the owner removes an app and leaves "keep its data" ticked. */
export function keptLine(app: { id: string; tier?: string; data_dir?: string | null }): string {
  const where = app.data_dir ? `in ${app.data_dir}` : `in JimmyHQ's data folder, under apps/${app.id}/data`;
  return `Its data is kept ${where}. Install the app again and it picks up where it left off.${app.tier === "full" ? ` ${DOOR_KEPT}` : ""}`;
}

/** What happens to a full app's keys for the owner's devices when it is removed with its data kept. */
export const DOOR_KEPT = "If it takes messages from your devices, its keys are kept; its door will be off until you switch it on again.";

/** And when the owner deletes a full app's data. */
export const DOOR_ERASED = "Any keys it gave your devices are deleted, so they stop working.";

export const ERASE_NOTE = "Deleting its data cannot be undone.";

/** The further warning when the owner chooses to delete an app's data. */
export const DELETE_WARNING =
  "This deletes its data for good: what it stored, the copies kept from earlier versions, and what its program printed. No copy is kept, and it cannot be undone.";
export const RECORD_ERASED =
  "What it wrote on your record is erased too: those entries keep their place and lose what they said. That you installed and removed it stays on the record.";
export const RECORD_KEPT = "What it wrote on your record stays readable.";
