// Note text is untrusted when it is shown: an agent, or a file dropped into
// the Library, can have written it.

/**
 * The address a link in a note may open, or null to show its words without a
 * link. Only web and mail addresses: nothing that runs (javascript:), carries
 * its own content (data:), or points back into this app.
 */
export function safeNoteHref(href: string | null | undefined): string | null {
  if (typeof href !== "string") return null;
  const value = href.trim();
  // Browsers ignore tabs and line breaks inside a scheme, so "java\tscript:" is checked without them.
  const compact = value.replace(/[\u0000- ]+/g, "");
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(compact)?.[1]?.toLowerCase();
  if (scheme === "mailto") return compact.length > "mailto:".length ? compact : null;
  if (scheme !== "http" && scheme !== "https") return null;
  try {
    const url = new URL(compact);
    if (!url.hostname || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

const ID_AT_END = /[ \t]*<!--[ \t]*m:[0-9A-HJKMNP-TV-Z]{26}[ \t]*-->([ \t]*)$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;

/**
 * A note's text without the fact id comments, for reading. Ids inside a code
 * block are part of the code and are left alone.
 */
export function hideFactIds(text: string): string {
  let fence: string | null = null;
  return text
    .split("\n")
    .map((line) => {
      const marks = FENCE.exec(line)?.[1];
      if (marks) {
        if (fence === null) fence = marks;
        else if (marks[0] === fence[0] && marks.length >= fence.length && line.trim() === marks) fence = null;
        return line;
      }
      return fence === null ? line.replace(ID_AT_END, "$1") : line;
    })
    .join("\n");
}

/** True when a note has nothing to read once comments are hidden: only headings and blank lines. */
export function onlyHeadings(text: string): boolean {
  const shown = hideFactIds(text).replace(/<!--[\s\S]*?-->/g, "");
  return shown.split("\n").every((line) => line.trim() === "" || /^#{1,6}\s/.test(line));
}
