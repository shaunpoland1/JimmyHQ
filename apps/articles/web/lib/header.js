// The header image of an article: when the page shows it, and what it says about one.
// Pure: no DOM, no bridge.

/** The states an article may have a header image made in (as the app's own rule). */
export const HEADER_STATES = new Set(["ready", "approved", "sent"]);

/** Whether the article's page shows the header image at all: only when the owner switched it on. */
export function showsHeader(data, piece) {
  return Boolean(data?.header_images) && HEADER_STATES.has(piece?.state);
}

export const NO_HEADER = "No header image has been made for this article.";
export const PRACTICE_HEADER = "This is a Practice placeholder: a plain picture made on this computer so you can try the steps. "
  + "It is never sent to your site.";

/** Whether the picture is a placeholder that never leaves this computer. */
export function isPlaceholder(header) {
  return Boolean(header) && !header.sent_with_draft;
}

/** One plain sentence on where the picture goes, or that there is none. */
export function headerSaid(header, piece, site) {
  if (!header) return NO_HEADER;
  if (isPlaceholder(header)) return PRACTICE_HEADER;
  if (site?.publisher !== "wordpress") return "It is kept here only: this site's way of publishing sends no picture.";
  if (piece?.state === "sent") {
    return "The article is already sent. A picture made before that went with the draft; one made since is kept here only and is not added to the draft.";
  }
  return "When the article is sent to WordPress, this picture is uploaded with it as the draft's featured image.";
}

/** The words on the button. */
export function makeWords(header) {
  return header ? "Make another header image" : "Make a header image";
}

/** What the page says once one is made. */
export function madeSaid(header) {
  return isPlaceholder(header) ? "A Practice placeholder was made. It is never sent to your site." : "A header image was made.";
}
