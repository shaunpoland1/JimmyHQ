// The permission lines of an app, grouped by what they let it do. The server writes the
// sentences and orders them most sensitive first; this only sets them in groups, so a
// long list reads as a few ideas and the dangerous ones are never buried.

import type { PermissionLine } from "../api.ts";

export type GroupKey = "warning" | "outside" | "reads" | "acts" | "own" | "other";

export type LineGroup = { key: GroupKey; heading: string; lines: PermissionLine[] };

const HEADINGS: Record<GroupKey, string> = {
  warning: "Read this first",
  outside: "Can send things away",
  reads: "Can read",
  acts: "Can act for you",
  own: "Its own data",
  other: "Also",
};

// Which table row belongs in which group. Anything not named falls into "other", so a
// permission added to the server later is shown, not dropped.
const GROUP_OF: Record<string, GroupKey> = {
  "always.full": "warning",
  net: "outside",
  "model.ask": "outside",
  "tape.read": "reads",
  feed: "reads",
  "memory.recall": "reads",
  "speech.dictate": "reads",
  "tasks.read": "reads",
  "agents.tools": "acts",
  "memory.source": "acts",
  inbound: "acts",
  jobs: "acts",
  "bench.file": "acts",
  "tasks.write": "acts",
  "always.data": "own",
};

const ORDER: GroupKey[] = ["warning", "outside", "reads", "acts", "own", "other"];

/** The table row of a line, from its `permission` or, failing that, its id. */
export function permissionOf(line: Pick<PermissionLine, "id" | "permission">): string {
  return line.permission ?? line.id.split(":")[0] ?? line.id;
}

export function groupOf(line: PermissionLine): GroupKey {
  if (line.strong) return "warning";
  return GROUP_OF[permissionOf(line)] ?? "other";
}

/** Lines in groups, the most sensitive group first. Within a group the server's order stays. */
export function groupLines(lines: readonly PermissionLine[]): LineGroup[] {
  const groups = new Map<GroupKey, PermissionLine[]>();
  for (const line of lines) {
    const key = groupOf(line);
    groups.set(key, [...(groups.get(key) ?? []), line]);
  }
  return ORDER.filter((key) => groups.has(key)).map((key) => ({ key, heading: HEADINGS[key], lines: groups.get(key) ?? [] }));
}

/** How many things beyond its own data the app asks for: the number a card shows. */
export function askedCount(lines: readonly PermissionLine[]): number {
  return lines.filter((line) => line.always !== true).length;
}

/** The line above a card's permissions: how many it asks for, and that a full app runs as you. */
export function askedSummary(lines: readonly PermissionLine[]): string {
  const asked = askedCount(lines);
  const runsAsYou = lines.some((line) => line.strong === true);
  const noun = asked === 1 ? "permission" : "permissions";
  if (runsAsYou) return asked === 0 ? "Runs as you, and asks for nothing else" : `Runs as you, and asks for ${asked} more ${noun}`;
  if (asked === 0) return "Asks for nothing beyond its own data";
  return `Asks for ${asked} ${noun}`;
}

/** The hosts an app may send data to, for a short line under a card. */
export function hostsOf(lines: readonly PermissionLine[]): string[] {
  return lines.filter((line) => permissionOf(line) === "net" && line.host).map((line) => line.host as string);
}
