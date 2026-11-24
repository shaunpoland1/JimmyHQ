// Copying an article out as clean HTML or Markdown, or saving it as a file. Pure apart
// from the `env` it is given, so a test hands it a fake one.
//
// The program writes the text (articles/render.py), never the page. A page cannot save a
// file itself: its frame is sandboxed without downloads. So the page copies, or asks
// JimmyHQ to save the Markdown with `hq.ui.save` (only right after a click). When the
// browser will not let a framed page write to the clipboard, it shows the text selected
// for the owner to copy with the keyboard.

export const FORMS = { html: "clean HTML", markdown: "Markdown" };

export const SAVE_NOTE = "Save as a file downloads the article as Markdown. Copy puts it on the clipboard instead, to paste where it goes.";

export const COPY_BY_HAND = "Select the text and copy it with the keyboard (Ctrl+C, or Cmd+C on a Mac), then paste it where it goes.";

/**
 * What the page asks JimmyHQ to save for an article's Markdown: the file is named from the
 * article's title (JimmyHQ makes the name safe), or from the program's own name when
 * there is no title yet.
 */
export function saveRequest(piece, made) {
  const title = String(piece?.title ?? "").replace(/\s+/g, " ").trim();
  const name = title || fileName(made?.name, piece?.id, "markdown").replace(/\.md$/, "");
  return { name, type: "text/markdown", text: String(made?.text ?? "") };
}

/** The file name the program suggests, made safe to show: "article-12.html". */
export function fileName(given, id, form) {
  const ending = form === "markdown" ? "md" : "html";
  const name = String(given ?? "").replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "");
  return name && name.endsWith(`.${ending}`) ? name : `article-${Number(id) || 0}.${ending}`;
}

/**
 * Put `text` on the clipboard. `env` is {clipboard?: {writeText}, copyCommand?: (text) =>
 * boolean}. Resolves {ok: true, how: "clipboard" | "command"} or {ok: false} when
 * neither way was allowed, and never throws.
 */
export async function copyText(text, env) {
  try {
    if (env?.clipboard?.writeText) {
      await env.clipboard.writeText(String(text));
      return { ok: true, how: "clipboard" };
    }
  } catch {
    // A framed page is often refused the clipboard; the older way may still work.
  }
  try {
    if (env?.copyCommand && env.copyCommand(String(text))) return { ok: true, how: "command" };
  } catch {
    // Refused as well: the page shows the text to copy by hand.
  }
  return { ok: false };
}

/** The sentence after a copy. */
export function copiedSaid(result, form) {
  return result.ok ? `Copied as ${FORMS[form] ?? form}. Paste it where it goes.`
    : `This browser would not let the page copy. The ${FORMS[form] ?? form} is shown selected: press Ctrl+C, or Cmd+C on a Mac, to copy it.`;
}
