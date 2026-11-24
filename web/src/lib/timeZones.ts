// Time zones for a picker: the list this browser knows, each with its offset
// from UTC right now, and a filter that forgives how people type them.

export type TimeZoneOption = {
  /** The IANA name, which is what gets stored: `America/New_York`. */
  zone: string;
  /** The name as shown: `America/New York`. */
  name: string;
  /** The offset from UTC at the moment asked about: `UTC-05:00`. Empty if this browser cannot say. */
  offset: string;
};

/** `America/New_York` reads better with a space. */
export function zoneName(zone: string): string {
  return zone.replaceAll("_", " ");
}

/** Minutes ahead of UTC as `UTC+05:30`; zero is plain `UTC`. */
export function formatOffset(minutes: number): string {
  if (minutes === 0) return "UTC";
  const sign = minutes < 0 ? "-" : "+";
  const abs = Math.abs(minutes);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `UTC${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** How many minutes a zone is ahead of UTC at a moment, or null for a zone this browser does not know. */
export function offsetMinutes(zone: string, at: Date): number | null {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "longOffset" }).formatToParts(at);
    const text = parts.find((part) => part.type === "timeZoneName")?.value ?? "";
    if (text === "GMT" || text === "UTC") return 0;
    const match = /^(?:GMT|UTC)([+-])(\d{1,2})(?::?(\d{2}))?$/.exec(text);
    if (!match) return null;
    const minutes = Number(match[2]) * 60 + Number(match[3] ?? 0);
    return match[1] === "-" ? -minutes : minutes;
  } catch {
    return null;
  }
}

/** True for a time zone name this browser can work with. */
export function isKnownZone(zone: string): boolean {
  return zone !== "" && offsetMinutes(zone, new Date(0)) !== null;
}

/** The time zone this device is set to, or null if the browser will not say. */
export function deviceTimeZone(): string | null {
  try {
    const zone = new Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zone && isKnownZone(zone) ? zone : null;
  } catch {
    return null;
  }
}

/**
 * Every time zone to offer, by name. `keep` are zones that must be in the list
 * whatever the browser says: the one already stored, and this device's own.
 */
export function timeZoneNames(keep: readonly (string | null | undefined)[] = [], supported?: readonly string[]): string[] {
  let known = supported;
  if (!known) {
    try {
      known = Intl.supportedValuesOf("timeZone");
    } catch {
      known = [];
    }
  }
  const names = new Set<string>(known);
  names.add("UTC");
  for (const zone of keep) if (zone) names.add(zone);
  return [...names].sort((a, b) => zoneName(a).localeCompare(zoneName(b), "en"));
}

export function timeZoneOptions(zones: readonly string[], at: Date): TimeZoneOption[] {
  return zones.map((zone) => {
    const minutes = offsetMinutes(zone, at);
    return { zone, name: zoneName(zone), offset: minutes === null ? "" : formatOffset(minutes) };
  });
}

function fold(text: string): string {
  return text.toLowerCase().replace(/[_/]+/g, " ").replace(/\s+/g, " ").trim();
}

/**
 * The options that match what was typed. Every word must be found in the name
 * (`new york`, `york america`), or it must be the offset or its hours
 * (`+5:30`, `utc-8`, `+1` but not `+10`). Names that start with what was typed, or have a part that does,
 * come first.
 */
export function filterTimeZones(options: readonly TimeZoneOption[], query: string): TimeZoneOption[] {
  const typed = fold(query);
  if (!typed) return [...options];
  const words = typed.split(" ");
  const asOffset = typed.replace(/\s+/g, "").replace(/^gmt/, "utc");
  const scored: { option: TimeZoneOption; score: number }[] = [];
  for (const option of options) {
    const name = fold(option.name);
    const offset = option.offset.toLowerCase();
    const short = offset.replace(/([+-])0(\d)/, "$1$2");
    let score = -1;
    if (words.every((word) => name.includes(word))) {
      const starts = name.split(" ").some((part) => part.startsWith(words[0] ?? ""));
      score = name.startsWith(typed) ? 0 : starts ? 1 : 2;
    } else if (/[+-]\d/.test(asOffset) && [offset, short, offset.slice(3), short.slice(3)].some((form) => form === asOffset || (form.startsWith(asOffset) && form[asOffset.length] === ":"))) {
      score = 3;
    }
    if (score >= 0) scored.push({ option, score });
  }
  return scored.sort((a, b) => a.score - b.score).map((item) => item.option);
}
