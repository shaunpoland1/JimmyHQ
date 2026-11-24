// Time zones for the page's picker: the list this browser knows, each with its offset
// from UTC right now, and a filter that forgives how people type them. The same feel as
// the shell's own picker (web/src/lib/timeZones.ts), written again because an app's page
// cannot use the shell's code. Pure: no DOM.

/** Zones offered when the browser will not list its own: one or two for each offset. */
export const FALLBACK_ZONES = [
  "Pacific/Honolulu", "America/Anchorage", "America/Los_Angeles", "America/Denver",
  "America/Phoenix", "America/Chicago", "America/New_York", "America/Toronto",
  "America/Halifax", "America/Sao_Paulo", "America/Argentina/Buenos_Aires",
  "Atlantic/Azores", "Europe/London", "Europe/Dublin", "Europe/Lisbon", "Europe/Paris",
  "Europe/Berlin", "Europe/Madrid", "Europe/Rome", "Africa/Lagos", "Africa/Johannesburg",
  "Europe/Athens", "Europe/Helsinki", "Africa/Cairo", "Europe/Istanbul", "Europe/Moscow",
  "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata", "Asia/Kathmandu", "Asia/Dhaka",
  "Asia/Bangkok", "Asia/Jakarta", "Asia/Singapore", "Asia/Shanghai", "Asia/Hong_Kong",
  "Asia/Tokyo", "Asia/Seoul", "Australia/Perth", "Australia/Adelaide", "Australia/Sydney",
  "Australia/Brisbane", "Pacific/Auckland", "UTC",
];

/** `America/New_York` reads better with a space. */
export function zoneName(zone) {
  return String(zone ?? "").replaceAll("_", " ");
}

/** Minutes ahead of UTC as `UTC+05:30`; zero is plain `UTC`. */
export function formatOffset(minutes) {
  if (minutes === 0) return "UTC";
  const sign = minutes < 0 ? "-" : "+";
  const abs = Math.abs(minutes);
  const pad = (value) => String(value).padStart(2, "0");
  return `UTC${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** How many minutes a zone is ahead of UTC at a moment, or null for a zone this browser does not know. */
export function offsetMinutes(zone, at) {
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

/** The zones this browser lists, or null when it will not say. */
export function browserZones(intl = globalThis.Intl) {
  try {
    const listed = intl?.supportedValuesOf?.("timeZone");
    return Array.isArray(listed) && listed.length ? listed : null;
  } catch {
    return null;
  }
}

/**
 * Every zone to offer, sorted by name: the browser's own list (`supported`), or the
 * fallback when there is none, with UTC and each of `keep` (the page's stored zone, the
 * owner's) always in it.
 */
export function zoneNames(keep = [], supported = null) {
  const names = new Set(supported && supported.length ? supported : FALLBACK_ZONES);
  names.add("UTC");
  for (const zone of keep) if (zone) names.add(zone);
  return [...names].sort((a, b) => zoneName(a).localeCompare(zoneName(b), "en"));
}

/** Each zone as {zone, name, offset} at the moment `at`. */
export function zoneOptions(zones, at) {
  return zones.map((zone) => {
    const minutes = offsetMinutes(zone, at);
    return { zone, name: zoneName(zone), offset: minutes === null ? "" : formatOffset(minutes) };
  });
}

function fold(text) {
  return String(text ?? "").toLowerCase().replace(/[_/]+/g, " ").replace(/\s+/g, " ").trim();
}

/**
 * The options that match what was typed. Every word must be found in the name
 * (`new york`, `york america`), or it must be the offset or its hours (`+5:30`,
 * `utc-8`, `+1` but not `+10`). Names that start with what was typed, or have a part
 * that does, come first.
 */
export function filterZones(options, query) {
  const typed = fold(query);
  if (!typed) return [...options];
  const words = typed.split(" ");
  const asOffset = typed.replace(/\s+/g, "").replace(/^gmt/, "utc");
  const scored = [];
  for (const option of options) {
    const name = fold(option.name);
    const offset = option.offset.toLowerCase();
    const short = offset.replace(/([+-])0(\d)/, "$1$2");
    let score = -1;
    if (words.every((word) => name.includes(word))) {
      const starts = name.split(" ").some((part) => part.startsWith(words[0] ?? ""));
      score = name.startsWith(typed) ? 0 : starts ? 1 : 2;
    } else if (/[+-]\d/.test(asOffset) && [offset, short, offset.slice(3), short.slice(3)]
      .some((form) => form === asOffset || (form.startsWith(asOffset) && form[asOffset.length] === ":"))) {
      score = 3;
    }
    if (score >= 0) scored.push({ option, score });
  }
  return scored.sort((a, b) => a.score - b.score).map((item) => item.option);
}

/** The words for the first choice, which stores nothing: the owner's own zone. */
export function useMineLabel(ownerZone) {
  return ownerZone ? `Use mine (${zoneName(ownerZone)})` : "Use mine";
}

/**
 * What the list shows for what was typed: "Use mine" first while nothing is typed,
 * then the matches. "Use mine" is {zone: ""}, which the page stores as "use the owner's".
 */
export function shownChoices(options, query, ownerZone) {
  const matches = filterZones(options, query);
  if (fold(query)) return matches;
  return [{ zone: "", name: useMineLabel(ownerZone), offset: "" }, ...matches];
}

/** Where the highlight goes when the list opens: on the stored choice, else on "Use mine". */
export function startIndex(shown, value) {
  if (!value) return 0;
  const at = shown.findIndex((option) => option.zone === value);
  return at >= 0 ? at : 0;
}

/** The highlight after an arrow key: down is +1, up is -1, wrapping round. */
export function stepIndex(current, length, step) {
  if (length <= 0) return 0;
  return (current + step + length) % length;
}
