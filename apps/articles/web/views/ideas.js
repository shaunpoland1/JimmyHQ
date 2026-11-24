// Ideas from readers: paste replies (many at once) with an optional audience, find ideas
// in them, and start or dismiss each idea. The page is only ever told counts: no reply's
// text comes back to it, and what the owner pasted is cleared once it is kept.

import { FORGET_REPLIES, askSafely, dismissIdea } from "../lib/danger.js";
import { ideaSites, ideaStart, pasteSaid } from "../lib/forms.js";
import { dialog, field, h, say, why } from "../_ui.js";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Where the replies go when ideas are found, in plain words. */
export function modelLine(hosted) {
  return hosted
    ? "Finding ideas may use a hosted model, outside your home: you allowed that under Articles' settings. The replies are still never kept on your record."
    : "Finding ideas uses a model on your own computer only. If none is set up for that role, nothing is sent anywhere and the page says so. You can change this under Articles' settings.";
}

export function render(ctx) {
  const data = ctx.data();
  const form = ctx.state.pasteForm;
  const counts = data.replies;
  const text = h("textarea", { rows: 10, value: form.text, placeholder: "Paste the replies here, with a line of --- between them, or with their From: lines.",
    oninput: () => { form.text = text.value; } });
  const audience = h("input", { type: "text", maxlength: 60, value: form.audience, placeholder: "Parents, teachers, …",
    oninput: () => { form.audience = audience.value; } });
  const keep = h("button", { type: "button", class: "btn primary", onclick: async () => {
    keep.disabled = true;
    try {
      const got = await ctx.call("replies.paste", { text: form.text, audience: form.audience });
      form.text = "";
      say(pasteSaid(got), "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    } finally {
      keep.disabled = false;
    }
  } }, "Keep these replies");
  const find = h("button", { type: "button", class: "btn primary", disabled: !counts.unread, onclick: async () => {
    find.disabled = true;
    say("Looking for ideas in the replies…");
    try {
      const got = await ctx.call("ideas.find", {});
      const where = got["local"] ? "A model on your own computer read them." : "A hosted model read them, as you allowed.";
      say(`${plural(got.found, "idea")} found${got.set_aside ? `, ${got.set_aside} set aside for breaking a rule` : ""}. ${where}`, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
      find.disabled = false;
    }
  } }, "Find ideas");
  const forget = h("button", { type: "button", class: "btn small danger", disabled: !counts.replies, onclick: async () => {
    if (!(await askSafely({ dialog, document }, FORGET_REPLIES.title, FORGET_REPLIES.intro, FORGET_REPLIES.yes))) return;
    try {
      const got = await ctx.call("replies.forget", {});
      say(`Deleted ${plural(got.deleted, "reply", "replies")} for good.`, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    }
  } }, "Delete every reply");
  return h("div", { class: "ideas" },
    h("section", { class: "form narrow", "aria-labelledby": "paste-h" },
      h("h2", { id: "paste-h" }, "Readers' replies"),
      h("p", { class: "small" }, counts.hint),
      field("Replies", text),
      field("Who they are (optional)", audience, "A word or two, kept with each reply and with the ideas found in them."),
      h("div", { class: "row" }, keep)),
    h("section", { "aria-labelledby": "find-h" },
      h("h2", { id: "find-h" }, "Find ideas"),
      h("p", {}, `${plural(counts.replies, "reply", "replies")} kept, ${counts.unread} not looked at yet.`),
      h("p", { class: "small muted" }, modelLine(data.ideas_hosted)),
      h("div", { class: "row wrap" }, find, forget)),
    ideaList(ctx));
}

function ideaList(ctx) {
  const data = ctx.data();
  const list = data.ideas ?? [];
  if (!list.length) return h("p", { class: "muted pad" }, "No ideas are waiting.");
  return h("section", { "aria-labelledby": "ideas-h" },
    h("h2", { id: "ideas-h" }, `Ideas waiting (${list.length})`),
    h("p", { class: "small muted" }, "Each idea is in the model's own words, never a reader's. Nothing is researched until you choose its site and press Start. Dismiss asks first: a dismissed idea does not come back."),
    h("ul", { class: "rows" }, list.map((idea) => ideaRow(ctx, idea))));
}

function ideaRow(ctx, idea) {
  const data = ctx.data();
  const offered = ideaSites(data.sites, ctx.state.ideaSite);
  const site = h("select", { "aria-label": `Site for ${idea.title}`, onchange: () => { ctx.state.ideaSite = site.value; } },
    offered.chosen ? null : h("option", { value: "" }, "Choose a site"),
    offered.choices.map((c) => h("option", { value: c.value, selected: c.value === offered.chosen }, c.name)));
  const start = () => {
    const made = ideaStart(idea.id, site.value);
    if (made.error) {
      say(made.error, "bad");
      site.focus();
      return undefined;
    }
    return act("idea.start", made.args, "Started. It is compared with earlier articles first.");
  };
  const dismiss = async () => {
    const words = dismissIdea(idea.title);
    if (!(await askSafely({ dialog, document }, words.title, words.intro, words.yes))) return;
    await act("idea.dismiss", { id: idea.id }, "Dismissed. It will not be proposed again.");
  };
  const act = async (call, args, done) => {
    try {
      await ctx.call(call, args);
      say(done, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    }
  };
  return h("li", { class: "idea" },
    h("p", { class: "strong" }, idea.title),
    h("p", { class: "small muted" }, `Rests on ${plural(idea.replies, "reply", "replies")}${idea.audience ? ` · ${idea.audience}` : ""}`),
    idea.angle ? h("p", { class: "small" }, h("span", { class: "strong" }, "Angle: "), idea.angle) : null,
    idea.why ? h("p", { class: "small" }, h("span", { class: "strong" }, "Why: "), idea.why) : null,
    h("div", { class: "row wrap" },
      data.sites.length ? site : h("span", { class: "small muted" }, "Add a site to start it."),
      h("button", { type: "button", class: "btn small primary", disabled: !data.sites.length,
        onclick: start }, "Start"),
      h("button", { type: "button", class: "btn small", onclick: dismiss }, "Dismiss")));
}
