// Pausing and removing a page: which pages take new work, and what the page says
// before one is removed. The app decides whether it may be; this is the wording. Pure.

const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** The pages new work may be started for: every page that is not paused. */
export function activePages(pages) {
  return (pages ?? []).filter((p) => !p.paused);
}

/** What the status line says once a page was paused or resumed. */
export function pausedWords(page) {
  return page.paused
    ? `Paused ${page.name}. It gets no new work until you resume it. What is drafted, queued or booked for it carries on.`
    : `Resumed ${page.name}. It takes new work again.`;
}

/**
 * The question before a page is removed: {title, intro, yes, no}. `found` is the app's
 * `pages.removal`: {name, working, drafts, rules, kept}. The way out never says "Cancel".
 */
export function removalQuestion(found) {
  const { working = 0, drafts = 0, rules = 0, kept = 0 } = found;
  const said = [];
  if (working) {
    said.push(`Its ${plural(working, "open piece")} ${working === 1 ? "is" : "are"} dropped${drafts ? `, and ${plural(drafts, "draft")} among them declined` : ""}.`);
  } else {
    said.push("It has no drafts and nothing being worked on.");
  }
  said.push(rules
    ? `Its ${plural(rules, "rule")} and what it learned from your decisions are deleted. Rules for every brand stay.`
    : "What it learned from your decisions is deleted. Rules for every brand stay.");
  if (kept) said.push(`Its ${plural(kept, "post")} that went out or were dropped stay in their lists, under “A brand you removed”.`);
  said.push("This cannot be undone. To stop new work and keep everything, pause the brand instead.");
  return { title: `Remove ${found.name}?`, intro: said.join(" "), yes: "Remove the brand", no: "Keep the brand" };
}
