// An article already sent to its site: editing it, and sending the edit. Pure.

/** Whether the open article can be edited and its edit sent. */
export function canEditSent(d) {
  return Boolean(d && d.piece?.state === "sent" && d.revision);
}

/** What the editor says and offers, by where the site publishes. */
export function editSentWords(site) {
  const kept = "Your text is kept as a new version, and every check runs again on it.";
  if (site?.publisher === "wordpress") {
    return {
      button: "Edit and send again", title: "Edit the sent article", label: "Save and send",
      intro: `${kept} Articles then puts it into the draft on your WordPress site, once, and only while it is still a draft there. `
        + "The page tells you whether your site was changed.",
    };
  }
  return {
    button: "Edit", title: "Edit the sent article", label: "Save",
    intro: `${kept} Nothing is sent: this site's way of publishing has nothing to update, so copy the new text to your site by hand if you want it there.`,
  };
}

/**
 * How to show the app's answer to an edit: "ok" only when a new attempt says the site
 * took it; otherwise "warn", since the site was not changed, or may not have been.
 */
export function editSentTone(before, after) {
  const known = new Set((before ?? []).map((a) => a.id));
  const fresh = (after ?? []).filter((a) => !known.has(a.id));
  return fresh.length && fresh.every((a) => a.outcome === "sent") ? "ok" : "warn";
}

/** Whether an edit sent after the article got no answer, so the owner must look on the site. */
export function editUnanswered(piece) {
  return piece?.state === "sent" && piece.attempt?.outcome === "unsure";
}

export const EDIT_UNANSWERED = "An edit sent after this article got no answer, so it may or may not be in the draft on your site. "
  + "Look there first. No other edit is sent until you say it is not there.";
