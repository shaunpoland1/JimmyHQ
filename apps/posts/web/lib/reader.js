// From Reader: what Today and New say about the articles Reader offers. Pure.

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** "Posts last heard from Reader 3 hours ago." for the time the feed was last read. */
export function heardLine(heardAt, nowMs) {
  if (heardAt === null || heardAt === undefined) return "Posts has not heard from Reader yet.";
  const gone = Math.max(0, (nowMs ?? heardAt) - heardAt);
  const words = gone < HOUR ? "less than an hour ago"
    : gone < DAY ? `${Math.floor(gone / HOUR)} hour${Math.floor(gone / HOUR) === 1 ? "" : "s"} ago`
      : `${Math.floor(gone / DAY)} day${Math.floor(gone / DAY) === 1 ? "" : "s"} ago`;
  return `Posts last heard from Reader ${words}.`;
}

/**
 * The loud group for Today when Reader's articles cannot reach Posts: the sentence JimmyHQ
 * gave, what to do, and when Posts last heard from Reader. Null when nothing is wrong.
 */
export function readerTrouble(data) {
  const reader = data?.reader;
  if (!reader?.loud) return null;
  return {
    key: "reader",
    title: "Reader's articles are not reaching Posts",
    items: [{ text: reader.said || "Posts could not read the articles Reader offers.",
      detail: `${reader.todo} ${heardLine(reader.heard_at, data?.now_ms)}`.trim(), go: { view: "new" } }],
  };
}

/** Today's line for ideas from Reader waiting for Start or Dismiss, or null. */
export function ideasWaiting(data) {
  const ideas = data?.ideas ?? [];
  if (!ideas.length) return null;
  return {
    key: "ideas",
    title: `${ideas.length} idea${ideas.length === 1 ? "" : "s"} from Reader`,
    items: ideas.slice(0, 5).map((idea) => ({ text: idea.title || idea.url, detail: idea.topic ? `Topic: ${idea.topic}` : "", go: { view: "new" } })),
  };
}

/** Why an idea waits for the owner although Posts is set to start pieces by itself, or "". */
export function waitsForBrandLine(data) {
  return data?.reader?.waits_for_brand ? "You have more than one brand, so choose which one this is for." : "";
}
