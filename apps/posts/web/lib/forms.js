// Turning what the owner typed into the shapes the app takes, and back. The app checks
// everything again and refuses with a sentence; this only reads the fields. Pure.

import { checkAccounts } from "./accounts.js";

/** "08:00, 17:30" -> ["08:00", "17:30"]. */
export function parseHours(text) {
  return String(text ?? "").split(/[\s,;]+/).map((h) => h.trim()).filter(Boolean);
}

/** "#garden #local" -> ["#garden", "#local"]. */
export function parseTags(text) {
  return String(text ?? "").split(/\s+/).map((t) => t.trim()).filter(Boolean);
}

/**
 * The page as the app's `pages.save` takes it, from the form's fields. `fields.accounts`
 * is the account rows; `offered` the schedulers the app offers. `fields.book_through` is the
 * tool the page chose, "" for Posts' setting.
 */
export function pageFromForm(fields, id, offered = []) {
  const parsed = checkAccounts(fields.accounts ?? [], offered);
  if (parsed.error) return { error: parsed.error };
  const page = {
    name: String(fields.name ?? "").trim(),
    register: String(fields.register ?? "").trim(),
    samples: String(fields.samples ?? "").replace(/\r\n?/g, "\n"),
    timezone: String(fields.timezone ?? "").trim(),
    hours: parseHours(fields.hours),
    accounts: parsed.accounts,
    tags: parseTags(fields.tags),
    book_through: String(fields.book_through ?? "").trim(),
  };
  if (id !== undefined && id !== null && id !== "") page.id = Number(id);
  return { page };
}

/** A decline reason as the app reads it: the chosen reason first, then the owner's note. */
export function declineReason(reason, note) {
  const r = String(reason ?? "").trim();
  const n = String(note ?? "").trim();
  if (!r) return n;
  return n ? `${r}: ${n}` : r;
}
