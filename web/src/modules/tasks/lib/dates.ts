// Calendar-date arithmetic and display for "YYYY-MM-DD" strings. All arithmetic
// is on whole days in UTC so a clock change can never move a date.

const DAY_MS = 86_400_000;

/** UTC midnight of a "YYYY-MM-DD" date, or null when it is not a real date. */
export function dayToUtc(day: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!match) return null;
  const [year, month, date] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const ms = Date.UTC(year, month - 1, date);
  const check = new Date(ms);
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== date) return null;
  return ms;
}

function utcToDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** The date `count` days after `day` (negative goes back). Returns `day` unchanged if unreadable. */
export function addDays(day: string, count: number): string {
  const ms = dayToUtc(day);
  return ms === null ? day : utcToDay(ms + count * DAY_MS);
}

/** Whole days from `from` to `to`; positive when `to` is later. 0 if either is unreadable. */
export function daysBetween(from: string, to: string): number {
  const a = dayToUtc(from);
  const b = dayToUtc(to);
  return a === null || b === null ? 0 : Math.round((b - a) / DAY_MS);
}

/** The later of two dates. */
export function laterDay(a: string, b: string): string {
  return a >= b ? a : b;
}

/** The date a one-day push lands on: the day after the due date, never earlier than tomorrow. */
export function pushOneDayTarget(due: string | null, today: string): string {
  return addDays(due ? laterDay(due, today) : today, 1);
}

/** The earliest date the server accepts for a push. */
export function earliestPush(due: string | null, today: string): string {
  return due && due >= today ? addDays(due, 1) : today;
}

/**
 * The calendar date a timestamp falls on in a named (IANA) time zone, or null
 * when the name is not one this browser knows. The browser's own zone is never
 * used: which day something happened on is the server's to say.
 */
export function dayOfTimestamp(ts: number, timezone: string): string | null {
  let parts: Intl.DateTimeFormatPart[];
  try {
    parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: timezone }).formatToParts(new Date(ts));
  } catch {
    return null;
  }
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${pick("year")}-${pick("month")}-${pick("day")}`;
}

function format(day: string, options: Intl.DateTimeFormatOptions, locale?: string): string {
  const ms = dayToUtc(day);
  if (ms === null) return day;
  return new Intl.DateTimeFormat(locale, { ...options, timeZone: "UTC" }).format(new Date(ms));
}

/** "Nov 18"; the year is added when it differs from today's. */
export function formatDay(day: string, today: string, locale?: string): string {
  const sameYear = day.slice(0, 4) === today.slice(0, 4);
  return format(day, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" }, locale);
}

/** "Wednesday, Nov 18" */
export function formatLongDay(day: string, locale?: string): string {
  return format(day, { weekday: "long", month: "short", day: "numeric" }, locale);
}

/** "Wed, Nov 18" */
export function formatShortDay(day: string, locale?: string): string {
  return format(day, { weekday: "short", month: "short", day: "numeric" }, locale);
}

/** A "HH:MM" time in the locale's own style, e.g. "2:00 PM" or "14:00". */
export function formatClock(time: string, locale?: string): string {
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  if (!match) return time;
  const ms = Date.UTC(2000, 0, 1, Number(match[1]), Number(match[2]));
  return new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit", timeZone: "UTC" }).format(new Date(ms));
}

/** "1 day late" / "3 days late" */
export function lateText(days: number): string {
  return `${days} ${days === 1 ? "day" : "days"} late`;
}

/** "just now", "14 min ago", "3 h ago", then the date. */
export function formatAgo(ts: number, now: number, locale?: string): string {
  const minutes = Math.floor((now - ts) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(new Date(ts));
}
