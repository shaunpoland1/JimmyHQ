// The apps that ship with JimmyHQ: the examples in `apps/examples` and the first-party apps in `apps/`.

import type { TierKey } from "./status.ts";

export type Example = { id: string; name: string; tier: TierKey; blurb: string };

export const EXAMPLES: readonly Example[] = [
  { id: "books_read", name: "Books read", tier: "described", blurb: "A reading log. Only a description file, with no code." },
  { id: "water", name: "Water", tier: "sandboxed", blurb: "Count glasses of water. A small page that can add a task." },
  { id: "watering", name: "Watering", tier: "full", blurb: "Asks on your Bench whether the plants were watered, on a schedule, and shows the answers. A small full app." },
  { id: "drafts", name: "Drafts", tier: "full", blurb: "Drafts a short post on a schedule with your AI model and asks you on your Bench." },
];

/**
 * The apps that ship in JimmyHQ's own `apps/` folder beside the examples, made by the JimmyHQ
 * project for real use, read from what the server sends for `GET /api/apps/_examples`. The
 * server lists the folders that are really in this copy, so no app is named here, and an app
 * left out of a copy is simply not offered. An entry that cannot be read is skipped.
 */
export function readFirstParty(raw: unknown): Example[] {
  const list = raw && typeof raw === "object" ? (raw as { examples?: unknown }).examples : null;
  if (!Array.isArray(list)) throw new Error("The server sent the apps that ship in a shape this screen cannot read.");
  const found: Example[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const entry = item as Record<string, unknown>;
    const { id, name, summary, tier } = entry;
    if (entry.group !== "first_party" || typeof id !== "string" || typeof name !== "string" || id === "" || name === "") continue;
    const kind: TierKey = entry.described === true ? "described" : tier === "sandboxed" ? "sandboxed" : "full";
    found.push({ id, name, tier: kind, blurb: typeof summary === "string" ? summary : "" });
  }
  return found;
}

export const FIRST_PARTY_LIST = {
  heading: "Made by the JimmyHQ project",
  text: "Apps for everyday use that come with JimmyHQ. Install shows what each one may do first.",
} as const;

/** The first-party apps not installed yet, in their listed order. */
export function notInstalled(apps: readonly Example[], installedIds: Iterable<string>): Example[] {
  const have = new Set(installedIds);
  return apps.filter((app) => !have.has(app.id));
}

/** The source that installs an example: its id, which the server looks up among the ones it ships. */
export function exampleSource(example: Pick<Example, "id">): { kind: "example"; id: string } {
  return { kind: "example", id: example.id };
}

/** The three kinds of app, one sentence each, for the empty state. */
export const TIER_SENTENCES: readonly { key: TierKey; title: string; text: string }[] = [
  { key: "described", title: "Records and views", text: "Only a description of records and views. It has no code, so it can only keep and show what you put in it." },
  { key: "sandboxed", title: "Sandboxed", text: "A web page shown in a locked box. It can only do what its permissions list, and you read that list before you install it." },
  { key: "full", title: "Full", text: "A program that runs as you and could do anything you can do on this computer. JimmyHQ starts it and shows how it is doing." },
];

/** The two panels of the empty Apps screen: one to read, one to act on. */
export const EMPTY_ABOUT = {
  eyebrow: "About apps",
  heading: "Three kinds of app",
  text: "Every app is one of these. The install window tells you which, and what it may do, before anything is installed.",
} as const;

export const EMPTY_TRY = {
  eyebrow: "Try one",
  heading: "Examples that come with JimmyHQ",
  text: "Made by the JimmyHQ project. Choose one to see what it is and what it may do. Nothing is installed until you say so, and you can remove it afterwards.",
} as const;
