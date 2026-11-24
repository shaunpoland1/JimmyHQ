// Which posts and pieces each view lists, in which order, and the search and page
// filters of Went out and Dropped. Pure: no DOM, no bridge.

export const QUEUE_STATES = new Set(["queued", "booking", "booked", "unsure", "missing", "failed"]);
export const STUCK_PIECES = new Set(["stalled", "stopped_source", "held_overlap"]);
const CLOSED_PIECES = new Set(["closed", "dropped"]);

/** Drafts waiting for the owner's decision, the longest waiting first. */
export function draftsToDecide(posts) {
  return posts
    .filter((p) => p.state === "draft" && p.piece_state === "ready")
    .sort((a, b) => a.updated - b.updated || a.id - b.id);
}

/** Pieces still being worked on (or stuck), with no draft waiting for the owner. */
export function inTheWorks(pieces, posts) {
  const waiting = new Set(draftsToDecide(posts).map((p) => p.piece_id));
  return pieces
    .filter((p) => !CLOSED_PIECES.has(p.state) && p.state !== "ready" && !waiting.has(p.id))
    .sort((a, b) => a.created - b.created || a.id - b.id);
}

/** Queued and booked posts in slot order; those with no time yet last. */
export function queue(posts) {
  return posts
    .filter((p) => QUEUE_STATES.has(p.state))
    .sort((a, b) => (a.slot_ms ?? Infinity) - (b.slot_ms ?? Infinity) || a.id - b.id);
}

/** Whether a post was booked on Practice: nothing was ever sent for it. */
export function isPracticePost(post) {
  return post?.scheduler === "practice";
}

/** Posts that went out, newest first. */
export function wentOut(posts) {
  return posts
    .filter((p) => p.state === "went_out")
    .sort((a, b) => (b.slot_ms ?? b.updated) - (a.slot_ms ?? a.updated) || b.id - a.id);
}

/**
 * What was dropped or declined, newest first, as one kind of row: a declined post, or
 * a dropped piece that never had a post. {kind, id, piece_id, page_id, title, text, note, when}
 */
export function dropped(posts, pieces) {
  const rows = posts
    .filter((p) => p.state === "declined")
    .map((p) => ({ kind: "post", id: p.id, piece_id: p.piece_id, page_id: p.page_id, title: p.title || p.source_title || p.topic || "",
      text: p.body, note: p.note || "", when: p.updated }));
  for (const piece of pieces) {
    if (piece.state === "dropped" && !piece.post_id) {
      rows.push({ kind: "piece", id: piece.id, piece_id: piece.id, page_id: piece.page_id, title: piece.source_title || piece.topic || piece.source_url || "",
        text: piece.source_url || piece.topic || "", note: piece.error || "", when: piece.updated });
    }
  }
  return rows.sort((a, b) => b.when - a.when || b.id - a.id);
}

/** The words a search looks in, for a post or a dropped row. */
function haystack(row) {
  return [row.title, row.body, row.text, row.note, row.source_title, row.source_url, row.topic]
    .filter((v) => typeof v === "string").join("\n").toLowerCase();
}

/**
 * Rows that match every word of `query` (in any order, any case) and, when `page` is
 * set, belong to that page. An empty query keeps every row of the page.
 */
export function filterRows(rows, { query = "", page = "" } = {}) {
  const words = String(query).toLowerCase().split(/\s+/).filter(Boolean);
  const pageId = page === "" || page === null || page === undefined ? null : Number(page);
  return rows.filter((row) => {
    if (pageId !== null && row.page_id !== pageId) return false;
    const text = haystack(row);
    return words.every((w) => text.includes(w));
  });
}

/** A short line for a post in a list: its title, else the start of its text. */
export function headline(post, most = 90) {
  const title = String(post.title || "").trim();
  if (title) return title;
  const text = String(post.body || post.text || post.topic || post.source_url || "").replace(/\s+/g, " ").trim();
  return text.length > most ? `${text.slice(0, most - 1)}…` : text;
}

/** The page's name by id, or a plain word when the page has gone. */
export function pageName(pages, id) {
  return pages.find((p) => p.id === id)?.name ?? "A brand you removed";
}
