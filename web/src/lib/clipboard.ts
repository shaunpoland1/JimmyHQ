// Copying text for the owner. The clipboard API is the way: it works from a real click on a
// page served over https or from localhost. It refuses when the page is not a secure context
// (`navigator.clipboard` is then missing, such as a plain-http address on the local network),
// when the document does not have the focus (a headless or automated browser) and when the
// owner has blocked it; in those cases the old way, selecting text in a hidden box and
// asking the browser to copy the selection, usually still works from a click.

export type ClipboardEnv = {
  /** `navigator.clipboard`, where the browser has one. */
  clipboard: { writeText: (text: string) => Promise<void> } | undefined;
  /** Selects `text` in a box that is not shown and asks the browser to copy it; true when it did. */
  copyBySelecting: (text: string) => boolean;
};

/** Whether `text` reached the clipboard. Never throws: a false means "tell the owner to copy it by hand". */
export async function copyText(text: string, env: ClipboardEnv): Promise<boolean> {
  if (env.clipboard) {
    try {
      await env.clipboard.writeText(text);
      return true;
    } catch {
      // Refused, or the page is not focused: try the other way below.
    }
  }
  try {
    return env.copyBySelecting(text);
  } catch {
    return false;
  }
}
