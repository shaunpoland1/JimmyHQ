// Ahead: one account's balance day by day from today, with the bills and income due and
// the rows dated later, and its lowest point called out.
import { field, h } from "../_ui.js";
import { dayWords } from "../lib/util.js";

const view = { days: 0, every: false };

export async function render(ctx) {
  const acct = ctx.chosen();
  if (!acct) return h("p", { class: "muted pad" }, "Add an account first, under Accounts.");
  const days = view.days || ctx.state.data.ahead_days;
  const look = await ctx.call("ahead", { account: acct.id, days });
  const span = h("input", { type: "number", min: "1", max: "400", value: String(days), class: "num", inputmode: "numeric" });
  span.addEventListener("change", () => { view.days = Math.min(400, Math.max(0, Math.floor(Number(span.value)) || 0)); ctx.render(); });
  const every = h("input", { type: "checkbox", checked: view.every });
  every.addEventListener("change", () => { view.every = every.checked; ctx.render(); });
  const year = ctx.state.data.today.slice(0, 4);
  const shown = look.days.filter((d, i) => view.every || i === 0 || d.events.length || d.date === look.low.date);
  return h("div", {},
    h("div", { class: "bar" }, field("Account", ctx.accountPicker()), field("Days ahead", span),
      h("label", { class: "check-line" }, every, "Show every day")),
    h("div", { class: "sum" },
      h("div", {}, h("div", { class: "label" }, look.owed ? "Owed today" : "Today"), h("div", { class: `value ${look.start.neg ? "neg" : ""}` }, look.start.text)),
      h("div", {}, h("div", { class: "label" }, look.owed ? "Most owed" : "Lowest point"),
        h("div", { class: `value ${look.low.neg ? "neg" : ""}` }, look.low.text), h("div", { class: "small muted" }, dayWords(look.low.date, year))),
      h("div", {}, h("div", { class: "label" }, `In ${days} days`), h("div", { class: `value ${look.days.at(-1).neg ? "neg" : ""}` }, look.days.at(-1).text))),
    posted(look, year),
    h("div", { class: "sheet" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Day"), h("th", {}, "What happens"), h("th", { class: "num" }, look.owed ? "Owed" : "Balance"))),
      h("tbody", {}, shown.map((d) => h("tr", { class: d.date === look.low.date ? "low" : "" },
        h("td", { class: "date" }, dayWords(d.date, year)),
        h("td", { class: "payee" },
          d.events.map((e) => h("div", {}, `${e.name} `, h("span", { class: "muted" }, e.text))),
          d.events.length ? null : h("span", { class: "muted" }, d.date === look.days[0].date ? "Today" : "")),
        h("td", { class: `amount ${d.neg ? "neg" : ""}`.trim() }, d.text)))))),
    other(look, year),
    h("p", { class: "hint" }, "From today's balance, adding the rows dated later and every bill, income and transfer reminder for this account. An overdue reminder counts today. A reminder paid another way (cash, or a card Books does not keep) belongs to no account: it is listed once, under Paid another way, and never changes a balance."));
}

/** Reminders paid another way, once, in their own group (they belong to no account). */
function other(look, year) {
  const recent = look.auto_recent.filter((e) => e.paid_other);
  if (!look.other.length && !recent.length) return null;
  return h("div", {}, h("h2", { class: "section" }, "Paid another way"),
    recent.length ? h("p", { class: "small note" }, "Marked paid by itself lately: ",
      recent.map((e, i) => [i ? "; " : "", `${e.name} for ${dayWords(e.due, year)} (posted automatically ${dayWords(e.on, year)})`])) : null,
    look.other.length ? h("div", { class: "sheet" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Due"), h("th", {}, "What"), h("th", { class: "num" }, "Amount"))),
      h("tbody", {}, look.other.map((e) => h("tr", {},
        h("td", { class: "date" }, dayWords(e.date, year), e.date !== e.due ? h("div", { class: "small muted" }, `was due ${dayWords(e.due, year)}`) : null),
        h("td", { class: "payee" }, e.name, " ", h("span", { class: "muted small" }, "no change to any balance")),
        h("td", { class: "amount" }, e.text)))))) : null);
}

/** What Books posted by itself lately for this account. */
function posted(look, year) {
  const mine = look.auto_recent.filter((e) => !e.paid_other);
  if (!mine.length) return null;
  return h("p", { class: "small note" }, "Posted by itself lately, already in the balance above: ",
    mine.map((e, i) => [i ? "; " : "", `${e.name} for ${dayWords(e.due, year)} (posted automatically ${dayWords(e.on, year)})`]));
}
