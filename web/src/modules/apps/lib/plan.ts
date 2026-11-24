// The install dialog's first step as a view model: every sentence the dialog shows, built
// from the plan the server returned. The dialog only draws this.

import type { CreatedItem, Creates, InstallPlan, PermissionLine } from "../api.ts";
import { nameMatches, shortCommit } from "./install.ts";
import { groupLines } from "./permissions.ts";
import type { LineGroup } from "./permissions.ts";
import { madeByLine, tierWords } from "./status.ts";
import type { TierWords } from "./status.ts";

export type WarningWeight = "calm" | "warn" | "red" | "blunt";

export type PlanWarning = { weight: WarningWeight; heading: string; text: string };

export type PlanView = {
  title: string;
  name: string;
  version: string;
  summary: string | null;
  /** Who made it: the JimmyHQ project for an app that ships with JimmyHQ, otherwise what the app says, marked as unchecked. */
  madeBy: string;
  licence: string | null;
  /** Where it comes from: JimmyHQ itself, a folder on this computer, or the git address and commit. */
  sourceLine: string;
  commit: string | null;
  tier: TierWords;
  /** The server checked that these are the files that ship with JimmyHQ. */
  bundled: boolean;
  warning: PlanWarning;
  /** For a full app: that installing starts its program, and where to see it. */
  notRunning: string | null;
  groups: LineGroup[];
  /** What installing creates, one sentence each. */
  creates: string[];
  /** For an app that asks to keep more than the usual storage: the sentence that says how much. */
  storage: string | null;
  /** The installed apps that will read the feeds it offers. */
  feeds: string[];
  /** "Already installed as version 1.0.0": installing replaces it. */
  replaces: string | null;
  /** The name the owner must type before the button works, or null. */
  nameToType: string | null;
  /** Why the name must be typed, under the field. */
  typedHint: string | null;
  installLabel: string;
  /** The install button is red only for a full app nobody has reviewed. */
  installTone: "primary" | "danger";
  /** Why the plan cannot be installed from here, if it cannot. */
  blocked: string | null;
};

type WarningFacts = Pick<InstallPlan, "reviewed" | "app" | "described"> & { provenance?: InstallPlan["provenance"] };

/** Whether the server vouched that these are the files that ship inside JimmyHQ. */
export function isBundled(plan: { provenance?: InstallPlan["provenance"] }): boolean {
  return plan.provenance === "bundled";
}

/** A full app that nobody has reviewed and that does not come with JimmyHQ: the blunt case. */
export function isBlunt(plan: Pick<InstallPlan, "reviewed" | "app"> & { provenance?: InstallPlan["provenance"] }): boolean {
  return plan.app.tier === "full" && !plan.reviewed && !isBundled(plan);
}

/** Whether the dialog asks for the app's name to be typed before installing: when the server will, and for any unreviewed full app. */
export function needsTypedName(plan: Pick<InstallPlan, "reviewed" | "app"> & { needs_typed_name?: boolean; provenance?: InstallPlan["provenance"] }): boolean {
  return plan.needs_typed_name === true || isBlunt(plan);
}

/** Said in the dialog for a full app: what installing starts. */
export const RUNS_ON_INSTALL =
  "Once it is installed, JimmyHQ starts its program and keeps it running while the app is switched on. You can see how it is, and restart it, on the Apps screen.";

const NO_CODE = "It has no code, so it can only keep and show what you put in it.";

export function planWarning(plan: WarningFacts): PlanWarning {
  const full = plan.app.tier === "full";
  const noCode = plan.described && !full;
  if (isBundled(plan)) {
    if (full) {
      return {
        weight: "red",
        heading: "Comes with JimmyHQ, and runs as you",
        text: "The JimmyHQ project made it as an example of a full app. Like every full app, its program can read and change your files and use the network, and JimmyHQ cannot limit it.",
      };
    }
    return {
      weight: "calm",
      heading: "Comes with JimmyHQ",
      text: "The JimmyHQ project made it as an example for you to try. You can remove it at any time, and its data with it if you choose.",
    };
  }
  if (full && !plan.reviewed) {
    return {
      weight: "blunt",
      heading: "Nobody has reviewed this app, and it runs as you",
      text:
        "Its program can read and change your files, use the network and do anything you can do on this computer. " +
        "JimmyHQ cannot limit it. Install it only if you trust whoever wrote it.",
    };
  }
  if (full) {
    return {
      weight: "red",
      heading: "This app runs as you",
      text: "It is listed as reviewed, but a review is not a guarantee. Its program can read and change your files and use the network, and JimmyHQ cannot limit it.",
    };
  }
  if (!plan.reviewed) {
    return {
      weight: "warn",
      heading: "Not reviewed",
      text: noCode
        ? `Nobody has reviewed this app, but ${NO_CODE.charAt(0).toLowerCase()}${NO_CODE.slice(1)}`
        : "Nobody has reviewed this app. It can still only do what is listed below, so read the list before you install it.",
    };
  }
  return {
    weight: "calm",
    heading: "Reviewed",
    text: "It is listed as reviewed in the store. A review is not a guarantee, so still read what it may do.",
  };
}

function label(item: CreatedItem): string {
  if (typeof item === "string") return item;
  return item.label ?? item.name ?? item.kind ?? item.id ?? "";
}

function names(items: readonly CreatedItem[] | undefined): string {
  return (items ?? []).map(label).filter((name) => name !== "").map((name) => `“${name}”`).join(", ");
}

function many(items: readonly CreatedItem[] | undefined, one: string, more: string): string | null {
  if (!items || items.length === 0) return null;
  return `${items.length === 1 ? one : more}: ${names(items)}`;
}

/** What installing creates, as sentences. The app's own folder is always first. */
export function createdLines(creates: Creates | null | undefined): string[] {
  const lines = ["A private folder for its own data, on this computer."];
  if (!creates) return lines;
  const add = (line: string | null) => {
    if (line) lines.push(line);
  };
  add(many(creates.screens, "A screen in the sidebar", "Screens in the sidebar"));
  add(many(creates.dock, "A tab in the side panel", "Tabs in the side panel"));
  add(many(creates.wall, "A tile on the Wall", "Tiles on the Wall"));
  add(many(creates.tables, "A table of records", "Tables of records"));
  if (typeof creates.settings === "number") {
    if (creates.settings > 0) lines.push(`${creates.settings === 1 ? "One setting" : `${creates.settings} settings`}, in their own section of Settings`);
  } else {
    add(many(creates.settings, "A setting, in its own section of Settings", "Settings, in their own section of Settings"));
  }
  add(many(creates.secrets, "A secret you type in yourself, kept in the secret store", "Secrets you type in yourself, kept in the secret store"));
  add(many(creates.cards, "A kind of card on your Bench", "Kinds of card on your Bench"));
  add(many(creates.memory_sources, "A source offered to Memory, switched off until you switch it on", "Sources offered to Memory, each switched off until you switch it on"));
  add(many(creates.tools, "A tool offered to your agents, off for every agent until you choose", "Tools offered to your agents, off for every agent until you choose"));
  if (creates.jobs === true || (typeof creates.jobs === "number" && creates.jobs > 0)) {
    lines.push("Work that runs in the background on a schedule");
  } else if (Array.isArray(creates.jobs) && creates.jobs.length > 0) {
    lines.push(`Work that runs in the background on a schedule: ${names(creates.jobs)}`);
  }
  return lines;
}

function sourceLine(plan: InstallPlan): string {
  const source = plan.source;
  if (isBundled(plan)) return "JimmyHQ itself. A copy goes into your apps.";
  if (source.kind === "git") return `${source.url} at commit ${shortCommit(source.commit)}`;
  return "A folder on this computer. A copy goes into JimmyHQ, so later changes to the folder change nothing.";
}

function typedHint(plan: InstallPlan): string {
  return isBlunt(plan)
    ? "Nobody has reviewed this app. Typing its name shows JimmyHQ you mean to install it anyway."
    : "Every full app runs as you, so JimmyHQ asks you to type its name to be sure you mean it.";
}

/** The first step's dialog, sentence by sentence. */
export function planView(plan: InstallPlan): PlanView {
  const lines: PermissionLine[] = plan.permissions;
  const tier = tierWords({ tier: plan.app.tier, described: plan.described, provenance: plan.provenance });
  const typed = needsTypedName(plan);
  const bundled = isBundled(plan);
  return {
    title: plan.installed_version ? `Replace ${plan.app.name}?` : `Install ${plan.app.name}?`,
    name: plan.app.name,
    version: plan.app.version,
    summary: plan.app.summary ?? null,
    madeBy: madeByLine(plan.app.author, bundled),
    licence: plan.app.licence ?? null,
    sourceLine: sourceLine(plan),
    commit: plan.source.kind === "git" ? plan.source.commit : null,
    tier,
    bundled,
    warning: planWarning(plan),
    notRunning: plan.app.tier === "full" ? RUNS_ON_INSTALL : null,
    groups: groupLines(lines),
    creates: createdLines(plan.creates),
    storage: plan.storage ?? null,
    feeds: plan.feeds ?? [],
    replaces: plan.installed_version ? `Version ${plan.installed_version} is installed. Installing replaces it, and its data stays.` : null,
    nameToType: typed ? plan.app.name : null,
    typedHint: typed ? typedHint(plan) : null,
    installLabel: plan.installed_version ? "Replace it" : "Install",
    installTone: isBlunt(plan) ? "danger" : "primary",
    blocked: lines.length === 0 ? "JimmyHQ did not get the list of what this app may do, so it will not install it. Nothing was installed. Close this window and try again." : null,
  };
}

/** Whether the Install button works: nothing blocks it and, if asked, the name was typed. */
export function canInstall(view: Pick<PlanView, "blocked" | "nameToType">, typed: string): boolean {
  if (view.blocked) return false;
  return view.nameToType === null || nameMatches(typed, view.nameToType);
}
