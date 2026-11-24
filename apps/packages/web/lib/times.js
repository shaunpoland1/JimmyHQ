// Times in words, for the page. Every stored time is unix milliseconds in UTC; the owner's
// time zone is used only here, to show one. Pure: no DOM.

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function plural(n, one) {
  return `${n} ${one}${n === 1 ? "" : "s"}`;
}

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago"; "never" for no time. */
export function ago(thenMs, nowMs) {
  if (thenMs === null || thenMs === undefined) return "never";
  const gap = Math.max(0, nowMs - thenMs);
  if (gap < MINUTE) return "just now";
  if (gap < HOUR) return `${plural(Math.floor(gap / MINUTE), "minute")} ago`;
  if (gap < DAY) return `${plural(Math.floor(gap / HOUR), "hour")} ago`;
  return `${plural(Math.floor(gap / DAY), "day")} ago`;
}

/** "now", "in 20 minutes", "in 3 hours", "in 2 days". */
export function soon(thenMs, nowMs) {
  const gap = thenMs - nowMs;
  if (gap <= 0) return "now";
  if (gap < HOUR) return `in ${plural(Math.max(1, Math.ceil(gap / MINUTE)), "minute")}`;
  if (gap < DAY) return `in ${plural(Math.ceil(gap / HOUR), "hour")}`;
  return `in ${plural(Math.ceil(gap / DAY), "day")}`;
}

/** A date and time as the owner reads it, in their time zone. */
export function when(ms, timeZone) {
  if (!ms) return "";
  const options = { dateStyle: "medium", timeStyle: "short" };
  try {
    return new Intl.DateTimeFormat(undefined, timeZone ? { ...options, timeZone } : options).format(new Date(ms));
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(new Date(ms));
  }
}

/** When a package was last looked at, in words. */
export function checkedLine(row, nowMs, outside, practice = false) {
  if (!outside) {
    if (row.status_by === "hand") return "Set by you";
    return practice ? "Practice: it moves along by itself" : "Not looked up: you set its status";
  }
  if (row.status_by === "hand" && row.archived) return "Marked by you; no longer checked";
  if (row.checked_ms === null || row.checked_ms === undefined) return "Not checked yet";
  return `Checked ${ago(row.checked_ms, nowMs)}`;
}

/** What the schedule will do next, in words. `schedule` is what the program sent. */
export function nextLine(schedule, nowMs) {
  if (!schedule?.outside) {
    if (!schedule?.practice) return "Practice looks nothing up, so nothing is checked.";
    const minutes = Number(schedule.practice_minutes) || 0;
    return `This is Practice: nothing is looked up and no parcel is real. A package you add moves along by itself and is delivered in about ${minutes === 1 ? "1 minute" : `${minutes} minutes`}, with a card on your Bench; one whose status you set yourself is left alone.`;
  }
  const parts = [];
  parts.push(schedule.last_ok_ms ? `Last checked ${ago(schedule.last_ok_ms, nowMs)}.` : "Not checked yet.");
  if (schedule.fails > 0) parts.push(`The last ${schedule.fails === 1 ? "try" : `${schedule.fails} tries`} got no clear answer; the next is ${soon(schedule.next_ms, nowMs)}.`);
  else parts.push(`Next check ${soon(schedule.next_ms, nowMs)}.`);
  return parts.join(" ");
}
