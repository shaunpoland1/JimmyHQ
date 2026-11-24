// The Scheduled view: what SocialBu has scheduled, as rows grouped by day in the owner's
// time zone. Pure: no DOM, no bridge. Every string made here is plain text. The text of a
// scheduled post comes from outside, and the view puts it on the page as text only.

import { QUEUE_STATES } from "./lists.js";
import { dayKey, fromInputValue, wall } from "./time.js";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");

export const MOST_LINES = 3;
export const MOST_CHARS = 280;
export const NO_TIME = "No time given";

/** "Read 5 minutes ago": how long ago the list was read from SocialBu. */
export function readLine(readMs, nowMs) {
  if (readMs === null || readMs === undefined) return "Not read yet";
  const gap = Math.max(0, nowMs - readMs);
  if (gap < MINUTE) return "Read just now";
  const [size, unit] = gap < HOUR ? [Math.floor(gap / MINUTE), "minute"]
    : gap < DAY ? [Math.floor(gap / HOUR), "hour"] : [Math.floor(gap / DAY), "day"];
  return `Read ${size} ${unit}${size === 1 ? "" : "s"} ago`;
}

/** "1 picture", "3 pictures", or "" for none. A count is all the list gives. */
export function picturesWords(count) {
  const n = Number(count) || 0;
  if (n <= 0) return "";
  return `${n} picture${n === 1 ? "" : "s"}`;
}

/** The first lines of a post's text, and whether there is more to show: {first, full, long}. */
export function splitText(text) {
  const full = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  let first = full.split("\n").slice(0, MOST_LINES).join("\n");
  if (first.length > MOST_CHARS) first = `${first.slice(0, MOST_CHARS - 1).trimEnd()}…`;
  return { first, full, long: first !== full };
}

/** An account as the owner knows it: its name and network, else the network and id. */
export function accountWords(row) {
  const label = String(row.account?.label ?? "").trim();
  const network = String(row.account?.network ?? "").trim();
  if (label) return network ? `${label} (${network})` : label;
  return `${network || "SocialBu account"} ${row.account_id}`.trim();
}

/** Where a post Posts booked is opened: its place in the Queue, else its piece. */
export function ownLink(own) {
  if (!own) return null;
  if (QUEUE_STATES.has(own.state)) return { view: "queue", where: { post: own.post_id }, words: "Open in the Queue" };
  return { view: "drafts", where: { piece: own.piece_id }, words: "Open the piece" };
}

/** One row, as plain strings and flags for the view to draw. */
export function scheduledRow(row, zone) {
  const w = row.when_ms === null || row.when_ms === undefined ? null : wall(row.when_ms, zone);
  return {
    id: String(row.id),
    time: w ? `${pad(w.hour)}:${pad(w.minute)}` : NO_TIME,
    account: accountWords(row),
    text: splitText(row.text),
    pictures: picturesWords(row.pictures),
    own: row.own ? { words: row.own.page_name ? `Booked by Posts for ${row.own.page_name}` : "Booked by Posts", link: ownLink(row.own) } : null,
  };
}

/** "Fri 15 Jan", with the year when it is not this year's. */
function dayWords(ms, zone, nowMs) {
  const w = wall(ms, zone);
  const year = w.year === wall(nowMs, zone).year ? "" : ` ${w.year}`;
  return `${w.weekday} ${w.day} ${MONTHS[w.month - 1]}${year}`;
}

/**
 * The rows grouped by the calendar day they fall on in `zone`, soonest first, each day
 * headed "Today", "Tomorrow" or its date. Rows with no time come last, under their own
 * heading. Returns [{key, label, rows}] where each row is `scheduledRow`'s.
 */
export function groupByDay(rows, { zone, nowMs }) {
  const timed = rows.filter((r) => r.when_ms !== null && r.when_ms !== undefined)
    .sort((a, b) => a.when_ms - b.when_ms || String(a.id).localeCompare(String(b.id)));
  const untimed = rows.filter((r) => r.when_ms === null || r.when_ms === undefined);
  // Noon never skips or repeats a day across a change of clocks.
  const noon = fromInputValue(`${dayKey(nowMs, zone)}T12:00`, zone).ms ?? nowMs;
  const today = dayKey(nowMs, zone);
  const tomorrow = dayKey(noon + DAY, zone);
  const groups = [];
  for (const row of timed) {
    const key = dayKey(row.when_ms, zone);
    let group = groups[groups.length - 1];
    if (!group || group.key !== key) {
      const words = dayWords(row.when_ms, zone, nowMs);
      group = { key, label: key === today ? `Today, ${words}` : key === tomorrow ? `Tomorrow, ${words}` : words, rows: [] };
      groups.push(group);
    }
    group.rows.push(scheduledRow(row, zone));
  }
  if (untimed.length) groups.push({ key: "none", label: NO_TIME, rows: untimed.map((r) => scheduledRow(r, zone)) });
  return groups;
}

/** The sentence for a list cut short, or "". */
export function cutWords({ cut, pages_read: pages, rows }) {
  if (!cut) return "";
  return `This list is cut short. Posts read ${pages} batch${pages === 1 ? "" : "es"} (${rows.length} posts) and SocialBu has more scheduled. To read further, raise "Most batches of SocialBu's schedule to read" under Settings, Apps, Posts.`;
}

/** The sentence for entries SocialBu sent that Posts could not read as posts, or "". */
export function skippedWords(count) {
  const n = Number(count) || 0;
  return n > 0 ? `${n} ${n === 1 ? "entry" : "entries"} in SocialBu's answer could not be read as a post, so ${n === 1 ? "it is" : "they are"} left out.` : "";
}

/** The sentence under the heading when there is nothing to list. */
export function emptyWords(data) {
  if (data.read_ms === null || data.read_ms === undefined) {
    return data.failed ? "" : "The list has not been read yet.";
  }
  return "Nothing is scheduled on SocialBu.";
}
