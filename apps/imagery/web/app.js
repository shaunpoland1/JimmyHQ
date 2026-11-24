// The Imagery screen. One page: the views are drawn by script, so the frame never loads
// again. Everything it shows comes from Imagery's own program through hq.app.call;
// nothing is fetched from anywhere else. While pictures are being made it asks how they are
// going every couple of seconds, and also listens for the program's nudge.
import { hq } from "./_bridge.js";
import { fill, h, say, why } from "./_ui.js";
import { anyActive } from "./lib/format.js";
import * as gallery from "./views/gallery.js";
import * as make from "./views/make.js";

const VIEWS = [["make", "Make"], ["gallery", "Gallery"]];
const POLL_MS = 2000;

const ready = await hq.ready;
const state = {
  view: "make", data: null, rows: [], total: 0, query: "", starred: false,
  form: { prompt: "", style: "", aspect: "", count: 1, reference: null },
  busy: false,
};

const ctx = {
  state,
  zone: ready.timezone || undefined,
  call: (name, args = {}) => hq.app.call(name, args),
  save: (file) => hq.ui.save(file),
  picture: (ref) => hq.files.picture(ref),
  upload: (file) => hq.files.upload(file),
  openSettings: () => hq.ui.settings().catch((error) => say(why(error), "bad")),
  render,
  async go(view) {
    state.view = view;
    if (view === "gallery") await loadRows(true);
    render();
  },
  async reloadRows() { await loadRows(true); },
  async refresh() {
    await load();
    if (state.view === "gallery") await loadRows(true);
    render();
  },
  async more() { await loadRows(false); },
};

async function load() {
  const data = await ctx.call("overview", {});
  state.data = data;
  if (!state.form.aspect || !data.provider.aspects.includes(state.form.aspect)) {
    state.form.aspect = data.provider.aspects[0] ?? "";
  }
  if (state.form.count > data.max_count) state.form.count = data.max_count;
  if (state.form.reference && !data.provider.supports_reference) state.form.reference = null;
}

async function loadRows(fresh) {
  const offset = fresh ? 0 : state.rows.length;
  const got = await ctx.call("pictures.list", {
    query: state.query, starred: state.starred, offset, limit: state.data?.page_size ?? 48,
  });
  state.rows = fresh ? got.rows : [...state.rows, ...got.rows];
  state.total = got.total;
}

function nav() {
  return VIEWS.map(([id, words]) => h("button", {
    type: "button", class: state.view === id ? "on" : "", "aria-current": state.view === id ? "page" : undefined,
    onclick: () => ctx.go(id),
  }, words, id === "gallery" && state.data?.gallery.pictures ? h("span", { class: "count" }, String(state.data.gallery.pictures)) : null));
}

function render() {
  if (!state.data) return;
  fill(document.getElementById("nav"), nav());
  const body = state.view === "gallery" ? gallery.render(ctx) : make.render(ctx);
  fill(document.getElementById("main"), body);
  keepPolling();
}

// --- polling --------------------------------------------------------------------------

let timer = null;
let lastMade = -1;

function keepPolling() {
  clearTimeout(timer);
  if (anyActive(state.data?.runs)) timer = setTimeout(poll, POLL_MS);
}

async function poll() {
  try {
    const seen = await ctx.call("status", {});
    const made = seen.runs.reduce((n, run) => n + run.made, 0);
    const changed = made !== lastMade || seen.busy !== state.data.busy;
    lastMade = made;
    state.data = { ...state.data, runs: seen.runs, busy: seen.busy, today: seen.today, gallery: seen.gallery };
    if (changed && state.view === "gallery") await loadRows(true);
    if (state.view === "make") make.update?.(ctx);
    if (changed && state.view === "gallery") gallery.update?.(ctx);
    fill(document.getElementById("nav"), nav());
  } catch (error) {
    say(why(error), "bad");
  }
  keepPolling();
}

let pending = null;
hq.on("imagery.changed", () => {
  clearTimeout(pending);
  pending = setTimeout(async () => {
    try {
      await load();
      if (state.view === "gallery") await loadRows(true);
      // A page being typed into is left alone: only its live parts are redrawn.
      if (document.activeElement && ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement.tagName)) {
        make.update?.(ctx);
        fill(document.getElementById("nav"), nav());
        keepPolling();
      } else render();
    } catch (error) {
      say(why(error), "bad");
    }
  }, 300);
});
hq.on("visible", () => poll());

try {
  await load();
  render();
} catch (error) {
  fill(document.getElementById("main"), h("p", { class: "problem" }, why(error)),
    h("button", { type: "button", class: "btn", onclick: () => location.reload() }, "Try again"));
}
