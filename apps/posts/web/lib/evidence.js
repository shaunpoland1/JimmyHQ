// Evidence on a draft: which source sentence each number and quotation rests on.
//
// The checks (posts/checks/numbers.py and quotes.py) keep one result for every number
// and every quotation, keyed the same way as here, with the sentence numbers it was
// found in. This file finds the same numbers and quotations in the text the page shows
// and pairs each with its result, so a click can light up the sentence it rests on.
// Pure: no DOM, no bridge.

// The same pattern as posts/checks/numbers.py `_NUMBER`.
const NUMBER = /(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\.\d)(\s*(?:%|percent\b|per cent\b))?(?:\s*(thousand|million|billion|trillion|bn|k)\b)?/gi;
// The same pattern as posts/checks/quotes.py `_QUOTE`, and its three-word minimum.
const QUOTE = /“([^”]+)”|"([^"]+)"/g;
const MIN_QUOTE_WORDS = 3;
const EDGE_PUNCT = /[\s.,;:!?\-–—]+$|^[\s.,;:!?\-–—]+/g;
const KEY_CHARS = 80;

/** Text compared as the quote check compares it: quotation marks and apostrophes of any shape, runs of spaces and case ignored. */
export function normalise(text) {
  return String(text)
    .replace(/‘/g, "'").replace(/’/g, "'").replace(/“/g, '"').replace(/”/g, '"')
    .replace(/\s+/g, " ").trim().toLowerCase();
}

/** The key the quote check keeps a quotation under. */
export function quoteKey(span) {
  return normalise(span).replace(EDGE_PUNCT, "").slice(0, KEY_CHARS);
}

/** The key the number check keeps a number under. */
export function numberKey(written) {
  return String(written).trim().toLowerCase();
}

/** Every quotation of three words or more, and every number outside one, in order: {start, end, kind, key}. */
export function marks(text) {
  const found = [];
  for (const m of String(text).matchAll(QUOTE)) {
    const span = (m[1] ?? m[2] ?? "").trim();
    if (span.split(/\s+/).filter(Boolean).length < MIN_QUOTE_WORDS) continue;
    found.push({ start: m.index, end: m.index + m[0].length, kind: "quote", key: quoteKey(span) });
  }
  const inQuote = (i) => found.some((q) => i >= q.start && i < q.end);
  for (const m of String(text).matchAll(NUMBER)) {
    const written = m[0].trimEnd();
    if (inQuote(m.index)) continue;
    found.push({ start: m.index, end: m.index + written.length, kind: "number", key: numberKey(written) });
  }
  return found.sort((a, b) => a.start - b.start);
}

/** What a check result says about one number or quotation, in one word the page styles by. */
export function standing(result) {
  if (!result) return "unchecked";
  if (result.level === "ask") return result.answer ? "answered" : "not_found";
  if (result.level === "warn") return "uncited";
  const evidence = result.evidence ?? [];
  if (evidence.some((e) => Number.isInteger(e))) return "found";
  return evidence.includes("notes") ? "notes" : "found";
}

/** The sentence numbers a result names (never "notes" or "post:3"). */
export function sentencesOf(result) {
  return (result?.evidence ?? []).filter((e) => Number.isInteger(e));
}

/**
 * The text cut into plain runs and marked runs. A marked run carries its kind, the
 * sentences it rests on, its standing and the check result itself (for the question,
 * and Yes / No / Fixed). Joined back together the runs are the text exactly.
 */
export function segments(text, checks) {
  const body = String(text ?? "");
  const byKey = new Map();
  for (const result of checks ?? []) {
    const kind = result.name === "numbers" ? "number" : result.name === "quotes" ? "quote" : null;
    if (kind && !byKey.has(`${kind}:${result.key}`)) byKey.set(`${kind}:${result.key}`, result);
  }
  const out = [];
  let at = 0;
  for (const mark of marks(body)) {
    if (mark.start < at) continue;
    if (mark.start > at) out.push({ kind: "text", text: body.slice(at, mark.start) });
    const result = byKey.get(`${mark.kind}:${mark.key}`) ?? null;
    out.push({
      kind: mark.kind,
      text: body.slice(mark.start, mark.end),
      sentences: sentencesOf(result),
      standing: standing(result),
      check: result,
    });
    at = mark.end;
  }
  if (at < body.length) out.push({ kind: "text", text: body.slice(at) });
  return out;
}

/** The claims that cite a sentence, for the source side: sentence number -> claim texts. */
export function claimsBySentence(claims) {
  const out = new Map();
  for (const claim of claims ?? []) {
    for (const n of claim?.sentences ?? []) {
      if (!Number.isInteger(n)) continue;
      if (!out.has(n)) out.set(n, []);
      out.get(n).push(String(claim.text ?? ""));
    }
  }
  return out;
}

/** The sentence numbers that rest on anything in the draft: lit faintly before any click. */
export function citedSentences(claims, checks) {
  const cited = new Set();
  for (const claim of claims ?? []) for (const n of claim?.sentences ?? []) if (Number.isInteger(n)) cited.add(n);
  for (const result of checks ?? []) if (result.level === "ok") for (const n of sentencesOf(result)) cited.add(n);
  return cited;
}

/** "2 questions open, 1 warning." in the words of posts/checked.py `summary`. */
export function checkSummary(checks) {
  const asks = (checks ?? []).filter((c) => c.level === "ask" && !c.answer).length;
  const warns = (checks ?? []).filter((c) => c.level === "warn").length;
  const parts = [];
  if (asks) parts.push(`${asks} question${asks === 1 ? "" : "s"} open`);
  if (warns) parts.push(`${warns} warning${warns === 1 ? "" : "s"}`);
  if (!parts.length) return "Every check passed.";
  const said = parts.join(", ");
  return `${said[0].toUpperCase()}${said.slice(1)}.`;
}

/** The checks to show, questions first, then warnings; the passes are counted, not listed. */
export function raised(checks) {
  const order = { ask: 0, warn: 1 };
  const list = (checks ?? []).filter((c) => c.level !== "ok");
  return list.sort((a, b) => order[a.level] - order[b.level] || a.id - b.id);
}

export const STANDING_WORDS = {
  found: "Found in the source",
  notes: "Found in your notes",
  uncited: "In the source, but no claim cites that sentence",
  not_found: "Not found in the source or your notes",
  answered: "Not found; you answered",
  unchecked: "Not checked on this version",
};
