// Times as the owner reads them: in the page's own time zone, else the owner's.
// Stored times are unix milliseconds, UTC; they are turned into words only here.
// Pure: no DOM, no bridge.

const MINUTE = 60_000;
const DAY = 86_400_000;

const formatters = new Map();

function formatter(zone) {
  const key = zone || "";
  if (!formatters.has(key)) {
    formatters.set(key, new Intl.DateTimeFormat("en-GB", {
      timeZone: zone || undefined, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", weekday: "short",
    }));
  }
  return formatters.get(key);
}

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

/** The zone a page's times are shown in: its own, else the owner's, else the browser's. */
export function zoneFor(page, ownerZone) {
  for (const zone of [page?.timezone, ownerZone]) if (knownZone(zone)) return zone;
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** The wall-clock parts of `ms` in `zone`: {year, month, day, hour, minute, second, weekday}. */
export function wall(ms, zone) {
  const out = {};
  for (const part of formatter(zone).formatToParts(new Date(ms))) {
    if (part.type === "weekday") out.weekday = part.value;
    else if (part.type !== "literal") out[part.type] = Number(part.value);
  }
  return out;
}

const pad = (n) => String(n).padStart(2, "0");

/** "2027-01-15": the day `ms` falls on in `zone`. */
export function dayKey(ms, zone) {
  const w = wall(ms, zone);
  return `${w.year}-${pad(w.month)}-${pad(w.day)}`;
}

/** How far `zone` is ahead of UTC at `ms`, in milliseconds. */
export function offsetAt(ms, zone) {
  const w = wall(ms, zone);
  const asUtc = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second);
  return asUtc - Math.floor(ms / 1000) * 1000;
}

/** The value of a `datetime-local` field showing `ms` in `zone`: "2027-01-15T08:00". */
export function toInputValue(ms, zone) {
  const w = wall(ms, zone);
  return `${w.year}-${pad(w.month)}-${pad(w.day)}T${pad(w.hour)}:${pad(w.minute)}`;
}

const INPUT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;

/**
 * The unix milliseconds of a `datetime-local` value read in `zone`, or
 * `{error}` with a sentence: a value that is not a time, or a time that does not
 * happen there because the clocks go forward.
 */
export function fromInputValue(value, zone) {
  const m = INPUT.exec(String(value ?? "").trim());
  if (!m) return { error: "Choose a day and a time." };
  const [y, mo, d, h, mi] = m.slice(1).map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  let ms = guess - offsetAt(guess, zone);
  const again = guess - offsetAt(ms, zone);
  if (again !== ms) ms = again;
  if (toInputValue(ms, zone) !== String(value).trim()) {
    return { error: `${pad(h)}:${pad(mi)} does not happen on that day in ${zone}, because the clocks go forward. Choose another time.` };
  }
  return { ms };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Fri 15 Jan, 08:00", with the year when it is not this year's. */
export function formatSlot(ms, zone, nowMs = Date.now()) {
  if (ms === null || ms === undefined) return "No time yet";
  const w = wall(ms, zone);
  const year = w.year === wall(nowMs, zone).year ? "" : ` ${w.year}`;
  return `${w.weekday} ${w.day} ${MONTHS[w.month - 1]}${year}, ${pad(w.hour)}:${pad(w.minute)}`;
}

/** A plain "3 days ago" or "in 2 hours". */
export function relative(ms, nowMs) {
  const gap = ms - nowMs;
  const ahead = gap > 0;
  const size = Math.abs(gap);
  let words;
  if (size < MINUTE) return "now";
  if (size < 60 * MINUTE) words = `${Math.round(size / MINUTE)} minute${Math.round(size / MINUTE) === 1 ? "" : "s"}`;
  else if (size < DAY) words = `${Math.round(size / (60 * MINUTE))} hour${Math.round(size / (60 * MINUTE)) === 1 ? "" : "s"}`;
  else words = `${Math.round(size / DAY)} day${Math.round(size / DAY) === 1 ? "" : "s"}`;
  return ahead ? `in ${words}` : `${words} ago`;
}

/**
 * The calendar strip: `days` days from today in `zone`, each with the posts whose slot
 * falls on it, in slot order. Posts after the last day are counted in `later`, posts
 * with no time in `unset`. `zoneOf(post)` gives a post's own zone for its day.
 */
export function calendar(posts, { zone, nowMs, days, zoneOf = () => zone }) {
  const out = [];
  const index = new Map();
  // Steps from today's noon never skip or repeat a day across a change of clocks.
  const noon = fromInputValue(`${dayKey(nowMs, zone)}T12:00`, zone).ms ?? nowMs;
  for (let i = 0; i < days; i += 1) {
    const at = noon + i * DAY;
    const key = dayKey(at, zone);
    if (index.has(key)) continue;
    const w = wall(at, zone);
    const entry = { key, label: `${w.weekday} ${w.day}`, today: i === 0, posts: [] };
    index.set(key, entry);
    out.push(entry);
  }
  let later = 0;
  let unset = 0;
  const sorted = [...posts].sort((a, b) => (a.slot_ms ?? Infinity) - (b.slot_ms ?? Infinity) || a.id - b.id);
  for (const post of sorted) {
    if (post.slot_ms === null || post.slot_ms === undefined) {
      unset += 1;
      continue;
    }
    const entry = index.get(dayKey(post.slot_ms, zoneOf(post) || zone));
    if (entry) entry.posts.push(post);
    else if (post.slot_ms > nowMs) later += 1;
  }
  return { days: out, later, unset };
}
