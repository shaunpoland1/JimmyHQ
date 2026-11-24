// The diary: days listed newest first, a page of them at a time, and how a day is named.

import { toMs } from "./status.ts";

/** How many days the list shows before "Show older days". */
export const DAYS_PER_PAGE = 14;

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The server lists days oldest first; the owner wants the latest first. Anything that is not a day is dropped. */
export function newestFirst(days: readonly string[] | undefined): string[] {
  return [...new Set((days ?? []).filter((day) => DAY.test(day)))].sort().reverse();
}

/** The first `pages` pages of days, and whether older ones are still hidden. */
export function visibleDays(days: readonly string[], pages: number): { shown: string[]; hidden: number } {
  const count = Math.max(1, pages) * DAYS_PER_PAGE;
  return { shown: days.slice(0, count), hidden: Math.max(0, days.length - count) };
}

/** How many pages are needed for the list to include `day`, so a day picked by "older" stays visible. */
export function pagesToShow(days: readonly string[], day: string, pages: number): number {
  const at = days.indexOf(day);
  return at < 0 ? pages : Math.max(pages, Math.floor(at / DAYS_PER_PAGE) + 1);
}

/** The days either side of `day`, in a newest-first list. Null at the ends. */
export function neighbours(days: readonly string[], day: string): { newer: string | null; older: string | null } {
  const at = days.indexOf(day);
  if (at < 0) return { newer: null, older: null };
  return { newer: days[at - 1] ?? null, older: days[at + 1] ?? null };
}

function localDay(ms: number): string {
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** "Today", "Yesterday", or the weekday and date, with the year only when it is not this one. */
export function dayHeading(day: string, now: number = Date.now()): string {
  if (!DAY.test(day)) return day;
  if (day === localDay(now)) return "Today";
  if (day === localDay(now - 86_400_000)) return "Yesterday";
  const [year, month, date] = day.split("-").map(Number) as [number, number, number];
  const thisYear = new Date(now).getFullYear();
  return new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: year === thisYear ? undefined : "numeric" }).format(new Date(year, month - 1, date));
}

/** "3 entries, 1 a dream". */
export function entryCount(entries: readonly { dream?: boolean }[]): string {
  const dreams = entries.filter((entry) => entry.dream).length;
  const base = `${entries.length} ${entries.length === 1 ? "entry" : "entries"}`;
  if (dreams === 0) return base;
  return `${base}, ${dreams === 1 ? "1 a dream" : `${dreams} dreams`}`;
}

/** A time the server may send as seconds or milliseconds, as milliseconds; 0 when it is missing. */
export function msOf(value: number | null | undefined): number {
  return toMs(value) ?? 0;
}
