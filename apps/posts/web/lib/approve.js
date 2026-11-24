// What the page asks before a draft is approved with the "a" key: one key press must
// not book a post unseen, so the question names what approving does and when. Pure.

const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

/**
 * The question for approving one draft: {title, intro, yes}. `preview` is the app's
 * `post.approval` ({slot_ms, books, scheduler, note}); `when` is its time as the page
 * shows times, or "" when it has none; `open` is how many questions are not answered.
 */
export function approvalQuestion({ open = 0, preview = {}, when = "" } = {}) {
  let what;
  if (preview.books && when) what = `It is booked through ${preview.scheduler || "the scheduler"} for ${when} as soon as you approve.`;
  else if (preview.note) what = `It joins the queue, and nothing is booked. ${preview.note}`;
  else if (when) what = `It joins the queue with ${when} as its time. Nothing is booked until you book it there.`;
  else what = "No free posting time was found, so it joins the queue with no time. Choose a time there, then book it.";
  const asks = open ? ` ${plural(open, "question")} on this draft ${open === 1 ? "is" : "are"} not answered; the post keeps the warning.` : "";
  return { title: "Approve this draft?", intro: `${what}${asks}`, yes: open ? "Approve anyway" : "Approve" };
}
