// What the status line says after a choice, and what it offers next, when the article
// is or was offered to other apps. Pure: no DOM.

const DONE = { keep: "Kept.", later: "Saved to read later.", skip: "Skipped.", send: "Sent on to other apps.", mine: "Choosing facts for Memory." };

/**
 * After `action` on an article: {words, undo}. `change` is what the program said became
 * of its offer: "withdrawn", "stays" or "". `undo` is the label of the button that takes
 * the offer back, or "" when there is nothing to take back.
 */
export function afterChoice(action, change = "") {
  const words = DONE[action] ?? "Done.";
  if (action === "send") return { words, undo: "Undo" };
  if (change === "withdrawn") return { words: `${words} It is no longer offered to other apps.`, undo: "" };
  if (change === "stays") return { words: `${words} It stays offered to other apps, as its topic says.`, undo: "Stop offering it" };
  return { words, undo: "" };
}

/** What to say once an offer was taken back. `row` is what offer.withdraw answered. */
export function afterTakenBack(row) {
  return row?.back_in_triage
    ? "Taken back. It is no longer offered, and it is waiting in Triage again."
    : "Taken back. It is no longer offered to other apps.";
}
