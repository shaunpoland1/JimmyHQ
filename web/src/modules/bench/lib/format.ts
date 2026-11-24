// Words and times for the Bench: ages, day headings, who filed a card, snooze
// choices and what a decision did.

import type { Action, HistoryEntry, Lane, SnoozeChoice } from "../api.ts";
import { dayKey, laneLabel } from "./board.ts";
import { NO_NAMES, shownInitials, shownName } from "../../../lib/agentNames.ts";
import type { ChosenNames } from "../../../lib/agentNames.ts";
import { plainActor } from "../../../lib/plainWords.ts";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Compact age: "now", "14 m", "6 h", "3 d". */
export function formatAge(ms: number): string {
  if (!Number.isFinite(ms) || ms < MINUTE) return "now";
  if (ms < HOUR) return `${Math.floor(ms / MINUTE)} m`;
  if (ms < DAY) return `${Math.floor(ms / HOUR)} h`;
  return `${Math.floor(ms / DAY)} d`;
}

/** "6 h ago", or "just now". */
export function formatAgo(ms: number): string {
  const age = formatAge(ms);
  return age === "now" ? "just now" : `${age} ago`;
}

/** `{ timeZone }` for Intl when a zone is named and this browser knows it; otherwise local time. */
function zoneOption(timeZone?: string): { timeZone?: string } {
  if (!timeZone || timeZone === "local") return {};
  try {
    new Intl.DateTimeFormat("en-CA", { timeZone });
    return { timeZone };
  } catch {
    return {};
  }
}

/** The calendar date of a moment, as that date's midnight in UTC, so dates can be subtracted. */
function calendarDay(ts: number, timeZone?: string): number {
  const zone = zoneOption(timeZone);
  const date = new Date(ts);
  if (!zone.timeZone) return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  const parts = new Intl.DateTimeFormat("en-CA", { ...zone, year: "numeric", month: "numeric", day: "numeric" }).formatToParts(date);
  const part = (type: string) => Number(parts.find((item) => item.type === type)?.value);
  return Date.UTC(part("year"), part("month") - 1, part("day"));
}

/**
 * Whole calendar days from `now` to `ts`: 0 today, -1 yesterday, 1 tomorrow.
 * In local time, or in `timeZone` (an IANA name) when one is given.
 */
export function dayOffset(ts: number, now: number, timeZone?: string): number {
  return Math.round((calendarDay(ts, timeZone) - calendarDay(now, timeZone)) / DAY);
}

/** Heading for a day group: "Today", "Yesterday", then the date. */
export function dayLabel(ts: number, now: number, locale?: string): string {
  const offset = dayOffset(ts, now);
  if (offset === 0) return "Today";
  if (offset === -1) return "Yesterday";
  const sameYear = new Date(ts).getFullYear() === new Date(now).getFullYear();
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  }).format(new Date(ts));
}

/**
 * A moment near now, in words: "4:00 PM", "tomorrow 9:00 AM", "Mon 9:00 AM", then date and time.
 * In local time, or in `timeZone` (an IANA name) when one is given.
 */
export function formatWhen(ts: number, now: number, locale?: string, timeZone?: string): string {
  const date = new Date(ts);
  const zone = zoneOption(timeZone);
  const time = new Intl.DateTimeFormat(locale, { ...zone, hour: "numeric", minute: "2-digit" }).format(date);
  const offset = dayOffset(ts, now, timeZone);
  if (offset === 0) return time;
  if (offset === 1) return `tomorrow ${time}`;
  if (offset === -1) return `yesterday ${time}`;
  if (offset > 1 && offset < 7) {
    return `${new Intl.DateTimeFormat(locale, { ...zone, weekday: "short" }).format(date)} ${time}`;
  }
  const sameYear = new Date(calendarDay(ts, timeZone)).getUTCFullYear() === new Date(calendarDay(now, timeZone)).getUTCFullYear();
  const day = new Intl.DateTimeFormat(locale, { ...zone, month: "short", day: "numeric", year: sameYear ? undefined : "numeric" }).format(date);
  return `${day}, ${time}`;
}

export function formatFull(ts: number, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(ts));
}

export type FilerKind = "owner" | "agent" | "token" | "other";
export type Filer = {
  kind: FilerKind;
  /** Name to show: "You" for the owner. */
  name: string;
  /** One letter for the tile. */
  letter: string;
  /** Which of the four agent colours the tile uses; null for a plain tile. */
  tone: 0 | 1 | 2 | 3 | null;
};

/** A small stable hash, so an agent keeps its colour whatever else is on the Bench. */
export function toneOf(name: string): 0 | 1 | 2 | 3 {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) hash = (hash * 31 + name.charCodeAt(index)) >>> 0;
  return (hash % 4) as 0 | 1 | 2 | 3;
}

function firstLetter(name: string): string {
  const first = Array.from(name.trim())[0];
  return first ? first.toUpperCase() : "?";
}

/**
 * Who filed a card, from the server's actor string ("owner", "agent:<id>", "token:<name>"). An
 * agent is shown as the Village shows it: the name it chose, else its working name, with the
 * letters of that name. Its colour stays by its id, so naming itself never changes it.
 */
export function describeFiler(filedBy: string, chosen: ChosenNames = NO_NAMES, source?: string | null): Filer {
  if (filedBy === "owner") return { kind: "owner", name: "You", letter: "Y", tone: null };
  const colon = filedBy.indexOf(":");
  const prefix = colon > 0 ? filedBy.slice(0, colon) : "";
  const rest = colon > 0 ? filedBy.slice(colon + 1) : filedBy;
  const name = rest.trim() || filedBy;
  if (prefix === "agent") return { kind: "agent", name: shownName(name, chosen), letter: shownInitials(name, chosen), tone: toneOf(name) };
  if (prefix === "token") return { kind: "token", name, letter: firstLetter(name), tone: null };
  if (prefix === "app") {
    const shown = plainActor(filedBy, source);
    return { kind: "other", name: shown, letter: firstLetter(shown), tone: null };
  }
  return { kind: "other", name: filedBy, letter: firstLetter(filedBy), tone: null };
}

export type KindTone = "ok" | "warn" | "accent";

/** The colour of a card's kind pill. */
export function kindTone(kind: string, lane: Lane): KindTone {
  if (lane === "done") return "ok";
  return kind.split(".").pop() === "finding" ? "warn" : "accent";
}

/**
 * The quick snooze choices to show at `now`. The server works the times out
 * (from the owner's settings and time zone) and this only drops any that have
 * passed since the Bench was last read; nothing here reads this device's clock
 * or zone.
 */
export function offeredSnoozes(choices: readonly SnoozeChoice[] | undefined, now: number): SnoozeChoice[] {
  return (choices ?? []).filter((choice) => choice.until > now);
}

/** Value for an `<input type="datetime-local">`, in local time. */
export function toLocalInput(ts: number): string {
  const date = new Date(ts);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${dayKey(ts)}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Reads a `datetime-local` value as local time; null if it is not one. */
export function fromLocalInput(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  if (year === undefined || month === undefined || day === undefined || hour === undefined || minute === undefined) return null;
  const date = new Date(year, month - 1, day, hour, minute);
  return Number.isNaN(date.getTime()) ? null : date.getTime();
}

const MAX_SNOOZE_DAYS = 366;

/** Why a picked snooze time cannot be used, in the server's words; null when it can. */
export function snoozeProblem(until: number | null, now: number): string | null {
  if (until === null) return "Pick a date and a time.";
  if (until <= now) return "A card can only be snoozed until a time in the future.";
  if (until > now + MAX_SNOOZE_DAYS * DAY) return "A card cannot be snoozed for more than a year.";
  return null;
}

/** The server's sentence for a missing reason, so the screen and the API say the same thing. */
export function reasonNeeded(label: string): string {
  return `A reason is needed for '${label}'. Say why in a sentence and try again.`;
}

function shorten(text: string, limit = 60): string {
  const chars = Array.from(text);
  return chars.length > limit ? `${chars.slice(0, limit - 1).join("").trimEnd()}…` : text;
}

/** One sentence for the toast after a decision. */
export function describeOutcome(action: Pick<Action, "label" | "closes" | "moves_to">, title: string): string {
  const name = `“${shorten(title)}”`;
  if (action.closes) return `${action.label}: ${name} is closed.`;
  if (action.moves_to) return `${action.label}: ${name} moved to ${laneLabel(action.moves_to)}.`;
  return `${action.label}: recorded on ${name}.`;
}

export type HistoryLine = { text: string; extra: string[] };

function actorName(actor: string, chosen: ChosenNames): string {
  return describeFiler(actor, chosen).name;
}

/** One history entry in words, with any note or reason as extra lines. */
export function historyLine(entry: HistoryEntry, now: number, locale?: string, chosen: ChosenNames = NO_NAMES): HistoryLine {
  const who = actorName(entry.actor, chosen);
  const extra: string[] = [];
  switch (entry.type) {
    case "filed":
      return { text: `${who} filed this`, extra };
    case "updated": {
      if (entry.note) extra.push(entry.note);
      const revised = entry.revised.length ? `revised the ${entry.revised.join(", ")}` : "added a note";
      return { text: `${who} ${revised}`, extra };
    }
    case "decided": {
      if (entry.reason) extra.push(`Reason: ${entry.reason}`);
      if (entry.note) extra.push(`Note: ${entry.note}`);
      const effect = entry.closes ? "closed" : entry.moves_to ? `moved to ${laneLabel(entry.moves_to)}` : "recorded";
      return { text: `${who} chose ${entry.label} (${effect})`, extra };
    }
    case "snoozed":
      return { text: `${who} snoozed it until ${formatWhen(entry.until, now, locale)}`, extra };
    case "unsnoozed":
      return { text: `${who} unsnoozed it`, extra };
    case "withdrawn":
      if (entry.note) extra.push(entry.note);
      return { text: `${who} withdrew this`, extra };
  }
}
