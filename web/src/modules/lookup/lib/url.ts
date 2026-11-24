// Addresses that came from a search result, a page or a model: never trusted.
// No browser or React in here.

/**
 * The address in its clean form when it is an ordinary web address (http or
 * https, a host, no user name or password), otherwise null. Only an address
 * this accepts is ever made into a link.
 */
export function safeWebUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!text || text.length > 4000 || /[\s\\\u0000-\u001f\u007f]/.test(text)) return null;
  let parsed: URL;
  try {
    parsed = new URL(text);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (!parsed.hostname) return null;
  if (parsed.username !== "" || parsed.password !== "") return null;
  return parsed.href;
}

/** The host of a web address (without a leading "www."), or "" when it is not one. */
export function hostOf(raw: unknown): string {
  const safe = safeWebUrl(raw);
  if (!safe) return "";
  const host = new URL(safe).hostname.toLowerCase();
  return host.startsWith("www.") ? host.slice(4) : host;
}

/**
 * A short, readable form of an address: the host and the start of the path,
 * with no scheme, no query and no fragment, cut with an ellipsis when long.
 * "https://example.com/a/b?x=1" is "example.com/a/b".
 */
export function shortAddress(raw: unknown, max = 48): string {
  const safe = safeWebUrl(raw);
  if (!safe) return typeof raw === "string" ? clip(raw.trim(), max) : "";
  const parsed = new URL(safe);
  const path = parsed.pathname === "/" ? "" : parsed.pathname;
  return clip(`${hostOf(safe)}${path}`, max);
}

function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;
}
