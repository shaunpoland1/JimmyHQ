// A question before something that cannot be taken back. The dialog is the page's own,
// and the keyboard starts on Cancel, so Enter or Space pressed too soon does nothing.

/** The dialog's way out: its first button, which is Cancel. */
export function safeButton(doc) {
  return doc?.querySelector?.('[role="dialog"] .row.end button') ?? null;
}

/**
 * Ask before a danger. `ui` is {dialog, document}: the page helper's dialog and the page.
 * Resolves true only when the owner pressed `yes`; Cancel, Esc and a click outside are no.
 */
export async function askSafely(ui, title, intro, yes) {
  const asked = ui.dialog({ title, intro, actions: [{ label: yes, kind: "danger" }] });
  safeButton(ui.document)?.focus();
  return (await asked) === yes;
}

/** What deleting every reply says first: what goes, and what stays. */
export const FORGET_REPLIES = {
  title: "Delete every reply?",
  intro: "Every reply you pasted is deleted for good from the private store, and cannot be brought back. "
    + "The ideas already found stay, and so do the articles started from them, without the replies they rested on.",
  yes: "Delete them",
};

/** What dismissing an idea says first. */
export function dismissIdea(title) {
  return {
    title: "Dismiss this idea?",
    intro: `“${title}” leaves the list and is not proposed again, even if later replies ask for it. This cannot be undone. `
      + "The replies it rested on are kept.",
    yes: "Dismiss it",
  };
}

/** What dropping an article says first. */
export const DROP_ARTICLE = {
  title: "Drop this article?",
  intro: "Nothing more is spent on it and nothing is published. It stays in the list as dropped, and cannot be picked up again.",
  yes: "Drop it",
};
