// New: paste a link, type a topic with notes, or upload pictures with notes, for a page;
// and the ideas Reader offered, each with Start and Dismiss.

import { activePages } from "../lib/pages.js";
import { waitsForBrandLine } from "../lib/reader.js";
import { pickRanOut, uploadProblem } from "../lib/uploads.js";
import { fill, field, h, say, why } from "../_ui.js";

const WAYS = [["link", "A link"], ["topic", "A topic"], ["pictures", "Pictures"]];

export function render(ctx) {
  const data = ctx.data();
  const form = ctx.state.newForm;
  if (!data.pages.length) {
    return h("div", { class: "empty" }, h("p", {}, "Add a brand first, so Posts knows who it is writing for."),
      h("button", { type: "button", class: "btn primary", onclick: () => ctx.go("pages") }, "Add a brand"));
  }
  const pages = activePages(data.pages); // a paused page takes no new work
  if (!pages.length) {
    return h("div", { class: "empty" }, h("p", {}, "Every brand is paused. Resume one under Brands to start something new."),
      h("button", { type: "button", class: "btn primary", onclick: () => ctx.go("pages") }, "Go to Brands"));
  }
  if (!pages.some((p) => String(p.id) === form.page)) form.page = String(pages[0].id);
  const page = h("select", { onchange: () => { form.page = page.value; } },
    pages.map((p) => h("option", { value: String(p.id), selected: String(p.id) === form.page }, p.name)));
  const tabs = h("div", { class: "segmented", role: "tablist", "aria-label": "How it starts" }, WAYS.map(([way, words]) => h("button", {
    type: "button", role: "tab", "aria-selected": String(form.way === way), class: form.way === way ? "on" : "",
    onclick: () => { form.way = way; ctx.render(); },
  }, words)));
  const notes = h("textarea", { rows: 4, value: form.notes, placeholder: "What you want said, facts you know, anything to avoid", oninput: () => { form.notes = notes.value; } });
  const sponsored = h("input", { type: "checkbox", checked: form.sponsored, onchange: () => { form.sponsored = sponsored.checked; } });
  const parts = [field("Brand", page)];
  if (form.way === "link") {
    const url = h("input", { type: "url", value: form.url, placeholder: "https://news.example.com/article", oninput: () => { form.url = url.value; } });
    parts.push(field("Link", url, "Posts reads the article and checks the draft against it."));
  } else if (form.way === "topic") {
    const topic = h("input", { type: "text", maxlength: 300, value: form.topic, placeholder: "What the post is about", oninput: () => { form.topic = topic.value; } });
    parts.push(field("Topic", topic, "With no article, every number and quote can rest only on your notes."));
  } else {
    parts.push(pictures(ctx));
  }
  parts.push(field("Your notes", notes), h("label", { class: "check-line" }, sponsored, " This post is sponsored"));
  const start = h("button", { type: "button", class: "btn primary", onclick: () => begin(ctx, start) }, "Start");
  return h("div", {}, ideas(ctx), h("div", { class: "form narrow" }, tabs, parts, h("div", { class: "row" }, start)));
}

/** The ideas Reader offered. What Reader said is shown as Reader's; Posts reads each article itself. */
function ideas(ctx) {
  const data = ctx.data();
  const reader = data.reader ?? {};
  const list = data.ideas ?? [];
  const parts = [];
  if (reader.loud) parts.push(h("p", { class: "problem" }, `${reader.said} ${reader.todo}`));
  if (reader.quiet) parts.push(h("p", { class: "small muted" }, reader.quiet));
  if (list.length) {
    parts.push(h("h2", {}, `Ideas from Reader (${list.length})`),
      h("p", { class: "small muted" }, "Nothing is drafted until you press Start. Posts then reads the article itself and checks it like any link."),
      h("ul", { class: "rows" }, list.map((idea) => ideaRow(ctx, idea))));
  }
  return parts.length ? h("section", { class: "ideas", "aria-label": "From Reader" }, parts) : null;
}

function ideaRow(ctx, idea) {
  const data = ctx.data();
  const page = h("select", { "aria-label": `Brand for ${idea.title || idea.url}` },
    activePages(data.pages).map((p) => h("option", { value: String(p.id), selected: String(p.id) === ctx.state.newForm.page }, p.name)));
  const act = async (call, args, done) => {
    try {
      await ctx.call(call, args);
      say(done, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    }
  };
  return h("li", {},
    h("p", { class: "strong" }, idea.title || idea.url),
    idea.topic ? h("p", { class: "small" }, `From Reader, topic ${idea.topic}`) : h("p", { class: "small" }, "From Reader"),
    idea.why ? h("p", { class: "small muted" }, `Reader said: ${idea.why}`) : null,
    waitsForBrandLine(data) ? h("p", { class: "small" }, waitsForBrandLine(data)) : null,
    h("p", { class: "small muted address" }, idea.url),
    h("div", { class: "row wrap" }, page,
      h("button", { type: "button", class: "btn small primary", onclick: () => act("idea.start", { id: idea.id, page_id: Number(page.value) }, "Started. Posts reads and checks the article first.") }, "Start"),
      h("button", { type: "button", class: "btn small", onclick: () => act("idea.dismiss", { id: idea.id }, "Dismissed.") }, "Dismiss")));
}

function pictures(ctx) {
  const form = ctx.state.newForm;
  const list = h("ol", { class: "uploads" });
  const draw = () => fill(list, form.pictures.map((pic, i) => {
    const img = h("img", { alt: pic.name });
    if (pic.url) img.src = pic.url;
    const note = h("input", { type: "text", value: pic.note, placeholder: "What is this? Who or what should be mentioned?", "aria-label": `Note on picture ${i + 1}`,
      oninput: () => { pic.note = note.value; } });
    return h("li", {}, img, h("div", { class: "stack grow" }, h("span", { class: "small muted" }, pic.name), note),
      h("button", { type: "button", class: "btn small", "aria-label": `Leave out picture ${i + 1}`, onclick: () => { form.pictures.splice(i, 1); draw(); } }, "Leave out"));
  }));
  const input = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", multiple: true });
  input.addEventListener("change", async () => {
    const files = [...input.files];
    for (const [i, file] of files.entries()) {
      try {
        const kept = await ctx.upload(file);
        form.pictures.push({ ...kept, note: "", url: await ctx.thumbnail(kept.ref) });
        draw();
      } catch (error) {
        // In the owner's words: the refusal JimmyHQ gives is written for the app's code.
        say(uploadProblem(error, files.length - i), "bad");
        if (pickRanOut(error)) break; // the rest of this pick would be refused the same way
      }
    }
    input.value = "";
  });
  draw();
  return h("div", { class: "stack" }, field("Pictures", input, "PNG, JPEG or WebP. Each is looked at by your model for pictures before anything is written."), list);
}

async function begin(ctx, button) {
  const form = ctx.state.newForm;
  const base = { page_id: Number(form.page), notes: form.notes, sponsored: form.sponsored };
  const args = form.way === "link" ? { ...base, url: form.url }
    : form.way === "topic" ? { ...base, topic: form.topic }
      : { ...base, pictures: form.pictures.map((p) => ({ ref: p.ref, note: p.note })) };
  button.disabled = true;
  try {
    const piece = await ctx.call(`new.${form.way}`, args);
    ctx.resetNewForm();
    say("Started. Posts reads and checks it first; it comes to Drafts when it is ready.", "ok");
    await ctx.refresh();
    await ctx.open(piece.id);
  } catch (error) {
    say(why(error), "bad");
  } finally {
    button.disabled = false;
  }
}
