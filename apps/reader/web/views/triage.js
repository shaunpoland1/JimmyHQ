// Triage: the articles waiting for you, one at a time, shaped for a five-minute break.
// Every word from an article goes in as text.
import { afterDecided, KEY_OF, keyHint, moveIndex, VOTES } from "../lib/keys.js";
import { afterChoice } from "../lib/offers.js";
import { clip } from "../lib/text.js";
import { sayWith, takeBack } from "../status.js";
import { h, say, why } from "../_ui.js";

const CHOICES = [
  ["keep", "Keep", "primary"],
  ["later", "Read later", ""],
  ["skip", "Skip", ""],
  ["send", "Send on", ""],
  ["mine", "Mine", ""],
];

export function current(ctx) {
  const list = ctx.state.data.triage;
  if (!list.length) return null;
  ctx.state.at = Math.min(Math.max(0, ctx.state.at), list.length - 1);
  return list[ctx.state.at];
}

export function render(ctx) {
  const data = ctx.state.data;
  const article = current(ctx);
  if (!article) {
    return h("div", { class: "empty" },
      h("p", { class: "big" }, "Nothing is waiting for you."),
      h("p", { class: "muted" }, "Reader suggests more as it finds them. Everything you chose is under Articles."),
      h("button", { type: "button", class: "btn", onclick: () => ctx.go("articles") }, "See all articles"));
  }
  const list = data.triage;
  const lettersOn = data.keys_on !== false;
  const full = ctx.state.full && ctx.state.full.id === article.id ? ctx.state.full : null;
  return h("div", {},
    h("p", { class: "muted small" }, `${ctx.state.at + 1} of ${list.length} waiting`),
    h("article", { class: "reading", "aria-labelledby": "reading-title" },
      h("h2", { id: "reading-title" }, article.title || article.host || "An article"),
      h("p", { class: "why" }, article.why),
      h("p", { class: "muted small" }, `${article.host}${article.topic ? ` · ${article.topic}` : ""}`),
      article.has_text
        ? h("p", { class: "excerpt" }, article.excerpt)
        : h("p", { class: "muted" }, "Its text was deleted; the address is below."),
      h("p", { class: "address" }, article.final_url || article.url),
      article.note ? h("p", { class: "note small" }, article.note) : null,
      article.offered ? h("p", { class: "muted small" }, "Offered to other apps already, as its topic says. Your choice here does not change that; Offered is where to take it back.") : null,
      article.has_text ? wholeArticle(ctx, article, full) : null,
      h("div", { class: "choices" }, CHOICES.map(([action, label, kind]) => h("button", {
        type: "button", class: `btn ${kind}`.trim(),
        disabled: action === "mine" && (!data.mining_on || !article.has_text),
        title: action === "mine" && !data.mining_on ? "Mining is off in Reader's settings." : undefined,
        onclick: () => decide(ctx, action),
      }, label, lettersOn ? h("kbd", {}, KEY_OF[action]) : null)))),
    keyHint(lettersOn).map((line) => h("p", { class: "muted small keys" }, line)),
    list.length > 1 ? h("div", { class: "upnext" },
      h("h3", {}, "Also waiting"),
      h("ul", {}, list.map((a, n) => n === ctx.state.at ? null : h("li", {},
        h("button", { type: "button", class: "link", onclick: () => { ctx.state.at = n; ctx.render(); } },
          clip(a.title || a.host, 120)),
        h("span", { class: "muted small" }, ` · ${clip(a.topic, 60)}`))))) : null);
}

function wholeArticle(ctx, article, full) {
  if (!full) {
    return h("button", { type: "button", class: "link small", onclick: () => openWhole(ctx, article.id) }, "Read it all here");
  }
  return h("details", { open: true },
    h("summary", {}, `The whole article, as Reader read it (${full.sentences.length} sentences)`),
    h("ol", { class: "sentences" }, full.sentences.map((s) => h("li", {}, s))));
}

async function openWhole(ctx, id) {
  try {
    ctx.state.full = await ctx.call("article.get", { id });
    ctx.render();
  } catch (error) {
    say(why(error), "bad");
  }
}

/** Keep, Read later, Skip, Send on or Mine, for the article on show. */
export async function decide(ctx, action) {
  const article = current(ctx);
  if (!article) return;
  if (action === "mine" && !ctx.state.data.mining_on) {
    say("Mining is off, so nothing was mined. Switch on \"Mine articles into Memory\" in Reader's settings first.", "bad");
    return;
  }
  let done;
  try {
    done = await ctx.call("article.choose", { id: article.id, action });
  } catch (error) {
    say(why(error), "bad");
    return;
  }
  const { words, undo } = afterChoice(action, done?.offer_change);
  sayWith(words, "ok", undo, () => takeBack(ctx, article.id));
  if (VOTES.has(action)) {
    const before = ctx.state.data.triage.length;
    ctx.state.data.triage = ctx.state.data.triage.filter((a) => a.id !== article.id);
    ctx.state.at = Math.max(0, afterDecided(ctx.state.at, before));
    ctx.state.full = null;
  }
  ctx.render();
  ctx.refresh();
}

/** Move to the next or the previous waiting article. */
export function move(ctx, step) {
  ctx.state.at = Math.max(0, moveIndex(ctx.state.at, ctx.state.data.triage.length, step));
  ctx.state.full = null;
  ctx.render();
}
