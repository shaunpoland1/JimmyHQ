// What an agent remembers, in plain words: where each line came from, how strong it is,
// what is waiting for the next night, what each night did, and what a rollback restores.

import type { Candidate, Impression, LastNight, MemoryLine, Snapshot, VillageEvent } from "../api.ts";
import { MEMORY_SECTIONS } from "../api.ts";
import { labelForId } from "./names.ts";
import { toMs } from "./status.ts";

export const SECTION_LABELS: Record<string, string> = {
  self: "Who it is",
  skills: "Skills",
  tools: "Tools",
  wants: "Wants",
  moments: "Moments",
  facts: "Facts",
  story: "Its story",
};

export const SECTION_HELP: Record<string, string> = {
  self: "How it sees itself: its character and habits.",
  skills: "What it has learned to do well.",
  tools: "What it has learned about the tools it uses, and their quirks.",
  wants: "What it is after and cares about.",
  moments: "Particular things that happened and are worth keeping.",
  facts: "What it has learned about the world and the work. Kept apart from its story, so nothing it imagined about itself becomes research.",
  story: "The story of its own life, including its name.",
};

export function sectionLabel(section: string): string {
  return SECTION_LABELS[section] ?? section.charAt(0).toUpperCase() + section.slice(1);
}

/** The seven sections in the server's order, then any the server added that this screen does not know. */
export function sectionOrder(have: Record<string, unknown> | undefined): string[] {
  const extra = Object.keys(have ?? {}).filter((key) => !(MEMORY_SECTIONS as readonly string[]).includes(key));
  return [...MEMORY_SECTIONS, ...extra];
}

export type Provenance = { label: string; help: string; trust: "checked" | "unchecked" | "unknown" };

const PROVENANCE: Record<string, Provenance> = {
  owner: { label: "From you", help: "It cites something you said to it in chat.", trust: "checked" },
  seen: { label: "Seen in its own actions", help: "It cites what it did, what it was refused, or what you decided on the Bench.", trust: "checked" },
  village: { label: "From the village's news", help: "It rests on the village's own notes about what changed.", trust: "checked" },
  "web:unconfirmed": { label: "From the web, unconfirmed", help: "It came from a page on the web and nobody has confirmed it. Treat it as a lead, not a fact.", trust: "unchecked" },
  "self-reported": { label: "Its own account, not checked", help: "The agent's own word, backed by nothing in the record. It is told so whenever it reads the line.", trust: "unchecked" },
};

/** Where a line came from, in words. A source this screen does not know is said plainly rather than hidden. */
export function provenanceOf(source: string | undefined | null): Provenance {
  if (!source) return { label: "Source not recorded", help: "This line has no source on file.", trust: "unknown" };
  return PROVENANCE[source] ?? { label: `Source: ${source}`, help: "A kind of source this screen has no wording for yet.", trust: "unknown" };
}

export type Strength = { percent: number; word: string };

export function strengthOf(value: number | undefined): Strength | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const clamped = Math.max(0, Math.min(1, value));
  const word = clamped >= 0.8 ? "Very strong" : clamped >= 0.55 ? "Strong" : clamped >= 0.3 ? "Moderate" : clamped >= 0.1 ? "Fading" : "Faint";
  return { percent: Math.round(clamped * 100), word };
}

const WEIGHT_WORDS: Record<string, string> = {
  core: "Core: central to who it is, fades slowly",
  useful: "Useful",
  routine: "Routine: easy to push out when room is needed",
};

export function weightWord(weight: string | undefined): string | null {
  if (!weight) return null;
  return WEIGHT_WORDS[weight] ?? weight;
}

export function evidenceText(evidence: readonly string[] | undefined): string {
  const count = evidence?.length ?? 0;
  if (count === 0) return "No evidence cited";
  return `${count} ${count === 1 ? "piece" : "pieces"} of evidence`;
}

const WEIGHT_RANK: Record<string, number> = { core: 0, useful: 1, routine: 2 };

/** Core lines first, then the strongest, then oldest id first so the order is steady between reads. */
export function sortLines(lines: readonly MemoryLine[] | undefined): MemoryLine[] {
  const rank = (line: MemoryLine) => WEIGHT_RANK[line.weight ?? ""] ?? 1;
  return [...(lines ?? [])].sort((a, b) => rank(a) - rank(b) || (b.strength ?? 0) - (a.strength ?? 0) || a.id - b.id);
}

export function lineCount(count: number): string {
  return count === 0 ? "Nothing yet" : `${count} ${count === 1 ? "line" : "lines"}`;
}

/** The agent's candidate lines, newest first. */
export function sortCandidates(candidates: readonly Candidate[] | undefined): Candidate[] {
  const when = (candidate: Candidate) => toMs(candidate.ts) ?? 0;
  return [...(candidates ?? [])].sort((a, b) => when(b) - when(a));
}

/** Who an impression is about: the owner as "You", a coworker by name when it is known, else by id. */
export function aboutLabel(about: string, everyone: readonly { id: string; name: string | null; role: string }[]): string {
  if (about === "owner") return "You";
  return labelForId(about, everyone);
}

/** Impressions of the owner first, then coworkers in a steady order. */
export function sortImpressions(impressions: readonly Impression[] | undefined): Impression[] {
  const rank = (item: Impression) => (item.about === "owner" ? 0 : 1);
  return [...(impressions ?? [])].filter((item) => item.text.trim()).sort((a, b) => rank(a) - rank(b) || a.about.localeCompare(b.about));
}

// ---- nights and snapshots ----

/** The snapshots, newest first. */
export function newestSnapshots(list: readonly Snapshot[] | undefined): Snapshot[] {
  return [...(list ?? [])].sort((a, b) => b.seq - a.seq);
}

export type NightWork = {
  /** One sentence on what the sorting did. */
  summary: string;
  by: string | null;
  notes: string | null;
  dreamt: boolean;
  /** The night did not finish; the reason, if it said. */
  failed: string | null;
};

const VERBS = ["added", "reinforced", "replaced", "merged", "dropped", "locked", "rejected"] as const;

/** "added 3, reinforced 2, rejected 1", counted from the sentences the night wrote about each change. */
export function tallyResults(results: readonly unknown[] | undefined): string {
  const counts = new Map<string, number>();
  for (const line of results ?? []) {
    const verb = typeof line === "string" ? /^[a-z]+/.exec(line.trim().toLowerCase())?.[0] : undefined;
    if (verb && (VERBS as readonly string[]).includes(verb)) counts.set(verb, (counts.get(verb) ?? 0) + 1);
  }
  return VERBS.filter((verb) => counts.has(verb))
    .map((verb) => `${verb} ${counts.get(verb)}`)
    .join(", ");
}

export function byWords(by: string | null | undefined): string | null {
  if (!by) return null;
  if (by === "smart") return "Sorted by the night model";
  if (by === "fallback") return "Sorted by the everyday model, standing in for the night model";
  return `Sorted by ${by}`;
}

/** What one night did, from its log event (`consolidation`) or, failing that, the card's last-night summary. */
export function describeNightWork(event: VillageEvent | undefined, fallback: Pick<LastNight, "ops" | "rejected" | "by" | "notes"> | undefined): NightWork | null {
  if (event) {
    const data = event.data;
    const failed = data.failed ? String(data.why ?? "it did not finish") : null;
    const ops = typeof data.ops === "number" ? data.ops : 0;
    const tally = tallyResults(Array.isArray(data.results) ? data.results : undefined);
    const faded = Array.isArray(data.decayed_out) ? data.decayed_out.length : 0;
    const parts = [failed ? `It did not finish: ${failed}.` : ops === 0 ? "Nothing needed changing." : `${ops} ${ops === 1 ? "change" : "changes"} to memory${tally ? `: ${tally}` : ""}.`];
    if (faded > 0) parts.push(`${faded} ${faded === 1 ? "line" : "lines"} faded out.`);
    return {
      summary: parts.join(" "),
      by: byWords(typeof data.by === "string" ? data.by : null),
      notes: typeof data.notes === "string" && data.notes.trim() ? data.notes.trim() : null,
      dreamt: Boolean(data.dream),
      failed,
    };
  }
  if (!fallback) return null;
  const rejected = fallback.rejected ? ` ${fallback.rejected} ${fallback.rejected === 1 ? "was" : "were"} turned down.` : "";
  return {
    summary: `${fallback.ops} ${fallback.ops === 1 ? "change" : "changes"} to memory.${rejected}`,
    by: byWords(fallback.by),
    notes: fallback.notes?.trim() || null,
    dreamt: false,
    failed: null,
  };
}

/** The log events for finished sorts, by the snapshot each one took. */
export function nightsBySnapshot(events: readonly VillageEvent[] | undefined): Map<number, VillageEvent> {
  const found = new Map<number, VillageEvent>();
  for (const event of events ?? []) {
    const snapshot = event.data.snapshot;
    if (event.kind === "consolidation" && typeof snapshot === "number" && !event.data.failed) found.set(snapshot, event);
  }
  return found;
}

export function snapshotTitle(snapshot: Snapshot, plainDay: (day: string) => string): string {
  const reason = snapshot.reason ?? "";
  if (reason === "night") return snapshot.day ? `Night after ${plainDay(snapshot.day)}` : "A night's sort";
  if (reason === "nap") return snapshot.day ? `Nap on ${plainDay(snapshot.day)}` : "A nap's sort";
  if (reason === "before rollback") return typeof snapshot.to === "number" ? `Saved just before rolling back to snapshot ${snapshot.to}` : "Saved just before a rollback";
  return `Snapshot ${snapshot.seq}`;
}

const PATH_WORDS: Record<string, string> = { memory: "its memory", impressions: "its impressions", diary: "its diary" };

/** "its memory, its impressions and its diary", for what a snapshot holds or a rollback restored. */
export function pathWords(paths: readonly string[] | undefined): string {
  const words = (paths ?? []).map((path) => PATH_WORDS[path] ?? path);
  if (words.length === 0) return "nothing";
  if (words.length === 1) return words[0] as string;
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** What a rollback does, as the lines the confirm dialog shows. The diary is only restored when asked for. */
export function rollbackEffects(seq: number, withDiary: boolean, name: string): { restored: string[]; untouched: string[]; undo: string } {
  return {
    restored: [
      `Its memory goes back to exactly what it was in snapshot ${seq}: all seven sections, with each line's strength and evidence. Anything it remembered after that is removed.`,
      `Its impressions of you and the others go back to how they were then.`,
      withDiary
        ? `Its diary goes back too. Entries written after that snapshot, dreams included, are removed from the diary.`
        : `Its diary is not touched: everything it has written since stays.`,
    ],
    untouched: [`Its files, its chat with you, its tasks, how it feels, and everything already recorded about ${name} on the Tape stay as they are.`],
    undo: "Before anything changes, its memory, impressions and diary as they are now are saved as a new snapshot, so a rollback can itself be rolled back.",
  };
}
