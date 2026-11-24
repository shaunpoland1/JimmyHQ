// The Packages screen. One page: the views are drawn by script, so the frame never loads
// again. Everything it shows comes from Packages' own program through hq.app.call;
// nothing is fetched from anywhere else.
import { hq } from "./_bridge.js";
import { fill, h, say, why } from "./_ui.js";
import { refreshLabel, refreshWords } from "./lib/lines.js";
import { counts, loud } from "./lib/rows.js";
import { nextLine } from "./lib/times.js";
import * as form from "./views/form.js";
import * as list from "./views/list.js";

const VIEWS = [["way", "On the way"], ["delivered", "Delivered"]];

const ready = await hq.ready;
const state = { view: "way", data: null, adding: false, editing: null, busy: false };

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
  state.data = await ctx.call("overview", {});
  hq.ui.badge(loud(state.data.rows)).catch(() => {});
}

function nav() {
  const total = counts(state.data.rows);
  return VIEWS.map(([id, words]) => h("button", {
    type: "button", class: state.view === id ? "on" : "", "aria-current": state.view === id ? "page" : undefined,
    onclick: () => ctx.go(id),
  }, words, total[id] ? h("span", { class: "count" }, String(total[id])) : null));
}

async function refreshNow() {
  state.busy = true;
  render();
  try {
    const { words, tone } = refreshWords(await ctx.call("refresh", {}));
    say(words, tone);
  } catch (error) {
    say(why(error), "bad");
  } finally {
    state.busy = false;
    await load().catch(() => {});
    render();
  }
}

function toolbar() {
  const data = state.data;
  const tracker = data.tracker;
  return h("div", {},
    tracker.key_problem ? h("div", { class: "banner warn" },
      h("p", { class: "small" }, tracker.key_problem),
      h("button", { type: "button", class: "btn small", onclick: () => hq.ui.settings().catch((e) => say(why(e), "bad")) }, "Open settings")) : null,
    data.schedule.clock_note ? h("p", { class: "note small" }, data.schedule.clock_note) : null,
    h("div", { class: "row" },
      h("button", { type: "button", class: "btn primary", disabled: state.adding, onclick: () => { state.adding = true; render(); } }, "Add a package"),
      tracker.outside || tracker.practice ? h("button", { type: "button", class: "btn", disabled: state.busy, "aria-busy": state.busy ? "true" : undefined, onclick: refreshNow }, refreshLabel(state.busy)) : null),
    h("p", { class: tracker.practice ? "note small" : "muted small" }, nextLine(data.schedule, Date.now())),
    data.quota ? h("p", { class: "muted small" }, `${tracker.label} quota: ${data.quota.left} left of ${data.quota.total}. A number uses one place, once.`) : null);
}

function render() {
  if (!state.data) return;
  fill(document.getElementById("nav"), nav());
  const view = VIEWS.find(([id]) => id === state.view) ?? VIEWS[0];
  fill(document.getElementById("main"), h("h1", { class: "visually-hidden" }, view[1]), toolbar(),
    state.adding ? form.render(ctx) : null, list.render(ctx));
}

async function redraw() {
  try {
    await load();
    if (state.adding || state.editing) fill(document.getElementById("nav"), nav());  // do not take typing away
    else render();
  } catch (error) {
    say(why(error), "bad");
  }
}

let pending = null;
hq.on("packages.changed", () => {
  clearTimeout(pending);
  pending = setTimeout(redraw, 300);
});
hq.on("visible", () => redraw());

try {
  await load();
  if (!state.data.rows.length) state.adding = true;  // a first run: offer the form
  render();
} catch (error) {
  fill(document.getElementById("main"), h("p", { class: "problem" }, why(error)),
    h("button", { type: "button", class: "btn", onclick: () => redraw() }, "Try again"));
}
