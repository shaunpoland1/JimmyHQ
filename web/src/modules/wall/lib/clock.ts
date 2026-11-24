// The clock: what to show, and when to look again.

export type ClockOptions = { hour24: boolean; seconds: boolean };

export type ClockFace = {
  /** "2:05", "14:05" or "2:05:09": the digits, without AM/PM. */
  time: string;
  /** "PM" in a 12-hour clock, in the locale's own word; "" in a 24-hour one. */
  period: string;
  /** "Wednesday, November 18" in the locale's own order. */
  date: string;
};

function timeFormat(options: ClockOptions, locale?: string, timeZone?: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat(locale, {
    hour: options.hour24 ? "2-digit" : "numeric",
    minute: "2-digit",
    second: options.seconds ? "2-digit" : undefined,
    hourCycle: options.hour24 ? "h23" : "h12",
    timeZone,
  });
}

/** The clock at `now`, in the browser's locale unless one is given. */
export function clockFace(now: number, options: ClockOptions, locale?: string, timeZone?: string): ClockFace {
  const date = new Date(now);
  const parts = timeFormat(options, locale, timeZone).formatToParts(date);
  const period = parts.find((part) => part.type === "dayPeriod")?.value ?? "";
  const time = parts
    .filter((part) => part.type !== "dayPeriod")
    .map((part) => part.value)
    .join("")
    .trim();
  const day = new Intl.DateTimeFormat(locale, { weekday: "long", month: "long", day: "numeric", timeZone }).format(date);
  return { time, period, date: day };
}

/** A short time of day for a feed line: "2:05 PM" or "14:05". */
export function shortTime(ts: number, hour24: boolean, locale?: string, timeZone?: string): string {
  return timeFormat({ hour24, seconds: false }, locale, timeZone).format(new Date(ts));
}

/**
 * A task's "HH:MM" time of day, written the way the Wall's clock is: "2:05 PM"
 * or "14:05". Anything that is not a time is returned as it came.
 */
export function timeOfDay(time: string, hour24: boolean, locale?: string): string {
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  if (!match) return time;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return time;
  return shortTime(Date.UTC(2000, 0, 1, hours, minutes), hour24, locale, "UTC");
}

/** Milliseconds from `now` until the clock's next change: the next second, or the next minute. */
export function untilNextTick(now: number, seconds: boolean): number {
  const unit = seconds ? 1000 : 60_000;
  return unit - (((now % unit) + unit) % unit);
}

/**
 * How wide the clock is, in units of its own font size, so the type can be
 * sized to fill the tile without ever spilling out of it. Digits in the mono
 * face are 0.6 wide; AM/PM is set smaller, after a gap.
 */
export function clockWidthEm(face: Pick<ClockFace, "time" | "period">): number {
  const digits = Array.from(face.time).length * 0.6;
  const period = face.period ? 0.3 + Array.from(face.period).length * 0.6 * 0.3 : 0;
  return Math.round((digits + period) * 100) / 100;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** How long something has waited, readable at a glance: "under a minute", "14 min", "6 h", "3 days". */
export function formatWait(ms: number): string {
  if (!Number.isFinite(ms) || ms < MINUTE) return "under a minute";
  if (ms < HOUR) return `${Math.floor(ms / MINUTE)} min`;
  if (ms < 2 * DAY) return `${Math.floor(ms / HOUR)} h`;
  return `${Math.floor(ms / DAY)} days`;
}
