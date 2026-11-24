// The packages on one tab: a row each, with what to do to it.
import { carrierLine, eventLine, removeQuestion } from "../lib/lines.js";
import { emptyWords, forTab, registerAgain } from "../lib/rows.js";
import { checkedLine } from "../lib/times.js";
import { ask, field, h, say, why } from "../_ui.js";
import { carrierPicker } from "./form.js";

export function render(ctx) {
  const data = ctx.state.data;
  const rows = forTab(data.rows, ctx.state.view);
  if (!rows.length) return h("div", { class: "empty" }, h("p", { class: "muted" }, emptyWords(ctx.state.view, data.rows.length > 0)));
  return h("ul", { class: "packages" }, rows.map((row) => item(ctx, row)));
}

function tone(status) {
  if (status === "delivered") return "chip good";
  if (status === "problem" || status === "not_found" || status === "expired") return "chip warn";
  if (status === "out_for_delivery") return "chip hot";
  return "chip";
}

async function act(ctx, name, args, words) {
  try {
    await ctx.call(name, args);
    if (words) say(words, "ok");
    ctx.state.editing = null;
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

function item(ctx, row) {
  const data = ctx.state.data;
  const editing = ctx.state.editing?.id === row.id ? ctx.state.editing.what : null;
  const now = Date.now();
  const news = eventLine(row, ctx.zone);
  return h("li", {},
    h("div", { class: "row spread" },
      h("div", { class: "grow" },
        h("h3", {}, row.label, h("span", { class: tone(row.status) }, row.words)),
        news ? h("p", { class: "small" }, news) : null,
        h("p", { class: "muted small" }, `${carrierLine(row)} · ${checkedLine(row, now, data.tracker.outside, data.tracker.practice)}`),
        h("p", { class: "address" }, row.number),
        row.refused ? h("p", { class: "note small" }, row.refused) : row.note ? h("p", { class: "note small" }, row.note) : null,
        row.waiting_to_tell ? h("p", { class: "muted small" }, "Its card is waiting for room on your Bench.") : null),
      editing ? null : buttons(ctx, row)),
    editing === "rename" ? renameForm(ctx, row) : null,
    editing === "carrier" ? carrierForm(ctx, row) : null);
}

function buttons(ctx, row) {
  const hand = ctx.state.data.tracker.hand_statuses;
  const edit = (what) => () => { ctx.state.editing = { id: row.id, what }; ctx.render(); };
  const out = [
    h("button", { type: "button", class: "btn small", onclick: edit("rename") }, "Rename"),
    h("button", { type: "button", class: "btn small", onclick: edit("carrier") }, "Change carrier"),
  ];
  if (hand.length > 1) out.push(statusPicker(ctx, row));
  else if (hand.includes("delivered") && row.status !== "delivered") {
    out.push(h("button", { type: "button", class: "btn small", onclick: () => act(ctx, "package.mark", { id: row.id, status: "delivered" }, `${row.label} is marked delivered.`) }, "Mark delivered"));
  }
  if (row.status_by === "hand" && row.status === "delivered" && hand.length === 1) {
    out.push(h("button", { type: "button", class: "btn small", onclick: () => act(ctx, "package.unmark", { id: row.id }, `${row.label} is checked again.`) }, "Not delivered after all"));
  }
  const again = registerAgain(row);
  if (again) {
    out.push(h("button", { type: "button", class: `btn small ${again.kind}`.trim(), onclick: () => registerOnce(ctx, row, again.label) }, again.label));
  }
  out.push(h("button", { type: "button", class: "btn small danger", onclick: () => remove(ctx, row) }, "Remove"));
  return h("div", { class: "row end" }, out);
}

function statusPicker(ctx, row) {
  const select = h("select", { "aria-label": `Status of ${row.label}` }, ctx.state.data.statuses.map((s) => h("option", { value: s.id, selected: s.id === row.status }, s.words)));
  select.addEventListener("change", () => act(ctx, "package.mark", { id: row.id, status: select.value }, `${row.label} is marked ${select.selectedOptions[0].textContent.toLowerCase()}.`));
  return select;
}

function renameForm(ctx, row) {
  const name = h("input", { value: row.label, maxlength: 80 });
  const problem = h("p", { class: "problem", role: "alert" });
  return h("div", { class: "form" },
    field("Name", name),
    problem,
    h("div", { class: "row end" },
      h("button", { type: "button", class: "btn", onclick: () => { ctx.state.editing = null; ctx.render(); } }, "Cancel"),
      h("button", { type: "button", class: "btn primary", onclick: async () => {
        try {
          await ctx.call("package.rename", { id: row.id, label: name.value });
          ctx.state.editing = null;
          await ctx.refresh();
        } catch (error) {
          problem.textContent = why(error);
        }
      } }, "Save")));
}

function carrierForm(ctx, row) {
  const picker = carrierPicker(ctx, row);
  const problem = h("p", { class: "problem", role: "alert" });
  return h("div", { class: "form" },
    picker.node,
    problem,
    h("div", { class: "row end" },
      h("button", { type: "button", class: "btn", onclick: () => { ctx.state.editing = null; ctx.render(); } }, "Cancel"),
      h("button", { type: "button", class: "btn primary", onclick: async () => {
        try {
          const done = await ctx.call("package.carrier", { id: row.id, ...picker.read() });
          ctx.state.editing = null;
          say(done.said, "ok");
          await ctx.refresh();
        } catch (error) {
          problem.textContent = why(error);
        }
      } }, "Save")));
}

async function remove(ctx, row) {
  const { title, intro } = removeQuestion(row);
  if (!(await ask(title, intro, "Remove", "danger"))) return;
  try {
    const done = await ctx.call("package.remove", { id: row.id });
    say(done.said, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

async function registerOnce(ctx, row, label) {
  if (!(await ask("Register it again?", `${ctx.state.data.tracker.label} no longer knows this number. Registering it again uses another place in your quota.`, label))) return;
  try {
    const done = await ctx.call("package.register", { id: row.id });
    say(done.said, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}
