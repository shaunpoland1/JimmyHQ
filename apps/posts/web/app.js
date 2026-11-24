// The Posts screen. One page: the views are drawn by script, so the frame never loads
// again. Everything the page shows comes from the app's own program through
// hq.app.call; nothing is fetched from anywhere else.
import { hq } from "./_bridge.js";
import { keyAction, moveIndex } from "./lib/keys.js";
import { draftsToDecide, queue } from "./lib/lists.js";
import { afterChange } from "./lib/redraw.js";
import { needs, waiting } from "./lib/today.js";
import { zoneFor } from "./lib/time.js";
import { closeDialog, dialogOpen, fill, h, say, why } from "./_ui.js";
import * as drafts from "./views/drafts.js";
import { droppedList, today, went } from "./views/lists.js";
import * as newPiece from "./views/new.js";
import * as pagesView from "./views/pages.js";
import * as queueView from "./views/queue.js";
import * as scheduledView from "./views/scheduled.js";

const VIEWS = [
  ["today", "Today", today],
  ["drafts", "Drafts", drafts.render],
  ["queue", "Queue", queueView.render],
  ["scheduled", "Scheduled", scheduledView.render],
  ["went", "Went out", went],
  ["dropped", "Dropped", droppedList],
  ["new", "New", newPiece.render],
  ["pages", "Brands", pagesView.render],
];
const ready = await hq.ready;
const freshForm = () => ({ way: "link", page: "", url: "", topic: "", notes: "", sponsored: false, pictures: [] });
const state = {
  view: "today", data: null, openPiece: null, detail: null, selected: -1, lit: [], showWork: false,
  editingPage: null, rules: [], newForm: freshForm(), typed: false, stale: false,
  scheduled: { data: null, busy: false, problem: "", open: new Set() },
  filters: { went: { query: "", page: "" }, dropped: { query: "", page: "" } },
};
const thumbs = new Map();

const ctx = {
  state,
  ownerZone: zoneFor(null, ready.timezone),
  data: () => state.data,
  call: (name, args = {}) => hq.app.call(name, args),
  zoneOfPage(id) {
    return zoneFor(state.data?.pages.find((p) => p.id === id), ready.timezone);
  },
  upload: (file) => hq.files.upload(file),
  async thumbnail(ref) {
    if (!thumbs.has(ref)) {
      thumbs.set(ref, hq.files.thumbnail(ref).then((blob) => URL.createObjectURL(blob)).catch(() => null));
    }
    return thumbs.get(ref);
  },
  render,
  go(view, where = {}) {
    state.view = view;
    if (view === "drafts" && where.piece) return ctx.open(where.piece);
    render();
    if (where.post) document.getElementById(`post-${where.post}`)?.scrollIntoView({ block: "center" });
    return undefined;
  },
  async open(pieceId, { edit = false } = {}) {
    state.view = "drafts";
    if (state.openPiece !== pieceId) {
      state.lit = [];
      state.showWork = false;
    }
    state.openPiece = pieceId;
    const list = draftsToDecide(state.data.posts);
    const at = list.findIndex((p) => p.piece_id === pieceId);
    if (at >= 0) state.selected = at;
    await loadDetail();
    render();
    document.getElementById("detail")?.scrollIntoView({ block: "start" });
    if (edit) await drafts.edit(ctx);
  },
  close() {
    state.openPiece = null;
    state.detail = null;
    state.lit = [];
    render();
  },
  async refresh() {
    await load();
    if (state.openPiece) await loadDetail();
    render();
  },
  async loadRules() {
    const seen = new Map();
    const lists = await Promise.all([ctx.call("rules.list", {}), ...state.data.pages.map((p) => ctx.call("rules.list", { page_id: p.id }))]);
    for (const rule of lists.flat()) seen.set(rule.id, rule);
    state.rules = [...seen.values()].sort((a, b) => a.id - b.id);
  },
  resetNewForm() {
    state.newForm = freshForm();
  },
};

async function load() {
  state.data = await ctx.call("overview", {});
  // The number is the program's own (it says the same to the sidebar when this page is closed).
  const said = Number.isInteger(state.data.waiting) ? state.data.waiting : waiting(needs(state.data));
  hq.ui.badge(said).catch(() => {});
}

async function loadDetail() {
  try {
    state.detail = await ctx.call("piece.get", { id: state.openPiece });
  } catch (error) {
    state.detail = null;
    state.openPiece = null;
    say(why(error), "bad");
  }
}

function nav() {
  const data = state.data;
  const counts = {
    today: waiting(needs(data)),
    drafts: draftsToDecide(data.posts).length,
    queue: queue(data.posts).length,
  };
  return VIEWS.map(([id, words]) => h("button", {
    type: "button", class: state.view === id ? "on" : "", "aria-current": state.view === id ? "page" : undefined,
    onclick: async () => {
      state.view = id;
      if (id === "pages") await ctx.loadRules().catch((error) => say(why(error), "bad"));
      render();
      if (id === "scheduled") scheduledView.enter(ctx); // read SocialBu now, not on a timer
    },
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
    if (state.openPiece && state.view === "drafts") await loadDetail();
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
  const action = keyAction(event, { view: state.view, dialogOpen: dialogOpen(), letters: state.data?.letter_keys !== false });
  if (!action) return;
  if (action === "close") {
    if (dialogOpen()) return closeDialog();
    if (event.target?.blur && event.target !== document.body) event.target.blur();
    if (state.view === "drafts" && state.openPiece) ctx.close();
    return;
  }
  event.preventDefault();
  const list = drafts.draftList(ctx);
  if (action === "next" || action === "previous") {
    state.selected = moveIndex(state.selected, list.length, action === "next" ? 1 : -1);
    if (state.selected >= 0) await ctx.open(list[state.selected].piece_id);
    return;
  }
  if (action === "open") {
    const pick = list[Math.max(0, state.selected)];
    if (pick) await ctx.open(pick.piece_id);
    return;
  }
  if (!state.detail) return;
  if (action === "approve") await drafts.approve(ctx, { byKey: true });
  if (action === "edit") await drafts.edit(ctx);
  if (action === "send_back") await drafts.sendBack(ctx);
  if (action === "decline") await drafts.decline(ctx);
});

// --- start -------------------------------------------------------------------------------

let pending = null;
hq.on("posts.changed", () => {
  clearTimeout(pending);
  pending = setTimeout(redraw, 300);
});
hq.on("visible", () => redraw());

try {
  await load();
  if (!state.data.pages.length) {
    state.view = "pages";
    state.editingPage = "new";
  }
  render();
} catch (error) {
  fill(document.getElementById("main"), h("p", { class: "problem" }, why(error)),
    h("button", { type: "button", class: "btn", onclick: () => redraw() }, "Try again"));
}
