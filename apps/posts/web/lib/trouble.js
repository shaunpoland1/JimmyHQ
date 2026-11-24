// What waits on the owner beyond a draft: a post in trouble with its scheduler, and agent
// work that is stuck or out of budget. The server says what happened and which actions
// resolve it; this says how each reads on the page. Pure: no DOM, no bridge.

/** The chip for a post's trouble with its scheduler, or null. */
export function troubleChip(trouble, schedulerLabel = "the scheduler") {
  if (!trouble) return null;
  const words = {
    failed: `${schedulerLabel} could not publish it`,
    missing: `Gone from ${schedulerLabel}`,
    lost: `Not found on ${schedulerLabel} after its time`,
    no_answer: `No answer from ${schedulerLabel}`,
    partial: "Only on some accounts",
    out_of_step: "Out of step with the scheduler",
  };
  return { text: words[trouble.kind] ?? "Needs you", kind: trouble.kind === "out_of_step" ? "warn" : "ask" };
}

/** The buttons for a post's trouble, in the server's order: [{call, label, kind, ask}]. */
export function troubleButtons(trouble) {
  return (trouble?.actions ?? []).map((a) => ({ call: String(a.call), label: String(a.label), kind: a.kind ? String(a.kind) : "", ask: a.ask ? String(a.ask) : "" }));
}

/** The question asked before one of those buttons acts: the server's words, with the tool's name. */
export function wayQuestion(way, schedulerLabel = "the scheduler") {
  const words = way?.ask || `Look on ${schedulerLabel} first.`;
  return words.split("{tool}").join(schedulerLabel);
}

/**
 * The class of one of a post's trouble buttons: the server's own kind, except that
 * "quiet" is never the first choice; otherwise the first button is the primary one.
 */
export function buttonKind(way, first) {
  if (way?.kind === "quiet") return "";
  return way?.kind || (first ? "primary" : "");
}

/** The chip for a piece whose agent work waits on the owner, or null. */
export function noticeChip(notice) {
  if (!notice) return null;
  const role = notice.role ? `the ${notice.role}` : "an agent";
  return { text: `Waiting for you: ${role}`, kind: "warn" };
}

/** The buttons for agent work that waits on the owner: [{action, label, kind}]. */
export function noticeButtons(notice) {
  return (notice?.actions ?? []).map((a) => ({ action: String(a.id), label: String(a.label), kind: a.id === "drop" ? "danger" : a.id === "wait" ? "primary" : "" }));
}

/** "Did this happen?" questions come first: only the owner can answer them. */
export function askChips(asks) {
  const list = asks ?? [];
  const lived = list.filter((a) => a.check === "lived").length;
  const other = list.length - lived;
  const out = [];
  if (lived) out.push({ text: lived === 1 ? "Asks if it happened" : `Asks if ${lived} things happened`, kind: "ask" });
  if (other) out.push({ text: `${other} question${other === 1 ? "" : "s"} open`, kind: "ask" });
  return out;
}

/**
 * The question before a post is taken out of the queue: {title, intro, yes, no, done}.
 * The way out (`no`) keeps the post and never says "Cancel", which here would read as
 * the opposite; `done` is what the status line says afterwards, matching `yes`.
 */
export function cancelQuestion(post, schedulerLabel = "the scheduler") {
  if (["failed", "missing"].includes(post.state)) {
    return { title: "Decline this post?", yes: "Decline it", no: "Keep the post", done: "Declined. It is under Dropped.",
      intro: `Nothing is sent to ${schedulerLabel}; anything still there for it is yours to remove there. It moves to Dropped.` };
  }
  const intro = post.state === "booked" ? `It is taken off ${schedulerLabel} first; if that does not work it stays booked.`
    : "It will not be booked. It moves to Dropped.";
  return { title: "Cancel this post?", yes: "Cancel the post", no: "Keep the post", done: "Cancelled. It is under Dropped.", intro };
}
