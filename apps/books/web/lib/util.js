// Small pure helpers for the Books page. No DOM. Money is never worked out here: the
// program reads what is typed and sends every amount already written out.

/** The sentence to show for a refusal: the one written for the owner when there is one. */
export function reason(error) {
  return String((error && (error.owner_message || error.message)) || "That did not work, so nothing was changed. Try again.");
}

/** A YYYY-MM-DD date moved by `days` (the + and - keys in a date box). */
export function shiftDay(iso, days) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso ?? ""));
  if (!m) return iso;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + days));
  return d.toISOString().slice(0, 10);
}

/** A payee's comparison form, the program's own rule: lower case, letters and digits. */
export function payeeKey(name) {
  return String(name ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/** The payee used before that `typed` names, or null. */
export function knownPayee(payees, typed) {
  const key = payeeKey(typed);
  if (!key) return null;
  return (payees ?? []).find((p) => payeeKey(p.name) === key) ?? null;
}

/** A long text in pieces of at most `size` characters, for handing a file over. */
export function pieces(text, size) {
  const out = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out.length ? out : [""];
}

export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "Fri 15 Jan" for a YYYY-MM-DD date, with the year when it is not `thisYear`. */
export function dayWords(iso, thisYear) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso ?? ""));
  if (!m) return String(iso ?? "");
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  const words = `${DAYS[d.getUTCDay()]} ${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}`;
  return m[1] === String(thisYear ?? m[1]) ? words : `${words} ${m[1]}`;
}

/** Whole days from `fromIso` to `toIso`. */
export function daysBetween(fromIso, toIso) {
  const a = Date.parse(`${fromIso}T00:00:00Z`);
  const b = Date.parse(`${toIso}T00:00:00Z`);
  return Math.round((b - a) / 86_400_000);
}

/** When a reminder is due, in words, from today. */
export function dueWords(due, today) {
  const gap = daysBetween(today, due);
  if (gap === 0) return "Due today";
  if (gap === 1) return "Due tomorrow";
  if (gap < 0) return `${plural(-gap, "day")} overdue`;
  return `In ${plural(gap, "day")}`;
}

/** A time in unix milliseconds as a short local date and time. */
export function when(ms, timeZone) {
  if (!ms) return "";
  const options = { dateStyle: "medium", timeStyle: "short" };
  try {
    return new Intl.DateTimeFormat(undefined, timeZone ? { ...options, timeZone } : options).format(new Date(ms));
  } catch {
    return new Intl.DateTimeFormat(undefined, options).format(new Date(ms));
  }
}

const LONG_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2028-09" as "September 2028"; anything else comes back as it was. */
export function monthWords(ym) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(ym ?? ""));
  if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return String(ym ?? "");
  return `${LONG_MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

/** The kinds of account that are a bank's, where a bill is normally paid from. */
const BANK_KINDS = ["checking", "savings"];

/**
 * The account a new bill starts on: the one the owner used for the bill they added most
 * recently (the highest id), else the first checking or savings account, else the first open
 * account. `accounts` are the open ones, in the order the owner keeps them; `reminders` are
 * all of them, done ones too. Null when there is no open account.
 */
export function billAccount(accounts, reminders) {
  const open = accounts ?? [];
  const usable = new Set(open.map((a) => a.id));
  let last = null;
  for (const r of reminders ?? []) {
    if (r.kind !== "bill" || r.paid_other || !usable.has(r.account)) continue;
    if (!last || r.id > last.id) last = r;
  }
  if (last) return open.find((a) => a.id === last.account) ?? null;
  return open.find((a) => BANK_KINDS.includes(a.kind)) ?? open[0] ?? null;
}
