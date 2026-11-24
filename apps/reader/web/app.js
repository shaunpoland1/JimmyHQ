// The Reader screen. One page: the views are drawn by script, so the frame never loads
// again. Everything it shows comes from Reader's own program through hq.app.call;
// nothing is fetched from anywhere else.
import { hq } from "./_bridge.js";
import { keyAction } from "./lib/keys.js";
import { closeDialog, dialogOpen, fill, h, say, why } from "./_ui.js";
import * as articles from "./views/articles.js";
import * as offered from "./views/offered.js";
import * as topics from "./views/topics.js";
import * as triage from "./views/triage.js";

const VIEWS = [
  ["triage", "Triage", triage.render],
  ["articles", "Articles", articles.render],
  ["topics", "Topics", topics.render],
  ["offered", "Offered", offered.render],
];
// Views with fields the owner may be typing in: a refresh does not draw them again.
const QUIET = new Set(["topics", "articles"]);

const ready = await hq.ready;
const state = {
  view: "triage", data: null, articles: [], offered: [], at: 0, full: null, opened: null,
  editing: null, filters: { topic: "", what: "all", query: "" },
};

const ctx = {
  state,
  zone: ready.timezone || undefined,
  call: (name, args = {}) => hq.app.call(name, args),
  render,
  go(view) {
    state.view = view;
    render();
  },
  async refresh() {
    await load();
    render();
  },
};

async function load() {
  const [data, list, offers] = await Promise.all([
    ctx.call("overview", {}), ctx.call("articles.list", {}), ctx.call("offered.list", {})]);
  state.data = data;
  state.articles = list;
  state.offered = offers;
  // The number is the program's own (it says the same to the sidebar when this page is closed).
  hq.ui.badge(Number.isInteger(data.waiting) ? data.waiting : data.triage.length).catch(() => {});
}

function nav() {
  const counts = { triage: state.data.triage.length, offered: state.offered.length };
  return VIEWS.map(([id, words]) => h("button", {
    type: "button", class: state.view === id ? "on" : "", "aria-current": state.view === id ? "page" : undefined,
    onclick: () => ctx.go(id),
  }, words, counts[id] ? h("span", { class: "count" }, String(counts[id])) : null));
}

function late() {
  const r = state.data.researcher;
  if (!r.late) return null;
  return h("div", { class: "banner warn" },
    h("p", { class: "small" }, r.late.why),
    h("div", { class: "row" },
      [["wait", "Wait"], ["finish_plain", "Look without the researcher"], ["drop", "Drop this round"]].map(([action, label]) =>
        h("button", { type: "button", class: "btn small", onclick: async () => {
          try {
            await ctx.call("round.choose", { id: r.late.id, action });
            await ctx.refresh();
          } catch (error) {
            say(why(error), "bad");
          }
        } }, label))));
}

function render() {
  if (!state.data) return;
  fill(document.getElementById("nav"), nav());
  const view = VIEWS.find(([id]) => id === state.view) ?? VIEWS[0];
  fill(document.getElementById("main"), h("h1", { class: "visually-hidden" }, view[1]), late(), view[2](ctx));
}

async function redraw() {
  try {
    await load();
    if (!QUIET.has(state.view) || (state.view === "topics" && !state.editing)) render();
    else fill(document.getElementById("nav"), nav());
  } catch (error) {
    say(why(error), "bad");
  }
}

// --- keys --------------------------------------------------------------------------------

document.addEventListener("keydown", async (event) => {
  const action = keyAction(event, { view: state.view, dialogOpen: dialogOpen(), lettersOff: state.data?.keys_on === false });
  if (!action) return;
  if (action === "close") {
    if (dialogOpen()) return closeDialog();
    if (event.target?.blur && event.target !== document.body) event.target.blur();
    if (state.full) {
      state.full = null;
      render();
    }
    return;
  }
  event.preventDefault();
  if (action === "next") return triage.move(ctx, 1);
  if (action === "previous") return triage.move(ctx, -1);
  await triage.decide(ctx, action);
});

// --- start -------------------------------------------------------------------------------

let pending = null;
hq.on("reader.changed", () => {
  clearTimeout(pending);
  pending = setTimeout(redraw, 300);
});
hq.on("visible", () => redraw());

try {
  await load();
  if (!state.data.topics.length) {  // a first run: say why, and offer the form
    state.view = "topics";
    state.editing = "new";
  }
  render();
} catch (error) {
  fill(document.getElementById("main"), h("p", { class: "problem" }, why(error)),
    h("button", { type: "button", class: "btn", onclick: () => redraw() }, "Try again"));
}
