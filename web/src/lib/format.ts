// Number, byte and time formatting.

import type { TapeStats } from "../api.ts";

export function formatCount(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale).format(value);
}

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;

/** Compact size: megabytes with one decimal from 0.1 MB up, smaller units below. */
export function formatBytes(bytes: number, locale?: string): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const one = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  if (bytes >= GB) return `${one.format(bytes / GB)} GB`;
  if (bytes >= MB / 20) return `${one.format(bytes / MB)} MB`;
  if (bytes >= KB) return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(bytes / KB)} KB`;
  return `${Math.round(bytes)} B`;
}

/** "TAPE 1,234 events · 0.4 MB" */
export function formatTapeUsage(stats: Pick<TapeStats, "events" | "bytes">, locale?: string): string {
  const noun = stats.events === 1 ? "event" : "events";
  return `TAPE ${formatCount(stats.events, locale)} ${noun} · ${formatBytes(stats.bytes, locale)}`;
}

/** Joins the usage segments that have data; empty ones render nothing. */
export function joinUsage(segments: readonly (string | null | undefined)[]): string {
  return segments.filter((segment): segment is string => Boolean(segment)).join("  |  ");
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Time of day for today's events, short date and time for older ones. */
export function formatEventTime(ts: number, now: number = Date.now(), locale?: string, timeZone?: string): string {
  const date = new Date(ts);
  const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone });
  if (timeZone === undefined && sameDay(date, new Date(now))) return time.format(date);
  if (timeZone !== undefined) {
    const day = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" });
    if (day.format(date) === day.format(new Date(now))) return time.format(date);
  }
  const short = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone });
  return short.format(date);
}

export function formatFullTime(ts: number, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(ts));
}
