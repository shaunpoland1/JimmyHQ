// Topic health in plain words, from the counts Reader keeps. Pure: no DOM.

const DAY = 86_400_000;

/** "today", "yesterday", "3 days ago", "2 weeks ago", "4 months ago". */
export function ago(thenMs, nowMs) {
  if (thenMs === null || thenMs === undefined) return "never";
  const days = Math.max(0, Math.floor((nowMs - thenMs) / DAY));
  if (days < 1) return "today";
  if (days < 2) return "yesterday";
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`;
  return `${Math.floor(days / 30)} months ago`;
}

function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

/** One line of counts: "Found 12 · kept 5 · skipped 3 · last found 3 days ago". */
export function healthLine(health, nowMs) {
  if (!health) return "";
  const parts = [`Found ${health.found}`, `kept ${health.kept}`, `skipped ${health.skipped}`];
  if (health.waiting) parts.push(`${health.waiting} waiting`);
  parts.push(health.last_found_ms ? `last found ${ago(health.last_found_ms, nowMs)}` : "nothing found yet");
  return parts.join(" · ");
}

/**
 * The offer to pause a quiet topic, or null. Only for a topic that is on and has been
 * quiet for the owner's number of days.
 */
export function quietOffer(topic) {
  const health = topic?.health;
  if (!health || topic.paused || !health.quiet) return null;
  return `Quiet for ${plural(health.quiet_days, "day")}. Pause it, or try other phrasings?`;
}

/** A short word on how a topic is doing, from what the owner chose, or null with too few choices to say. */
export function verdict(health, least = 5) {
  if (!health || health.decided < least) return null;
  const share = health.kept / Math.max(1, health.kept + health.skipped);
  if (share >= 0.6) return "Mostly kept";
  if (share <= 0.25) return "Mostly skipped";
  return "Mixed";
}

/** Who a topic is for, in words. */
export function forWhom(topic) {
  const said = String(topic?.audience ?? "").trim();
  return said ? `For: ${said}` : "For you";
}

export const OUTCOME_WORDS = {
  list: "Kept in the list",
  bench: "On your Bench to read",
  memory: "Mined into Memory",
  offer: "Offered to other apps",
};

/** What happens to a topic's articles, in words. */
export function outcomesLine(outcomes) {
  const known = (outcomes ?? []).filter((o) => Object.hasOwn(OUTCOME_WORDS, o));
  return (known.length ? known : ["list"]).map((o) => OUTCOME_WORDS[o]).join(", ");
}
