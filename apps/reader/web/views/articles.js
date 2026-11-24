// Articles: everything Reader suggested, and what it passed over, filtered by topic,
// by what happened to it, and by words.
import { countsByWhat, filterArticles, stateWords, WHAT } from "../lib/filters.js";
import { afterChoice } from "../lib/offers.js";
import { clip, when } from "../lib/text.js";
import { sayWith, takeBack } from "../status.js";
import { h, say, why } from "../_ui.js";

export function render(ctx) {
  const f = ctx.state.filters;
  const topics = ctx.state.data.topics;
  const list = h("div", { id: "article-list" });
  const what = h("select", { "aria-label": "What happened", onchange: (e) => { f.what = e.target.value; update(); } });
  // Only the list and the counts are drawn again as the owner types, so the search box keeps its focus.
  function update() {
    const all = ctx.state.articles ?? [];
    const counts = countsByWhat(all, f.topic);
    what.replaceChildren(...WHAT.map(([id, words]) => h("option", { value: id, selected: f.what === id }, `${words} (${counts[id]})`)));
    const shown = filterArticles(all, f);
    list.replaceChildren(shown.length
      ? h("ul", { class: "rows" }, shown.map((a) => row(ctx, a)))
      : h("p", { class: "muted pad" }, all.length ? "No article matches these filters." : "No articles yet."));
  }
  update();
  return h("div", {},
    h("div", { class: "row filters" },
      h("input", { type: "search", placeholder: "Search titles, sources and reasons", value: f.query, "aria-label": "Search articles",
        oninput: (e) => { f.query = e.target.value; update(); } }),
      h("select", { "aria-label": "Topic", onchange: (e) => { f.topic = e.target.value; update(); } },
        h("option", { value: "", selected: f.topic === "" }, "Every topic"),
        topics.map((t) => h("option", { value: String(t.id), selected: String(f.topic) === String(t.id) }, t.name))),
      what),
    list);
}

function row(ctx, a) {
  const open = ctx.state.opened?.id === a.id ? ctx.state.opened : null;
  const tags = [stateWords(a), a.mined ? `${a.mined} mined` : "", a.offered ? "offered" : "", a.posted_close ? "close to a post" : "", a.has_text ? "" : "text deleted"].filter(Boolean);
  return h("li", { id: `article-${a.id}` },
    h("button", { type: "button", class: "link", "aria-expanded": open ? "true" : "false", onclick: () => toggle(ctx, a.id) },
      a.title || a.host || a.url),
    h("p", { class: "small" }, a.why || a.said),
    h("p", { class: "muted small" }, `${a.host}${a.topic ? ` · ${a.topic}` : ""} · ${when(a.suggested || a.created, ctx.zone)}`),
    h("div", { class: "chips" }, tags.map((t) => h("span", { class: "chip" }, t))),
    a.posted_close ? h("p", { class: "muted small" }, a.posted_close) : null,
    open ? detail(ctx, open) : null);
}

async function toggle(ctx, id) {
  if (ctx.state.opened?.id === id) {
    ctx.state.opened = null;
    ctx.render();
    return;
  }
  try {
    ctx.state.opened = await ctx.call("article.get", { id });
    ctx.render();
  } catch (error) {
    say(why(error), "bad");
  }
}

function detail(ctx, a) {
  const choices = [["keep", "Keep"], ["later", "Read later"], ["skip", "Skip"], ["send", "Send on"]];
  const suggested = ["new", "kept", "later", "skipped", "sent"].includes(a.state);
  return h("div", { class: "form" },
    h("p", { class: "address" }, a.final_url || a.url),
    a.said && a.why ? h("p", { class: "small muted" }, a.said) : null,
    a.note ? h("p", { class: "note small" }, a.note) : null,
    suggested ? h("div", { class: "row" },
      choices.map(([action, label]) => h("button", { type: "button", class: `btn small${a.state === stateOf(action) ? " primary" : ""}`,
        onclick: () => choose(ctx, a.id, action) }, label)),
      h("button", { type: "button", class: "btn small", disabled: !ctx.state.data.mining_on || !a.sentences.length,
        title: ctx.state.data.mining_on ? undefined : "Mining is off in Reader's settings.",
        onclick: () => choose(ctx, a.id, "mine") }, "Mine")) : null,
    suggested && a.offered ? h("div", { class: "row" },
      h("p", { class: "small muted" }, a.state === "sent"
        ? "Offered to other apps. Taking it back puts it in Triage again; Keep, Read later or Skip takes it back too, unless its topic offers it."
        : "Offered to other apps, as its topic says. Your choice here does not change that."),
      h("button", { type: "button", class: "btn small", onclick: () => takeBack(ctx, a.id) }, "Take it back")) : null,
    a.facts?.length ? h("div", {},
      h("h3", {}, "Facts kept for Memory"),
      h("ul", {}, a.facts.map((fact) => h("li", { class: "small" },
        `Sentences ${fact.sentences.join(", ")}${fact.note ? `: ${clip(fact.note, 200)}` : ""}`)))) : null,
    a.sentences.length
      ? h("details", {}, h("summary", {}, `The whole article (${a.sentences.length} sentences)`),
        h("ol", { class: "sentences" }, a.sentences.map((s) => h("li", {}, s))))
      : h("p", { class: "muted small" }, "Its text is not kept."));
}

function stateOf(action) {
  return { keep: "kept", later: "later", skip: "skipped", send: "sent" }[action];
}

async function choose(ctx, id, action) {
  try {
    const done = await ctx.call("article.choose", { id, action });
    const { words, undo } = afterChoice(action, done?.offer_change);
    sayWith(words, "ok", undo, () => takeBack(ctx, id));
    await ctx.refresh();
    ctx.state.opened = await ctx.call("article.get", { id });
    ctx.render();
  } catch (error) {
    say(why(error), "bad");
  }
}
