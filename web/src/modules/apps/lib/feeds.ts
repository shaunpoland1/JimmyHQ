// Feeds between apps: the plain lines the server writes about who reads what. The server
// writes every sentence (server/jimmyhq/apps/feeds.py: `who_reads_what`, `plan_lines`);
// this only reads them safely, so a missing or odd value shows nothing rather than breaking.

export const FEED_LINES_MOST = 20;
const LINE_MOST = 400;

/** The server's `feeds`: a list of sentences. Anything that is not text is left out. */
export function feedLines(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is string => typeof item === "string" && item.trim() !== "")
    .map((item) => item.trim().slice(0, LINE_MOST))
    .slice(0, FEED_LINES_MOST);
}

/** The heading over an app's feed lines: what it offers, reads, or both. */
export function feedsHeading(lines: readonly string[]): string {
  return lines.length === 1 ? "Who reads what" : `Who reads what (${lines.length})`;
}
