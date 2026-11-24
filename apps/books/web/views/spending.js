// Spending: by category for a month, with the month before beside it, income apart.
// A category opens to show its rows. Transfers between your accounts are never counted.
import { field, h } from "../_ui.js";

const view = { month: "", open: null };

export async function render(ctx) {
  const month = view.month || ctx.state.data.today.slice(0, 7);
  const seen = await ctx.call("spending", { month });
  const picker = h("input", { type: "month", value: month });
  picker.addEventListener("change", () => { view.month = picker.value; view.open = null; ctx.render(); });
  const opened = view.open && view.open.month === month ? await ctx.call("spending.rows", { month, category: view.open.id, side: view.open.side }) : null;
  return h("div", {},
    h("div", { class: "bar" }, field("Month", picker)),
    block(ctx, "Spending", "spending", seen.spending, seen.spent, seen.spent_before, seen, opened),
    block(ctx, "Income", "income", seen.income, seen.earned, seen.earned_before, seen, opened),
    !seen.spending.length && !seen.income.length ? h("p", { class: "muted pad" }, "Nothing in this month yet. Rows with a category show up here.") : null);
}

function block(ctx, title, side, lines, total, before, seen, opened) {
  if (!lines.length) return null;
  return h("div", {}, h("h2", { class: "section" }, title),
    h("div", { class: "sheet" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Category"), h("th", { class: "num" }, seen.month), h("th", { class: "num" }, seen.before))),
      h("tbody", {}, lines.flatMap((line) => {
        const isOpen = view.open && view.open.id === line.id && view.open.side === side;
        const toggle = () => { view.open = isOpen ? null : { id: line.id, side, month: seen.month }; ctx.render(); };
        const rows = [h("tr", { class: "line", tabindex: "0", onclick: toggle, onkeydown: (e) => { if (e.key === "Enter") toggle(); } },
          h("td", { class: "payee" }, line.name), h("td", { class: "amount" }, line.text), h("td", { class: "amount muted" }, line.before))];
        if (isOpen && opened) rows.push(h("tr", { class: "tools" }, h("td", { colspan: "3" }, detail(ctx, opened))));
        return rows;
      }),
      h("tr", {}, h("td", {}, h("b", {}, "Total")), h("td", { class: "amount" }, h("b", {}, total)), h("td", { class: "amount muted" }, before))))));
}

function detail(ctx, opened) {
  if (!opened.rows.length) return h("p", { class: "muted small" }, "No rows this month.");
  return h("table", {}, h("tbody", {}, opened.rows.map((r) => h("tr", { class: "line", onclick: () => ctx.go("register", r.account_id) },
    h("td", { class: "date" }, r.date), h("td", { class: "payee" }, r.payee), h("td", { class: "cat" }, r.account),
    h("td", { class: "memo" }, r.memo), h("td", { class: "amount" }, r.amount)))));
}
