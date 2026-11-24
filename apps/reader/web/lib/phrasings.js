// The phrasings box: merging suggested lines into what the owner already wrote. Pure: no DOM.
import { lines } from "./text.js";

/** The existing lines first, untouched, then each suggestion not already there (ignoring case and
 * extra spaces), up to `most` lines in all. Returns the new box text and how many were added. */
export function mergePhrasings(existing, suggested, most = 8) {
  const have = lines(existing);
  const seen = new Set(have.map(fold));
  const added = [];
  for (const raw of Array.isArray(suggested) ? suggested : []) {
    const text = String(raw ?? "").replace(/\s+/g, " ").trim();
    if (!text || seen.has(fold(text)) || have.length + added.length >= most) continue;
    seen.add(fold(text));
    added.push(text);
  }
  return { text: [...have, ...added].join("\n"), added: added.length };
}

function fold(text) {
  return String(text).replace(/\s+/g, " ").trim().toLowerCase();
}
