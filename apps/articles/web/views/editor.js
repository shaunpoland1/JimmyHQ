// The owner's editor for an article: the allowed markup as plain text, with a preview
// drawn by the page's one safe renderer as it is typed. Saving makes a new version; the
// app sanitises it and runs every check again.

import { build, parse } from "../lib/markup.js";
import { dialog, field, fill, h } from "../_ui.js";

export const MARKUP_HELP = "Paragraphs are <p>…</p>, headings <h2> or <h3>, lists <ul> or <ol> with <li>, "
  + "and <strong>, <em>, <blockquote>, <br>. Cite a source sentence as [S2:5], or several as [S2:5,7] or [S1:3; S4:2]. "
  + "A link may go only to a source, as <a href=\"S2\">…</a>; anything else is taken out when you save.";

/**
 * Resolves with what the app answered when a new version was saved (true when it said
 * nothing), else false. `how` may give the dialog's own {title, intro, label} and `save`,
 * which is handed the title and the text instead of the usual `article.edit`.
 */
export async function editArticle(ctx, d, how = {}) {
  const sources = new Set(d.sources.map((s) => s.n));
  const title = h("input", { type: "text", value: d.revision.title, maxlength: 200 });
  const body = h("textarea", { rows: 18, value: d.revision.body, spellcheck: "true", class: "mono-text" });
  const preview = h("div", { class: "preview article-text", "aria-live": "polite" });
  const removed = h("p", { class: "small warn-text" });
  let timer = null;
  let answer = null;
  const label = how.label ?? "Save";
  const draw = () => {
    const tree = parse(body.value, sources);
    fill(preview, h("h3", { class: "article-title" }, title.value), build(tree.nodes, h));
    removed.textContent = tree.removed.length ? `When you save, Articles takes out: ${tree.removed.join("; ")}.` : "";
  };
  const later = () => { clearTimeout(timer); timer = setTimeout(draw, 150); };
  body.addEventListener("input", later);
  title.addEventListener("input", later);
  draw();
  const saved = await dialog({
    title: how.title ?? "Edit the article",
    intro: how.intro ?? "Your text is kept as a new version. When you save, every check runs again on it and the page shows what they found.",
    body: h("div", { class: "editor" },
      h("div", { class: "editor-write" }, field("Title", title), field("Text", body, MARKUP_HELP)),
      h("div", { class: "editor-preview" }, h("span", { class: "label" }, "Preview"), removed, preview)),
    actions: [{ label, kind: "primary", run: async () => {
      if (!body.value.trim()) return "You left the article empty. Nothing was changed. Write its text, or drop it.";
      answer = how.save ? await how.save(title.value, body.value)
        : await ctx.call("article.edit", { id: d.piece.id, title: title.value, body: body.value });
    } }],
  });
  clearTimeout(timer);
  return saved === label ? (answer ?? true) : false;
}
