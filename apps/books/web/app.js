// The Books screen. One page: the views are drawn by script, so the frame never loads
// again. Everything it shows comes from Books' own program through hq.app.call; nothing
// is fetched from anywhere else, and every amount arrives already written out.
import { hq } from "./_bridge.js";
import { fill, h, say } from "./_ui.js";
import { reason } from "./lib/util.js";
import * as accounts from "./views/accounts.js";
import * as ahead from "./views/ahead.js";
import * as bills from "./views/bills.js";
import * as data from "./views/data.js";
import * as debts from "./views/debts.js";
import * as reconcile from "./views/reconcile.js";
import * as register from "./views/register.js";
import * as spending from "./views/spending.js";

const VIEWS = [
  ["register", "Register", register],
  ["accounts", "Accounts", accounts],
  ["bills", "Bills and income", bills],
  ["ahead", "Ahead", ahead],
  ["debts", "Debts", debts],
  ["reconcile", "Reconcile", reconcile],
  ["spending", "Spending", spending],
  ["data", "Categories and files", data],
];

const ready = await hq.ready;
const state = { view: "register", data: null, account: null, busy: false, quietUntil: 0 };

const ctx = {
  state,
  zone: ready.timezone || undefined,
  async call(name, args = {}) {
    try {
      // The bridge takes plain JSON only: a field left undefined is dropped here, not sent.
      return await hq.app.call(name, JSON.parse(JSON.stringify(args)));
    } finally {
      state.quietUntil = Date.now() + 1500;  // our own change: the view draws itself
    }
  },
  save: (file) => hq.ui.save(file),
  render,
  say,
  fail: (error) => say(reason(error), "bad"),
  go(view, account) {
    state.view = view;
    if (account !== undefined) state.account = account;
    render();
  },
  async refresh() {
    await load();
    render();
  },
  /** The open accounts, and the one chosen (the first open one when none is). */
  openAccounts() {
    return (state.data?.accounts ?? []).filter((a) => !a.closed);
  },
  chosen() {
    const all = state.data?.accounts ?? [];
    const found = all.find((a) => a.id === state.account);
    if (found) return found;
    const first = all.find((a) => !a.closed) ?? all[0] ?? null;
    state.account = first ? first.id : null;
    return first;
  },
  /** A picker of accounts that changes the chosen one. */
  accountPicker(onChange, { includeClosed = true } = {}) {
    const all = (state.data?.accounts ?? []).filter((a) => includeClosed || !a.closed);
    const current = ctx.chosen();
    const select = h("select", { "aria-label": "Account" },
      all.map((a) => h("option", { value: String(a.id), selected: current && a.id === current.id }, a.closed ? `${a.name} (closed)` : a.name)));
    select.addEventListener("change", () => {
      state.account = Number(select.value);
      onChange?.();
      render();
    });
    return select;
  },
};

async function load() {
  state.data = await ctx.call("overview", {});
  hq.ui.badge(state.data.due_now).catch(() => {});
}

function nav() {
  return VIEWS.map(([id, words]) => h("button", {
    type: "button", class: state.view === id ? "on" : "", "aria-current": state.view === id ? "page" : undefined,
    onclick: () => ctx.go(id),
  }, words, id === "bills" && state.data.due_now ? h("span", { class: "count" }, String(state.data.due_now)) : null));
}

function render() {
  if (!state.data) return;
  if (!state.data.accounts.length && !["accounts", "data", "debts"].includes(state.view)) state.view = "accounts";
  fill(document.getElementById("nav"), nav());
  const view = VIEWS.find(([id]) => id === state.view) ?? VIEWS[0];
  const main = document.getElementById("main");
  fill(main, h("h1", { class: "visually-hidden" }, view[1]));
  Promise.resolve(view[2].render(ctx)).then((node) => {
    if (state.view === view[0]) fill(main, h("h1", { class: "visually-hidden" }, view[1]), node);
  }).catch((error) => fill(main, h("p", { class: "problem" }, reason(error)),
    h("button", { type: "button", class: "btn", onclick: () => ctx.refresh() }, "Try again")));
}

function typing() {
  const el = document.activeElement;
  return Boolean(document.querySelector(".scrim")) || Boolean(el && document.getElementById("main").contains(el) && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName));
}

async function redraw() {
  try {
    await load();
    if (typing()) fill(document.getElementById("nav"), nav());  // do not take typing away
    else render();
  } catch (error) {
    ctx.fail(error);
  }
}

// Something changed elsewhere (another window, the job at a new day): draw again when
// nobody is in the middle of typing.
let pending = null;
hq.on("books.changed", () => {
  clearTimeout(pending);
  pending = setTimeout(() => { if (Date.now() > state.quietUntil) redraw(); }, 400);
});
hq.on("visible", () => redraw());
hq.on("settings.changed", () => redraw());

try {
  await load();
  if (!state.data.accounts.length) state.view = "accounts";
  render();
} catch (error) {
  fill(document.getElementById("main"), h("p", { class: "problem" }, reason(error)),
    h("button", { type: "button", class: "btn", onclick: () => redraw() }, "Try again"));
}
