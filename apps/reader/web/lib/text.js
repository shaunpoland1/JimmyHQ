// Text from articles, as text. Pure: no DOM.

/** Any value as a string to put in a text node; null and undefined are "". */
export function asText(value) {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

/** At most `most` characters, ending with "…" when cut, never in the middle of a pair of surrogates. */
export function clip(value, most) {
  const text = asText(value);
  const chars = Array.from(text);
  if (chars.length <= most) return text;
  return `${chars.slice(0, Math.max(0, most - 1)).join("").trimEnd()}…`;
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

/** The lines of a phrasings box: trimmed, blank ones left out. */
export function lines(value) {
  return asText(value).replace(/\r\n?/g, "\n").split("\n").map((l) => l.trim()).filter(Boolean);
}
