// The timeline: each entry in plain words, and the entries grouped by day.

import type { ActivityEntry } from "../api.ts";
import { describeActor } from "./facts.ts";
import { tallySentence, tallyFromFlat } from "./mining.ts";

function text(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function whole(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function capital(words: string): string {
  return words ? `${words.charAt(0).toUpperCase()}${words.slice(1)}` : words;
}

/** Who did it, to start a sentence: "You", "Mining", "An edit on disk", "Agent scout". */
export function actorSubject(actor: string): string {
  return capital(describeActor(actor));
}

const CHANGED_WORDS: Record<string, string> = { text: "its text", note: "the note it is in", kind: "its kind", confirmed: "whether it is confirmed" };

function changedWords(detail: Record<string, unknown>): string {
  const changed = Array.isArray(detail.changed) ? detail.changed.filter((item): item is string => typeof item === "string") : [];
  const words = changed.map((item) => CHANGED_WORDS[item] ?? item);
  if (words.length === 0) return "a fact";
  return `a fact: ${words.join(", ")}`;
}

const VIA_WORDS: Record<string, string> = { bench: "on the Bench", memory: "in Memory", miner: "by mining", library: "in the Library" };

function via(detail: Record<string, unknown>): string {
  const how = text(detail.via);
  return how ? ` ${VIA_WORDS[how] ?? `via ${how}`}` : "";
}

function versions(detail: Record<string, unknown>): string {
  const n = whole(detail.versions);
  return `${n} stored ${n === 1 ? "version" : "versions"}`;
}

export type ActivityLine = {
  /** What happened, as one sentence. */
  line: string;
  /** A second, quieter line: a reason, or a pass's counts. */
  more: string | null;
};

/**
 * One timeline entry in plain words. `labels` maps a source id to its name. A
 * type this screen does not know is shown by its raw name, never hidden.
 */
export function describeActivity(entry: Pick<ActivityEntry, "type" | "actor" | "detail">, labels: Record<string, string> = {}): ActivityLine {
  const who = actorSubject(entry.actor);
  const detail = entry.detail ?? {};
  switch (entry.type) {
    case "fact_saved": {
      const verb = detail.revived === true ? "brought back a fact" : "saved a fact";
      if (entry.actor === "system:memory") return { line: `${who} ${verb} automatically`, more: detail.confirmed === false ? "Not confirmed yet." : null };
      return { line: `${who} ${verb}`, more: detail.confirmed === false ? "Not confirmed yet." : null };
    }
    case "fact_edited":
      return { line: `${who} changed ${changedWords(detail)}`, more: null };
    case "fact_confirmed":
      return { line: `${who} confirmed a fact`, more: null };
    case "fact_forgotten": {
      const reason = text(detail.reason);
      return { line: `${who} forgot a fact`, more: reason ? `Reason: ${reason}` : null };
    }
    case "note_saved": {
      const from = text(detail.moved_from);
      if (from) return { line: `${who} moved a note`, more: `It was ${from}.` };
      return { line: `${who} ${detail.created === true ? "created a note" : "changed a note"}`, more: null };
    }
    case "note_removed":
      return { line: `${who} removed a note`, more: null };
    case "mined": {
      const source = text(detail.source);
      const name = source ? (labels[source] ?? source) : "a source";
      const asked = detail.trigger === "owner" ? ", when you asked" : detail.trigger === "schedule" ? ", on schedule" : "";
      return { line: `Mining read ${name}${asked}`, more: tallySentence(tallyFromFlat(detail)) };
    }
    case "candidate_kept":
      return { line: `${who} kept a proposed fact${via(detail)}`, more: null };
    case "candidate_declined":
      return { line: `${who} declined a proposed fact${via(detail)}`, more: null };
    case "history_purged": {
      const why = text(detail.why);
      if (why === "note_deleted") return { line: `${who} deleted a note with its history`, more: `${capital(versions(detail))} erased for good.` };
      if (why === "forgotten_everywhere") return { line: `${who} forgot a fact everywhere`, more: `${capital(versions(detail))} erased for good.` };
      if (why === "retention") return { line: "Old versions were removed, as your retention settings ask", more: `${capital(versions(detail))} erased for good.` };
      return { line: `${who} erased stored history${why ? ` (${why})` : ""}`, more: `${capital(versions(detail))} erased for good.` };
    }
    default:
      return { line: `${entry.type} (by ${describeActor(entry.actor)})`, more: null };
  }
}

/**
 * The note an entry can link to, or null. A note that was removed, or deleted
 * with its history, is not linked: there is nothing there to open.
 */
export function activityNote(entry: Pick<ActivityEntry, "type" | "note" | "detail">): string | null {
  if (!entry.note || !entry.note.toLowerCase().endsWith(".md")) return null;
  if (entry.type === "note_removed") return null;
  if (entry.type === "history_purged" && entry.detail?.why === "note_deleted") return null;
  return entry.note;
}

/** What to show of the fact an entry is about: its words, or that they are gone. */
export function activityFactText(entry: Pick<ActivityEntry, "fact">): { text: string; stored: boolean } | null {
  if (!entry.fact) return null;
  if (entry.fact.text === "") return { text: "Its words are no longer stored.", stored: false };
  return { text: entry.fact.text, stored: true };
}

// ---- days ----

/** The calendar day of a time as "2026-01-08", in the browser's time zone unless one is given. */
export function dayKey(ts: number, timeZone?: string): string {
  if (timeZone === undefined) {
    const date = new Date(ts);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }
  // en-CA writes a date as year-month-day.
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ts));
}

const DAY_MS = 86_400_000;

/** "Today", "Yesterday", or the date in the reader's locale. */
export function dayHeading(ts: number, now: number, locale?: string, timeZone?: string): string {
  const key = dayKey(ts, timeZone);
  if (key === dayKey(now, timeZone)) return "Today";
  // Noon of the day before, so a clock change cannot skip or repeat a day.
  const today = new Date(now);
  const noon = timeZone === undefined ? new Date(today.getFullYear(), today.getMonth(), today.getDate(), 12).getTime() : now;
  if (key === dayKey(noon - DAY_MS, timeZone)) return "Yesterday";
  const sameYear = key.slice(0, 4) === dayKey(now, timeZone).slice(0, 4);
  return new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short", year: sameYear ? undefined : "numeric", timeZone }).format(new Date(ts));
}

export type DayGroup<T> = { day: string; entries: T[] };

/** Entries grouped by calendar day, keeping the order they came in (newest first), each id once. */
export function groupByDay<T extends { id: string; ts: number }>(entries: readonly T[], timeZone?: string): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    if (seen.has(entry.id)) continue;
    seen.add(entry.id);
    const day = dayKey(entry.ts, timeZone);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.entries.push(entry);
    else groups.push({ day, entries: [entry] });
  }
  return groups;
}

/** The time of day of an entry, in the reader's locale. */
export function timeOfDay(ts: number, locale?: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(ts));
}
