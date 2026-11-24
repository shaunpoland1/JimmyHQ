// Card text is written by agents and outside tools, so it is only ever shown
// as plain text. The one thing recognised in it is an http or https address.

export type TextPart = { type: "text"; value: string } | { type: "link"; value: string; href: string };

/** Paragraphs split on blank lines. Line breaks inside a paragraph are kept. */
export function splitParagraphs(text: string): string[] {
  return text
    .replace(/\r\n?/g, "\n")
    .split(/\n[ \t]*\n+/)
    .map((paragraph) => paragraph.replace(/^\n+|\s+$/g, ""))
    .filter((paragraph) => paragraph.trim() !== "");
}

const URL_PATTERN = /https?:\/\/[^\s<>"'`]+/gi;
const TRAILING = /[.,;:!?'"”’»]+$/;

/** The address a link may open, or null unless it is a well-formed http or https URL. */
export function safeHref(candidate: string): string | null {
  try {
    const url = new URL(candidate);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (!url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

function trimUrl(raw: string): string {
  let url = raw;
  for (;;) {
    const before = url;
    url = url.replace(TRAILING, "");
    // A closing bracket belongs to the sentence unless the address opened one.
    for (const [open, close] of [["(", ")"], ["[", "]"], ["{", "}"]] as const) {
      if (url.endsWith(close) && url.split(close).length > url.split(open).length) url = url.slice(0, -1);
    }
    if (url === before) return url;
  }
}

/** Splits text into plain runs and http(s) links. Nothing else is ever a link. */
export function linkify(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let cursor = 0;
  const push = (value: string) => {
    if (!value) return;
    const last = parts[parts.length - 1];
    if (last && last.type === "text") last.value += value;
    else parts.push({ type: "text", value });
  };
  for (const match of text.matchAll(URL_PATTERN)) {
    const start = match.index;
    const url = trimUrl(match[0]);
    const href = safeHref(url);
    // Only at a word boundary: "xhttp://..." is not an address.
    const boundary = start === 0 || !/[A-Za-z0-9]/.test(text[start - 1] ?? "");
    if (!href || !boundary) continue;
    push(text.slice(cursor, start));
    parts.push({ type: "link", value: url, href });
    cursor = start + url.length;
  }
  push(text.slice(cursor));
  return parts;
}
