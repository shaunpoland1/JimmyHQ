// Which articles each view lists, in which order, and the list's search and filters.
// Pure: no DOM, no bridge.

export const HELD = new Set(["held_coverage", "stopped_research", "stalled"]);
export const TO_PUBLISH = new Set(["approved", "publishing", "unsure", "sent"]);
export const DONE = new Set(["published", "declined", "dropped"]);

/** Articles waiting for the owner's decision, the longest waiting first. */
export function toDecide(pieces) {
  return (pieces ?? []).filter((p) => p.state === "ready").sort((a, b) => a.updated - b.updated || a.id - b.id);
}

/** Articles still being worked on or held, oldest first. */
export function inTheWorks(pieces) {
  return (pieces ?? []).filter((p) => p.state !== "ready" && !TO_PUBLISH.has(p.state) && !DONE.has(p.state))
    .sort((a, b) => a.created - b.created || a.id - b.id);
}

/** Approved articles on their way to the site: no answer first, then the rest, oldest first. */
export function toPublish(pieces) {
  const rank = { unsure: 0, approved: 1, publishing: 2, sent: 3 };
  return (pieces ?? []).filter((p) => TO_PUBLISH.has(p.state))
    .sort((a, b) => rank[a.state] - rank[b.state] || a.updated - b.updated || a.id - b.id);
}

/** Published, declined and dropped articles, newest first. */
export function finished(pieces) {
  return (pieces ?? []).filter((p) => DONE.has(p.state)).sort((a, b) => b.updated - a.updated || b.id - a.id);
}

/** The groups the Articles view's list can show, in the order the owner meets them. */
export const GROUPS = [
  ["decide", "To decide"], ["working", "Being worked on"], ["publish", "Approved"], ["done", "Finished"],
];

/** Which group an article is in. */
export function groupOf(piece) {
  if (piece.state === "ready") return "decide";
  if (TO_PUBLISH.has(piece.state)) return "publish";
  if (DONE.has(piece.state)) return "done";
  return "working";
}

/** A short line for an article: its title, else its topic. */
export function headline(piece, most = 110) {
  const text = String(piece?.title || piece?.topic || `Article ${piece?.id ?? ""}`).replace(/\s+/g, " ").trim();
  return text.length > most ? `${text.slice(0, most - 1)}…` : text;
}

/** The site's name by id, or a plain word when the site has gone. */
export function siteName(sites, id) {
  return (sites ?? []).find((s) => s.id === id)?.name ?? "A site you removed";
}

/**
 * Articles that match every word of `query` (any order, any case) in their title, topic,
 * angle or state, and when set, belong to `site` and are in `group`.
 */
export function filterPieces(pieces, { query = "", site = "", group = "" } = {}) {
  const words = String(query).toLowerCase().split(/\s+/).filter(Boolean);
  const siteId = site === "" || site === null || site === undefined ? null : Number(site);
  return (pieces ?? []).filter((p) => {
    if (siteId !== null && p.site_id !== siteId) return false;
    if (group && groupOf(p) !== group) return false;
    const text = [p.title, p.topic, p.angle, p.words].filter((v) => typeof v === "string").join("\n").toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

/** "2 questions open · 1 warning", or "Every check passed". */
export function checksLine(piece) {
  const parts = [];
  if (piece.questions) parts.push(`${piece.questions} question${piece.questions === 1 ? "" : "s"} open`);
  if (piece.warnings) parts.push(`${piece.warnings} warning${piece.warnings === 1 ? "" : "s"}`);
  return parts.length ? parts.join(" · ") : "Every check passed";
}
