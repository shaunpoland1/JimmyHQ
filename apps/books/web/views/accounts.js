// Accounts: each one's balance today and cleared balance, the total, and adding,
// changing, closing or deleting one.
import { ask, field, h } from "../_ui.js";
import { reason } from "../lib/util.js";

let adding = false;
let editing = null;

function money(told, owed) {
  return h("span", { class: told.neg ? "neg" : "" }, told.text, owed ? h("span", { class: "tag" }, "owed") : null);
}

export function render(ctx) {
  const data = ctx.state.data;
  const list = data.accounts;
  const open = list.filter((a) => !a.closed);
  const closed = list.filter((a) => a.closed);
  const out = [];
  if (!list.length) {
    out.push(h("div", { class: "banner" },
      h("h2", {}, "Start with one account"),
      h("p", {}, "Add the account you use most, such as your checking account, with its balance on a day you can check against a statement. You can bring in a bank's CSV file afterwards, or a whole Books file under Categories and files.")));
    adding = true;
  } else {
    out.push(h("div", { class: "facts" },
      h("span", {}, "What you have ", h("b", {}, data.total.have)),
      h("span", {}, "What you owe ", h("b", {}, data.total.owe)),
      h("span", {}, "Total ", h("b", { class: data.total.neg ? "neg" : "" }, data.total.text))));
    out.push(table(ctx, open));
  }
  out.push(adding ? form(ctx, null) : h("div", { class: "row" },
    h("button", { type: "button", class: "btn primary", onclick: () => { adding = true; editing = null; ctx.render(); } }, "Add an account")));
  if (closed.length) {
    out.push(h("h2", { class: "section" }, "Closed accounts"), table(ctx, closed));
  }
  return h("div", {}, out);
}

function table(ctx, rows) {
  return h("div", { class: "sheet" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, "Account"), h("th", { class: "wide-only" }, "Kind"),
      h("th", { class: "num" }, "Today"), h("th", { class: "num" }, "Cleared"), h("th", {}, ""))),
    h("tbody", {}, rows.flatMap((a) => {
      const line = h("tr", { class: "line", tabindex: "0", onclick: () => ctx.go("register", a.id),
        onkeydown: (e) => { if (e.key === "Enter") ctx.go("register", a.id); } },
      h("td", { class: "payee" }, a.name, a.later ? h("span", { class: "tag", title: `After the rows dated later: ${a.ending.text}` }, `later ${a.ending.text}`) : null),
      h("td", { class: "wide-only" }, a.kind_words),
      h("td", { class: "num" }, money(a.today, a.owed)),
      h("td", { class: "num" }, money(a.cleared, false)),
      h("td", { class: "num" }, h("button", { type: "button", class: "btn small", onclick: (e) => { e.stopPropagation(); editing = a.id; adding = false; ctx.render(); } }, "Change")));
      return editing === a.id ? [line, h("tr", {}, h("td", { colspan: "5" }, form(ctx, a)))] : [line];
    }))));
}

function form(ctx, acct) {
  const data = ctx.state.data;
  const name = h("input", { value: acct?.name ?? "", maxlength: 60, placeholder: "Such as: Example Bank checking" });
  const kind = h("select", {}, data.kinds.map((k) => h("option", { value: k.id, selected: (acct?.kind ?? "checking") === k.id }, k.words)));
  const opening = h("input", { class: "num", value: acct?.opening_text ?? "", inputmode: "decimal", placeholder: "0.00" });
  const date = h("input", { type: "date", value: acct?.opening_date ?? data.today });
  const owedWords = () => (["credit_card", "loan"].includes(kind.value) ? "Owed on that day" : "Balance on that day");
  const openingField = field(owedWords(), opening, "Type what you owe as a plain number, such as 250.00.");
  kind.addEventListener("change", () => { openingField.querySelector(".label").textContent = owedWords(); });
  const problem = h("p", { class: "problem", role: "alert" });
  const close = () => { adding = false; editing = null; ctx.render(); };
  async function save() {
    problem.textContent = "";
    try {
      const args = { name: name.value, kind: kind.value, opening: opening.value, opening_date: date.value };
      const done = acct ? await ctx.call("account.edit", { id: acct.id, ...args }) : await ctx.call("account.add", args);
      ctx.say(done.said, "ok");
      adding = false;
      editing = null;
      if (!acct) ctx.state.account = done.id;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  const keys = (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") close(); };
  for (const el of [name, opening, date]) el.addEventListener("keydown", keys);
  const more = acct ? [
    h("button", { type: "button", class: "btn", onclick: async () => {
      try {
        await ctx.call("account.edit", { id: acct.id, closed: !acct.closed });
        ctx.say(acct.closed ? `${acct.name} is open again.` : `${acct.name} is closed. Its history stays.`, "ok");
        editing = null;
        await ctx.refresh();
      } catch (error) { problem.textContent = reason(error); }
    } }, acct.closed ? "Open it again" : "Close it"),
    acct.rows === 0 ? h("button", { type: "button", class: "btn danger", onclick: async () => {
      if (!(await ask(`Delete ${acct.name}?`, "It has no rows, so only the account itself is lost.", "Delete", "danger"))) return;
      try {
        ctx.say((await ctx.call("account.delete", { id: acct.id })).said, "ok");
        editing = null;
        ctx.state.account = null;
        await ctx.refresh();
      } catch (error) { problem.textContent = reason(error); }
    } }, "Delete") : null,
  ] : [];
  const node = h("div", { class: "form" },
    h("h2", {}, acct ? `Change ${acct.name}` : "Add an account"),
    h("div", { class: "row" }, field("Name", name), field("Kind", kind)),
    h("div", { class: "row" }, openingField, field("On", date)),
    problem,
    h("div", { class: "row spread" },
      h("div", { class: "row" }, more),
      h("div", { class: "row" },
        ctx.state.data.accounts.length ? h("button", { type: "button", class: "btn", onclick: close }, "Cancel") : null,
        h("button", { type: "button", class: "btn primary", onclick: save }, acct ? "Save" : "Add account"))));
  setTimeout(() => name.focus(), 0);
  return node;
}
