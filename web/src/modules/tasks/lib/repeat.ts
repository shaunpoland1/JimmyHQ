// The friendly repeat picker: its state, the rule it sends, and reading a
// stored rule back into it. Anything it cannot express stays as a raw rule.

export type RepeatMode = "none" | "daily" | "weekdays" | "weekly" | "biweekly" | "monthly" | "yearly" | "advanced";

export const WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"] as const;
export type Weekday = (typeof WEEKDAYS)[number];

export type RepeatChoice = {
  mode: RepeatMode;
  /** For "weekly": the chosen days. Empty means the weekday of the due date. */
  days: Weekday[];
  /** For "monthly": the day of the month, 1-31. */
  monthDay: number;
  /** For "advanced": the raw rule as typed. */
  raw: string;
};

export const REPEAT_MODES: readonly { mode: RepeatMode; label: string }[] = [
  { mode: "none", label: "Does not repeat" },
  { mode: "daily", label: "Every day" },
  { mode: "weekdays", label: "Every weekday" },
  { mode: "weekly", label: "Every week on…" },
  { mode: "biweekly", label: "Every other week" },
  { mode: "monthly", label: "Every month on a day" },
  { mode: "yearly", label: "Every year" },
  { mode: "advanced", label: "Advanced rule" },
];

const WORKDAYS: readonly Weekday[] = ["MO", "TU", "WE", "TH", "FR"];

export function emptyRepeat(monthDay = 1): RepeatChoice {
  return { mode: "none", days: [], monthDay, raw: "" };
}

function isWeekday(value: string): value is Weekday {
  return (WEEKDAYS as readonly string[]).includes(value);
}

function sortDays(days: readonly Weekday[]): Weekday[] {
  return WEEKDAYS.filter((day) => days.includes(day));
}

/** The rule to send for a choice; null means "does not repeat". */
export function ruleFor(choice: RepeatChoice): string | null {
  switch (choice.mode) {
    case "none":
      return null;
    case "daily":
      return "FREQ=DAILY";
    case "weekdays":
      return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR";
    case "weekly": {
      const days = sortDays(choice.days);
      return days.length ? `FREQ=WEEKLY;BYDAY=${days.join(",")}` : "FREQ=WEEKLY";
    }
    case "biweekly":
      return "FREQ=WEEKLY;INTERVAL=2";
    case "monthly":
      return `FREQ=MONTHLY;BYMONTHDAY=${choice.monthDay}`;
    case "yearly":
      return "FREQ=YEARLY";
    case "advanced": {
      const raw = choice.raw.trim();
      return raw ? raw : null;
    }
  }
}

function parts(rule: string): Map<string, string> | null {
  const map = new Map<string, string>();
  for (const piece of rule.trim().toUpperCase().replace(/^RRULE:/, "").split(";")) {
    if (!piece) continue;
    const at = piece.indexOf("=");
    if (at <= 0) return null;
    map.set(piece.slice(0, at), piece.slice(at + 1));
  }
  return map;
}

/** Reads a stored rule into the picker; rules it cannot express become "advanced". */
export function choiceFor(rule: string | null, monthDay = 1): RepeatChoice {
  const base = emptyRepeat(monthDay);
  if (!rule || !rule.trim()) return base;
  const advanced: RepeatChoice = { ...base, mode: "advanced", raw: rule };
  const map = parts(rule);
  if (!map) return advanced;
  const freq = map.get("FREQ");
  const interval = map.get("INTERVAL") ?? "1";
  const only = (...keys: string[]) => [...map.keys()].every((key) => key === "FREQ" || key === "INTERVAL" || keys.includes(key));

  if (freq === "DAILY" && interval === "1" && only()) return { ...base, mode: "daily" };
  if (freq === "YEARLY" && interval === "1" && only()) return { ...base, mode: "yearly" };
  if (freq === "WEEKLY" && interval === "2" && only()) return { ...base, mode: "biweekly" };
  if (freq === "WEEKLY" && interval === "1" && only("BYDAY")) {
    const days = (map.get("BYDAY") ?? "").split(",").filter(Boolean);
    if (!days.every(isWeekday)) return advanced;
    const sorted = sortDays(days);
    if (sorted.length === WORKDAYS.length && WORKDAYS.every((day) => sorted.includes(day))) return { ...base, mode: "weekdays" };
    return { ...base, mode: "weekly", days: sorted };
  }
  if (freq === "MONTHLY" && interval === "1" && only("BYMONTHDAY") && map.has("BYMONTHDAY")) {
    const day = Number(map.get("BYMONTHDAY"));
    if (Number.isInteger(day) && day >= 1 && day <= 31) return { ...base, mode: "monthly", monthDay: day };
  }
  return advanced;
}

/** Adds or removes a day from the weekly choice. */
export function toggleDay(days: readonly Weekday[], day: Weekday): Weekday[] {
  return days.includes(day) ? days.filter((item) => item !== day) : sortDays([...days, day]);
}

/** Weekdays starting from the locale's first day of the week (1 = Monday … 7 = Sunday). */
export function weekdaysFrom(firstDay: number): Weekday[] {
  const start = Number.isInteger(firstDay) && firstDay >= 1 && firstDay <= 7 ? firstDay - 1 : 0;
  return [...WEEKDAYS.slice(start), ...WEEKDAYS.slice(0, start)];
}

/** The locale's short name for a weekday, e.g. "Mon". */
export function weekdayName(day: Weekday, locale?: string, width: "short" | "long" = "short"): string {
  // 2024-01-01 was a Monday.
  const ms = Date.UTC(2024, 0, 1 + WEEKDAYS.indexOf(day));
  return new Intl.DateTimeFormat(locale, { weekday: width, timeZone: "UTC" }).format(new Date(ms));
}
