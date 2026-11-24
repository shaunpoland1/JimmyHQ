// Reconcile: type the statement's end date and ending balance, tick the rows on the
// statement, and finish when the difference is exactly zero.
import { ask, field, h } from "../_ui.js";
import { reason, when } from "../lib/util.js";

const view = { account: null, through: "", statement: "", seen: null, problem: "" };

export async function render(ctx) {
  const acct = ctx.chosen();
  if (!acct) return h("p", { class: "muted pad" }, "Add an account first, under Accounts.");
  if (view.account !== acct.id) Object.assign(view, { account: acct.id, through: "", statement: "", seen: null, problem: "" });
  const start = view.seen ? null : await ctx.call("reconcile.start", { account: acct.id });
  const through = h("input", { type: "date", value: view.through || ctx.state.data.today });
  const statement = h("input", { class: "num", value: view.statement, inputmode: "decimal", placeholder: "0.00" });
  const problem = h("p", { class: "problem", role: "alert" }, view.problem);
  async function begin() {
    view.through = through.value;
    view.statement = statement.value;
    try {
      view.seen = await ctx.call("reconcile.view", { account: acct.id, through: view.through, statement: view.statement });
      view.problem = "";
    } catch (error) {
      view.problem = reason(error);
      view.seen = null;
    }
    ctx.render();
  }
  for (const el of [through, statement]) {
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") begin(); });
    if (view.seen) el.addEventListener("change", begin);
  }
  const top = h("div", { class: "bar" },
    field("Account", ctx.accountPicker()),
    field("Statement ends on", through),
    field(acct.owed ? "Statement says you owe" : "Statement ending balance", statement),
    view.seen ? null : h("button", { type: "button", class: "btn primary", onclick: begin }, "Start"));
  if (!view.seen) {
    setTimeout(() => statement.focus(), 0);
    return h("div", {}, top, problem,
      h("p", { class: "muted" }, "Have your statement to hand. Books lists every row up to its end date that is not reconciled yet; tick each one the statement shows."),
      start?.last ? h("p", { class: "small" }, `Last reconciled to ${start.last.through}, at ${start.last.statement}.`) : null,
      past(ctx, start?.history ?? []));
  }
  return h("div", {}, top, problem, working(ctx, acct));
}

function working(ctx, acct) {
  const seen = view.seen;
  const args = { account: acct.id, through: view.through, statement: view.statement };
  async function tick(row) {
    try {
      view.seen = await ctx.call("reconcile.tick", { ...args, id: row.id });
      ctx.render();
    } catch (error) {
      ctx.fail(error);
    }
  }
  async function finish() {
    if (!(await ask("Finish this reconcile?", `The ticked rows up to ${seen.through} are locked as reconciled. Changing one later asks first.`, "Finish"))) return;
    try {
      ctx.say((await ctx.call("reconcile.finish", args)).said, "ok");
      view.seen = null;
      view.statement = "";
      await ctx.refresh();
    } catch (error) {
      ctx.fail(error);
    }
  }
  const diff = seen.difference;
  return h("div", {},
    h("div", { class: "sum" },
      h("div", {}, h("div", { class: "label" }, acct.owed ? "Cleared, owed" : "Cleared balance"), h("div", { class: "value" }, seen.cleared.text)),
      h("div", {}, h("div", { class: "label" }, "Statement"), h("div", { class: "value" }, seen.statement.text)),
      h("div", {}, h("div", { class: "label" }, "Difference"), h("div", { class: `value ${diff.zero ? "zero" : "neg"}` }, diff.text))),
    h("div", { class: "row spread" },
      h("p", { class: "small muted" }, diff.zero ? "The difference is zero. You can finish." : "Tick the rows on your statement until the difference is zero."),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn", onclick: () => { view.seen = null; ctx.render(); } }, "Stop for now"),
        h("button", { type: "button", class: "btn primary", disabled: !seen.can_finish, onclick: finish }, "Finish"))),
    seen.rows.length ? h("div", { class: "sheet" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Date"), h("th", {}, "Payee"), h("th", { class: "cat" }, "Category"),
        h("th", { class: "num" }, acct.owed ? "Charge" : "Payment"), h("th", { class: "num" }, acct.owed ? "Payment" : "Deposit"))),
      h("tbody", {}, seen.rows.map((row) => {
        const box = h("input", { type: "checkbox", class: "tick", checked: row.ticked, "aria-label": `On the statement: ${row.date} ${row.payee}` });
        box.addEventListener("change", () => tick(row));
        return h("tr", { class: row.ticked ? "picked line" : "line", onclick: (e) => { if (e.target !== box) tick(row); } },
          h("td", { class: "clr" }, box), h("td", { class: "date" }, row.date), h("td", { class: "payee" }, row.payee),
          h("td", { class: "cat" }, row.category), h("td", { class: "amount" }, row.payment), h("td", { class: "amount" }, row.deposit));
      })))) : h("p", { class: "muted pad" }, "Every row up to that date is reconciled already."),
    past(ctx, seen.history));
}

function past(ctx, history) {
  if (!history.length) return null;
  return h("div", {}, h("h2", { class: "section" }, "Past reconciliations"),
    h("ul", { class: "rows" }, history.map((r) => h("li", {},
      h("span", {}, `To ${r.through}: ${r.statement}, ${r.rows} row${r.rows === 1 ? "" : "s"} locked`),
      h("span", { class: "muted small" }, `Finished ${when(r.done_ms, ctx.zone)}`)))));
}
