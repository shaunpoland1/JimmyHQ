// A month of days for a calendar layout. A date field holds a calendar day ("2030-01-31"),
// which belongs to no time zone and is bucketed as written. The owner's time zone and week
// start matter at the edges: which day is "today", on which day a record was made, and which
// day a week begins on. They are read from settings by the caller and passed in.

import type { DescribedRecord, Value } from "../api.ts";

const DAY_MS = 86_400_000;

export type WeekStart = "monday" | "sunday" | "saturday";
export type Month = { year: number; month: number };

/** The weekday number (0 Sunday to 6 Saturday) a week begins on. An unknown setting is Monday. */
export function weekStartIndex(setting: unknown): number {
  return setting === "sunday" ? 0 : setting === "saturday" ? 6 : 1;
}

function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}

export function dayString(year: number, month: number, day: number): string {
  return new Date(Date.UTC(year, month - 1, day)).toISOString().slice(0, 10);
}

/** The calendar day a timestamp falls on in a named (IANA) time zone; "" is this computer's own. */
export function dayOfTimestamp(ts: number, timeZone: string): string {
  const parts = (zone: string | undefined) =>
    new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: zone }).formatToParts(new Date(ts));
  let found: Intl.DateTimeFormatPart[];
  try {
    found = parts(timeZone || undefined);
  } catch {
    found = parts("UTC");
  }
  const pick = (type: string) => found.find((p) => p.type === type)?.value ?? "";
  return `${pick("year")}-${pick("month")}-${pick("day")}`;
}

export function monthOf(day: string): Month {
  return { year: Number(day.slice(0, 4)), month: Number(day.slice(5, 7)) };
}

export function shiftMonth(at: Month, by: number): Month {
  const index = at.year * 12 + (at.month - 1) + by;
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

/** The weeks that cover a month, each a list of seven "YYYY-MM-DD" days starting on `weekStart`. */
export function monthGrid(at: Month, weekStart: number): string[][] {
  const first = Date.UTC(at.year, at.month - 1, 1);
  const lead = (new Date(first).getUTCDay() - weekStart + 7) % 7;
  const days = new Date(Date.UTC(at.year, at.month, 0)).getUTCDate();
  const weeks = Math.ceil((lead + days) / 7);
  const out: string[][] = [];
  for (let w = 0; w < weeks; w++) {
    const week: string[] = [];
    for (let d = 0; d < 7; d++) week.push(new Date(first + (w * 7 + d - lead) * DAY_MS).toISOString().slice(0, 10));
    out.push(week);
  }
  return out;
}

/** The first and last day of the grid, which are what a calendar asks the server for. */
export function gridRange(at: Month, weekStart: number): { from: string; to: string } {
  const grid = monthGrid(at, weekStart);
  const last = grid[grid.length - 1] ?? [];
  return { from: grid[0]?.[0] ?? dayString(at.year, at.month, 1), to: last[last.length - 1] ?? dayString(at.year, at.month, 28) };
}

/** Records by the day their date field holds; those with none are `undated`. */
export function bucketByDay(records: DescribedRecord[], field: string): { days: Map<string, DescribedRecord[]>; undated: DescribedRecord[] } {
  const days = new Map<string, DescribedRecord[]>();
  const undated: DescribedRecord[] = [];
  for (const record of records) {
    const value: Value | undefined = record.values[field];
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      undated.push(record);
      continue;
    }
    const list = days.get(value);
    if (list) list.push(record);
    else days.set(value, [record]);
  }
  return { days, undated };
}

/** The days of the week in order from `weekStart`, named in the locale: "Mon", "Tue", ... */
export function weekdayNames(weekStart: number, locale?: string, width: "short" | "narrow" | "long" = "short"): string[] {
  const format = new Intl.DateTimeFormat(locale, { weekday: width, timeZone: "UTC" });
  // 2023-01-01 was a Sunday.
  return Array.from({ length: 7 }, (_, i) => format.format(new Date(Date.UTC(2023, 0, 1 + ((weekStart + i) % 7)))));
}

export function monthTitle(at: Month, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(at.year, at.month - 1, 1)));
}

/** "Wednesday, November 18, 2030" for a day's accessible name. */
export function longDay(day: string, locale?: string): string {
  const ms = Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)));
  return new Intl.DateTimeFormat(locale, { dateStyle: "full", timeZone: "UTC" }).format(new Date(ms));
}

export function dayOfMonth(day: string): number {
  return Number(day.slice(8, 10));
}

export function inMonth(day: string, at: Month): boolean {
  return day.startsWith(`${pad(at.year, 4)}-${pad(at.month)}-`);
}
