// The Articles screen. One page: the views are drawn by script, so the frame never loads
// again. Everything the page shows comes from the app's own program through
// hq.app.call; nothing is fetched from anywhere else.
import { hq } from "./_bridge.js";
import { keyAction, moveIndex } from "./lib/keys.js";
import { toDecide, toPublish } from "./lib/lists.js";
import { afterChange } from "./lib/redraw.js";
import { showsHeader } from "./lib/header.js";
import { count } from "./lib/today.js";
import { zoneFor } from "./lib/time.js";
import { closeDialog, dialogOpen, fill, h, say, why } from "./_ui.js";
import * as articles from "./views/articles.js";
import * as decide from "./views/decide.js";
import * as ideas from "./views/ideas.js";
import * as newArticle from "./views/new.js";
import * as publish from "./views/publish.js";
import * as sitesView from "./views/sites.js";
import * as today from "./views/today.js";

const VIEWS = [
  ["today", "Today", today.render],
  ["articles", "Articles", articles.render],
  ["publish", "Publish", publish.render],
  ["ideas", "Ideas from readers", ideas.render],
  ["new", "New", newArticle.render],
  ["sites", "Sites", sitesView.render],
];
const ready = await hq.ready;
const freshForm = () => ({ site: "", topic: "", angle: "", notes: "" });
const state = {
  view: "today", data: null, openPiece: null, detail: null, sentences: new Map(), selected: -1,
  lit: new Map(), openSources: new Set(), tab: "article", reading: "evidence", showWork: false,
  editingSite: null, newForm: freshForm(), ideaSite: "", pasteForm: { text: "", audience: "" }, typed: false, stale: false,
  filters: { query: "", site: "", group: "" },
  header: null, headerProblem: null, sentSaid: null,
};
const pictures = new Map();

const ctx = {
  state,
  ownerZone: zoneFor(null, ready.timezone),
  data: () => state.data,
  call: (name, args = {}) => hq.app.call(name, args),
  // The owner's own page for this app under Settings: it opens over this page, and only on a click.
  openSettings: () => hq.ui.settings(),
  /** Hands the owner a text file to keep: {name, type, text}. Only from a click; JimmyHQ makes the save. */
  save: (file) => hq.ui.save(file),
  /** One of the app's own pictures as an address the page can show, or null. */
  picture(ref) {
    if (!pictures.has(ref)) {
      pictures.set(ref, hq.files.thumbnail(ref).then((blob) => URL.createObjectURL(blob)).catch(() => null));
    }
    return pictures.get(ref);
  },
  zoneOfSite(id) {
    return zoneFor(state.data?.sites.find((s) => s.id === id), ready.timezone);
  },
  decideList: () => toDecide(state.data?.pieces),
  render,
  go(view, where = {}) {
    state.view = view;
    if (view === "articles" && where.piece) return ctx.open(where.piece);
    render();
    if (where.piece) document.getElementById(`pub-${where.piece}`)?.scrollIntoView({ block: "center" });
    return undefined;
  },
  async open(pieceId) {
    state.view = "articles";
    if (state.openPiece !== pieceId) {
      state.lit = new Map();
      state.openSources = new Set();
      state.sentences = new Map();
      state.tab = "article";
      state.showWork = false;
      state.header = null;
      state.headerProblem = null;
      state.sentSaid = null;
    }
    state.openPiece = pieceId;
    const at = articles.listed(ctx).findIndex((p) => p.id === pieceId);
    if (at >= 0) state.selected = at;
    await loadDetail();
    render();
    document.getElementById("detail")?.scrollIntoView({ block: "start" });
  },
  close() {
    state.openPiece = null;
    state.detail = null;
    state.header = null;
    state.headerProblem = null;
    state.sentSaid = null;
    state.lit = new Map();
    render();
  },
  async refresh() {
    await load();
    if (state.openPiece) await loadDetail();
    render();
  },
  resetNewForm() {
    const site = state.newForm.site;
    state.newForm = { ...freshForm(), site };
  },
};

async function load() {
  state.data = await ctx.call("overview", {});
  // The number is the program's own (it says the same to the sidebar when this page is closed).
  hq.ui.badge(Number.isInteger(state.data.waiting) ? state.data.waiting : count(state.data)).catch(() => {});
}

async function loadDetail() {
  const id = state.openPiece;
  try {
    state.detail = await ctx.call("article.view", { id });
  } catch (error) {
    state.detail = null;
    state.openPiece = null;
    say(why(error), "bad");
    return;
  }
  await loadHeader(id);
  // The sources' sentences, in parts; each part is drawn as it comes.
  if (!state.detail.sources.length || state.detail.sources.every((s) => state.sentences.has(s.n))) return;
  state.sentences = new Map();
  let after = 0;
  try {
    while (after !== null && state.openPiece === id) {
      const part = await ctx.call("article.sources", { id, after });
      for (const source of part.sources) state.sentences.set(source.n, { list: source.sentences, cut: source.cut });
      after = part.next;
    }
  } catch (error) {
    say(`The sources' sentences could not all be shown: ${why(error)}`, "bad");
  }
}

/** The article's header image, asked only where the page shows one. */
async function loadHeader(id) {
  if (!showsHeader(state.data, state.detail?.piece)) {
    state.header = null;
    return;
  }
  try {
    const got = await ctx.call("header.get", { id });
    state.header = { piece: id, header: got.header ?? null };
  } catch (error) {
    state.header = { piece: id, header: null, problem: why(error) };
  }
}

function nav() {
  const data = state.data;
  const counts = {
    today: count(data),
    articles: toDecide(data.pieces).length,
    publish: toPublish(data.pieces).length,
    ideas: (data.ideas ?? []).length,
  };
  return VIEWS.map(([id, words]) => h("button", {
    type: "button", class: state.view === id ? "on" : "", "aria-current": state.view === id ? "page" : undefined,
    onclick: () => { state.view = id; render(); },
  }, words, counts[id] ? h("span", { class: "count" }, String(counts[id])) : null));
}

function render() {
  if (!state.data) return;
  fill(document.getElementById("nav"), nav());
  const view = VIEWS.find(([id]) => id === state.view) ?? VIEWS[0];
  state.typed = false;
  state.stale = false;
  fill(document.getElementById("main"), h("h1", { class: "visually-hidden" }, view[1]), view[2](ctx));
}

async function redraw() {
  try {
    await load();
    if (state.openPiece && state.view === "articles") await loadDetail();
    if (afterChange({ view: state.view, typed: state.typed, focused: inField() }) === "all") render();
    else {
      state.stale = true;
      fill(document.getElementById("nav"), nav());
    }
  } catch (error) {
    say(why(error), "bad");
  }
}

/** Whether the owner is in one of the view's fields now. */
function inField() {
  const at = document.activeElement;
  return Boolean(at && at !== document.body && document.getElementById("main")?.contains(at)
    && ["INPUT", "TEXTAREA", "SELECT"].includes(at.tagName));
}

const main = document.getElementById("main");
main.addEventListener("input", () => { state.typed = true; });
// A view left stale while the owner was in a field is drawn once they leave it, if it may be.
main.addEventListener("focusout", () => setTimeout(() => {
  if (state.stale && afterChange({ view: state.view, typed: state.typed, focused: inField() }) === "all") render();
}, 0));

// --- keys --------------------------------------------------------------------------------

document.addEventListener("keydown", async (event) => {
  const action = keyAction(event, { view: state.view, dialogOpen: dialogOpen(), letters: state.data?.single_keys !== false });
  if (!action) return;
  if (action === "close") {
    if (dialogOpen()) return closeDialog();
    if (event.target?.blur && event.target !== document.body) event.target.blur();
    if (state.view === "articles" && state.openPiece) ctx.close();
    return;
  }
  event.preventDefault();
  const list = articles.listed(ctx);
  if (action === "next" || action === "previous") {
    state.selected = moveIndex(state.selected, list.length, action === "next" ? 1 : -1);
    if (state.selected >= 0) await ctx.open(list[state.selected].id);
    return;
  }
  if (action === "open") {
    const pick = list[Math.max(0, state.selected)];
    if (pick) await ctx.open(pick.id);
    return;
  }
  if (!state.detail) return;
  if (action === "approve") await decide.approve(ctx);
  if (action === "edit") await decide.edit(ctx);
  if (action === "send_back") await decide.sendBack(ctx);
  if (action === "decline") await decide.decline(ctx);
});

// --- start -------------------------------------------------------------------------------

let pending = null;
hq.on("articles.changed", () => {
  clearTimeout(pending);
  pending = setTimeout(redraw, 300);
});
hq.on("visible", () => redraw());

try {
  await load();
  if (!state.data.sites.length) {
    state.view = "sites";
    state.editingSite = "new";
  }
  render();
} catch (error) {
  fill(document.getElementById("main"), h("p", { class: "problem" }, why(error)),
    h("button", { type: "button", class: "btn", onclick: () => redraw() }, "Try again"));
}
