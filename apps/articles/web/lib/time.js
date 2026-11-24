// Times as the owner reads them: in the article's site's time zone, else the owner's.
// Stored times are unix milliseconds, UTC; they are turned into words only here. Pure.

const MINUTE = 60_000;
const DAY = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const formatters = new Map();

/** Whether this browser knows the time zone by that name. */
export function knownZone(zone) {
  if (!zone) return false;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** The zone a site's times are shown in: its own, else the owner's, else the browser's. */
export function zoneFor(site, ownerZone) {
  for (const zone of [site?.timezone, ownerZone]) if (knownZone(zone)) return zone;
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

function wall(ms, zone) {
  const key = zone || "";
  if (!formatters.has(key)) {
    formatters.set(key, new Intl.DateTimeFormat("en-GB", {
      timeZone: zone || undefined, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23", weekday: "short",
    }));
  }
  const out = {};
  for (const part of formatters.get(key).formatToParts(new Date(ms))) {
    if (part.type === "weekday") out.weekday = part.value;
    else if (part.type !== "literal") out[part.type] = Number(part.value);
  }
  return out;
}

const pad = (n) => String(n).padStart(2, "0");

/** "Fri 15 Jan, 08:00", with the year when it is not this year's. */
export function formatWhen(ms, zone, nowMs = Date.now()) {
  if (ms === null || ms === undefined) return "";
  const w = wall(ms, zone);
  const year = w.year === wall(nowMs, zone).year ? "" : ` ${w.year}`;
  return `${w.weekday} ${w.day} ${MONTHS[w.month - 1]}${year}, ${pad(w.hour)}:${pad(w.minute)}`;
}

/** A plain "3 days ago" or "in 2 hours". */
export function relative(ms, nowMs) {
  const gap = ms - nowMs;
  const size = Math.abs(gap);
  if (size < MINUTE) return "just now";
  const [unit, one] = size < 60 * MINUTE ? [MINUTE, "minute"] : size < DAY ? [60 * MINUTE, "hour"] : [DAY, "day"];
  const n = Math.round(size / unit);
  const words = `${n} ${one}${n === 1 ? "" : "s"}`;
  return gap > 0 ? `in ${words}` : `${words} ago`;
}
