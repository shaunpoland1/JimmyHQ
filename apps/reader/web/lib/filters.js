// The Articles view's filters: by topic, by what happened to an article, and by words.
// Pure: no DOM.

export const WHAT = [
  ["all", "Everything suggested"],
  ["waiting", "Waiting in Triage"],
  ["kept", "Kept"],
  ["later", "Read later"],
  ["skipped", "Skipped"],
  ["sent", "Sent on"],
  ["mined", "Mined into Memory"],
  ["offered", "Offered to other apps"],
  ["passed", "Not suggested"],
];

const SUGGESTED = new Set(["new", "kept", "later", "skipped", "sent"]);
const STATE_OF = { waiting: "new", kept: "kept", later: "later", skipped: "skipped", sent: "sent" };

/** Words for an article's state, as the owner reads them. */
export function stateWords(article) {
  return {
    new: "Waiting", kept: "Kept", later: "Read later", skipped: "Skipped", sent: "Sent on",
    dropped: "Dropped", passed: "Not picked", read: "Read",
  }[article?.state] ?? String(article?.state ?? "");
}

/** Whether `article` passes the `what` filter. */
export function matchesWhat(article, what) {
  if (!what || what === "all") return SUGGESTED.has(article.state);
  if (what === "passed") return article.state === "passed" || article.state === "dropped";
  if (what === "mined") return Number(article.mined) > 0;
  if (what === "offered") return Boolean(article.offered);
  return article.state === STATE_OF[what];
}

/** Lower-case words with accents set aside, so "Café" finds "cafe". */
export function fold(text) {
  return String(text ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Whether every word of `query` is somewhere in the article's title, source, why, topic or excerpt. */
export function matchesQuery(article, query) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = fold([article.title, article.host, article.why, article.topic, article.excerpt, article.url].join(" "));
  return words.every((w) => hay.includes(w));
}

/**
 * The articles that pass every filter, in the order given.
 * `filters` is {topic: id or "" for any, what, query}.
 */
export function filterArticles(articles, filters = {}) {
  const topic = filters.topic === "" || filters.topic === undefined || filters.topic === null ? null : Number(filters.topic);
  return (articles ?? []).filter((a) =>
    (topic === null || a.topic_id === topic) && matchesWhat(a, filters.what) && matchesQuery(a, filters.query));
}

/** How many articles each `what` filter would show, for the counts beside them. */
export function countsByWhat(articles, topic = "") {
  const out = {};
  for (const [id] of WHAT) out[id] = filterArticles(articles, { topic, what: id }).length;
  return out;
}
