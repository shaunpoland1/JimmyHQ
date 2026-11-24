// Words and shapes for the page: pure functions with no page in them, so a test can run them.
// Times arrive as unix milliseconds in UTC and are shown in the owner's own time zone.

export const TABS = [
  ["days", "Conversations"],
  ["search", "Search"],
  ["settings", "Settings"],
];

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

/** What an import tells the owner: what came in, and anything not understood. */
export function importWords(made, lines, skipped) {
  const left = skipped
    ? ` ${plural(skipped, "item was", "items were")} not understood and ${skipped === 1 ? "was" : "were"} left out.`
    : "";
  return `Imported ${plural(made, "new conversation", "new conversations")} and ${plural(lines, "line", "lines")}.${left}`
    + " Anything already kept was left as it was.";
}

/** "14:05" in the owner's time zone (the computer's when `zone` is empty). */
export function clock(ms, zone) {
  if (!Number.isFinite(ms)) return "";
  const options = { hour: "2-digit", minute: "2-digit", hour12: false };
  if (zone) options.timeZone = zone;
  return new Intl.DateTimeFormat("en-GB", options).format(new Date(ms));
}

/** "15 January 2027, 08:00" in the owner's time zone. */
export function whenWords(ms, zone) {
  if (!Number.isFinite(ms)) return "";
  const options = { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false };
  if (zone) options.timeZone = zone;
  return new Intl.DateTimeFormat("en-GB", options).format(new Date(ms));
}

/** "Today", "Yesterday" or "Friday 15 January 2027" for a `YYYY-MM-DD` day. */
export function dayHeading(day, today) {
  if (day === today) return "Today";
  if (today && day === addDays(today, -1)) return "Yesterday";
  const date = parseDay(day);
  if (!date) return String(day ?? "");
  return new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(date);
}

export function parseDay(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day ?? ""));
  if (!m) return null;
  const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return Number.isNaN(date.getTime()) ? null : date;
}

export function addDays(day, n) {
  const date = parseDay(day);
  if (!date) return day;
  date.setUTCDate(date.getUTCDate() + n);
  return date.toISOString().slice(0, 10);
}

/** How long a conversation lasted, in words: "under a minute", "12 min", "1 h 5 min". */
export function lengthWords(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 60) return "under a minute";
  const minutes = Math.round(s / 60);
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** Who spoke: "You and Sam", "You, Sam and 2 more". */
export function whoWords(names, total) {
  const list = (names ?? []).filter(Boolean);
  if (!list.length) return "";
  const more = Math.max(0, (total ?? list.length) - list.length);
  const shown = more ? [...list, plural(more, "more", "more")] : list;
  if (shown.length === 1) return shown[0];
  return `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}

/** Conversations (newest first) as days: [{day, items}], keeping their order. */
export function groupByDay(conversations) {
  const days = [];
  for (const item of conversations ?? []) {
    const last = days[days.length - 1];
    if (last && last.day === item.day) last.items.push(item);
    else days.push({ day: item.day, items: [item] });
  }
  return days;
}

/** Add a later page of conversations to the days already shown, merging a shared day. */
export function addPage(days, conversations) {
  const merged = days.map((d) => ({ day: d.day, items: [...d.items] }));
  for (const d of groupByDay(conversations)) {
    const last = merged[merged.length - 1];
    if (last && last.day === d.day) last.items.push(...d.items);
    else merged.push(d);
  }
  return merged;
}

const encoder = new TextEncoder();

/** Bytes a value takes as JSON text. */
export function jsonBytes(value) {
  return encoder.encode(JSON.stringify(value)).length;
}

/**
 * Divide exported conversations into calls that stay under `maxBytes` and `maxCount`: one
 * call may carry about 256 KB. A piece too big on its own still goes, alone.
 */
export function batches(items, maxBytes = 200_000, maxCount = 100) {
  const out = [];
  let current = [];
  let size = 0;
  for (const item of items) {
    const cost = jsonBytes(item) + 1;
    if (current.length && (size + cost > maxBytes || current.length >= maxCount)) {
      out.push(current);
      current = [];
      size = 0;
    }
    current.push(item);
    size += cost;
  }
  if (current.length) out.push(current);
  return out;
}

/** What a file the owner picked holds: {conversations} or {error: sentence}. */
export function readExport(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return { error: "That file is not a Heard export: it could not be read as JSON. Nothing was imported." };
  }
  if (!data || typeof data !== "object" || data.app !== "heard" || !Array.isArray(data.conversations)) {
    return { error: "That file is not a Heard export, so nothing was imported. Choose a file saved by Export everything." };
  }
  if (data.format !== 1) {
    return { error: "That file was saved by a different version of Heard, so nothing was imported." };
  }
  return { conversations: data.conversations };
}

/** Split a search snippet's parts into runs for drawing: [{text, hit}]. */
export function runs(parts) {
  return (parts ?? []).map(([text, hit]) => ({ text: String(text ?? ""), hit: hit === true }));
}

/** Which colour class a speaker's lines take: the wearer is one, everyone else by order. */
export function speakerClass(name, speakers) {
  const index = (speakers ?? []).findIndex((s) => s.name === name);
  const mine = (speakers ?? []).find((s) => s.name === name)?.is_user;
  if (mine) return "who-you";
  return `who-${(Math.max(index, 0) % 4) + 1}`;
}

export function sizeWords(bytes) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
