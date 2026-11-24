// A file or note an agent wrote is untrusted text. Links are kept only when they are web
// or mail addresses, so nothing the text says can run or point back into this app.

/** The address a link may open, or null to show its words without a link. */
export function safeHref(href: string | null | undefined): string | null {
  if (typeof href !== "string") return null;
  // Browsers ignore tabs and line breaks inside a scheme, so check without them.
  const compact = href.trim().replace(/[\u0000- ]+/g, "");
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
