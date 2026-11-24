// The Heard screen. One page: the views are drawn by script, so the frame never loads
// again. Everything it shows comes from Heard's own program through hq.app.call. Nothing is
// stored in the browser, nothing is fetched from anywhere else, and no count is shown.
// Every word of a conversation is put on the page as text, never as HTML.
import { hq } from "./_bridge.js";
import { fill, h, say, why } from "./_ui.js";
import { TABS } from "./lib/format.js";
import * as conversation from "./views/conversation.js";
import * as days from "./views/days.js";
import * as search from "./views/search.js";
import * as settings from "./views/settings.js";

const VIEWS = { days, search, settings };

const ready = await hq.ready;
const state = { tab: "days", overview: null, shown: 0, open: null, query: "", found: null };

const ctx = {
  state,
  zone: ready.timezone || undefined,
  // A refusal reaches the page as the sentence the program wrote; `why` shows it as given.
  call: (name, args = {}) => hq.app.call(name, args),
  save: (file) => hq.ui.save(file),
  openSettings: () => hq.ui.settings(),
  show,
  async go(tab) {
    state.tab = tab;
    state.open = null;
    await show();
  },
  /** Open one conversation; `line` is a line to scroll to and mark (from a search). */
  async openConversation(id, line = null) {
    state.open = { id, line };
    await show();
  },
  async closeConversation() {
    state.open = null;
    await show();
  },
  async reloadOverview() {
    state.overview = await ctx.call("overview", {});
    drawTop();
  },
};

function drawTop() {
  fill(document.getElementById("nav"), TABS.map(([id, words]) => h("button", {
    type: "button", class: state.tab === id && !state.open ? "on" : "",
    "aria-current": state.tab === id && !state.open ? "page" : undefined,
    onclick: () => ctx.go(id),
  }, words)));
}

async function show() {
  drawTop();
  const root = document.getElementById("main");
  const mine = ++state.shown;
  fill(root, h("p", { class: "muted pad" }, "Loading..."));
  try {
    if (state.open) await conversation.show(ctx, root, () => mine === state.shown);
    else await VIEWS[state.tab].show(ctx, root, () => mine === state.shown);
  } catch (error) {
    if (mine !== state.shown) return;
    fill(root, h("p", { class: "problem" }, why(error)),
      h("button", { type: "button", class: "btn", onclick: () => show() }, "Try again"));
  }
}

hq.on("settings.changed", () => ctx.reloadOverview().catch((error) => say(why(error), "bad")));

async function start() {
  try {
    await ctx.reloadOverview();
    await show();
  } catch (error) {
    fill(document.getElementById("main"), h("p", { class: "problem" }, why(error)),
      h("button", { type: "button", class: "btn", onclick: () => start() }, "Try again"));
  }
}

await start();
