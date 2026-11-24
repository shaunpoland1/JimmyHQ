// New: a topic with an optional angle and notes, for a site.

import { topicFromForm } from "../lib/forms.js";
import { field, h, say, why } from "../_ui.js";

export function render(ctx) {
  const data = ctx.data();
  const form = ctx.state.newForm;
  if (!data.sites.length) {
    return h("div", { class: "empty" }, h("p", {}, "Add a site first, so Articles knows who it is writing for."),
      h("button", { type: "button", class: "btn primary", onclick: () => ctx.go("sites") }, "Add a site"));
  }
  if (!data.sites.some((s) => String(s.id) === form.site)) form.site = String(data.sites[0].id);
  const site = h("select", { onchange: () => { form.site = site.value; } },
    data.sites.map((s) => h("option", { value: String(s.id), selected: String(s.id) === form.site }, s.name)));
  const topic = h("input", { type: "text", maxlength: 300, value: form.topic, placeholder: "What the article is about", oninput: () => { form.topic = topic.value; } });
  const angle = h("input", { type: "text", maxlength: 600, value: form.angle, placeholder: "The way into it, if you have one", oninput: () => { form.angle = angle.value; } });
  const notes = h("textarea", { rows: 5, value: form.notes, placeholder: "What you know, what to look for, anything to avoid", oninput: () => { form.notes = notes.value; } });
  const start = h("button", { type: "submit", class: "btn primary" }, "Start");
  const begin = async () => {
    const made = topicFromForm(form);
    if (made.error) return say(made.error, "bad");
    start.disabled = true;
    try {
      const piece = await ctx.call("new.topic", made.args);
      ctx.resetNewForm();
      say("Started. It is compared with the site's earlier articles first, before anything is spent.", "ok");
      await ctx.refresh();
      await ctx.open(piece.id);
    } catch (error) {
      say(why(error), "bad");
    } finally {
      start.disabled = false;
    }
    return undefined;
  };
  return h("form", { class: "form narrow", onsubmit: (event) => { event.preventDefault(); begin(); } },
    h("h2", {}, "A new article"),
    field("Site", site),
    field("Topic", topic, "Articles searches the web for it, keeps the best sources, and writes only from them."),
    field("Angle (optional)", angle),
    field("Notes (optional)", notes, "Given to the writer as yours. A fact in your notes is not a source: the article must still cite one."),
    h("div", { class: "row" }, start));
}
