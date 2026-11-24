// The register: one account's rows, newest first, each with the balance after it. The
// top row enters a new one from the keyboard: Tab between the boxes, Enter saves, Esc
// clears, + and - move the date a day. A row opens for changing with a click or Enter.
import { ask, field, h } from "../_ui.js";
import { dayWords, knownPayee, reason, shiftDay } from "../lib/util.js";

const view = { account: null, q: "", limit: 0, editing: null, transfer: false, focus: "payee", last: null };

/** Send a change; when the row is reconciled, ask inside the page and send it again. */
export async function withConfirm(ctx, name, args, title = "Change a reconciled row?") {
  const first = await ctx.call(name, args);
  if (!first?.needs_confirm) return first;
  if (!(await ask(title, first.needs_confirm, "Change it anyway", "danger"))) return null;
  return ctx.call(name, { ...args, confirm: true });
}

function dateKeys(input) {
  input.addEventListener("keydown", (e) => {
    if (e.key === "+" || e.key === "=" || e.key === "-") {
      e.preventDefault();
      input.value = shiftDay(input.value, e.key === "-" ? -1 : 1);
    }
  });
}

export async function render(ctx) {
  const acct = ctx.chosen();
  if (!acct) return h("p", { class: "muted pad" }, "Add an account first, under Accounts.");
  if (view.account !== acct.id) Object.assign(view, { account: acct.id, q: "", limit: 0, editing: null, transfer: false });
  const reg = await ctx.call("register", { account: acct.id, q: view.q, limit: view.limit || undefined });
  view.last = reg;
  const node = h("div", {}, header(ctx, reg), view.transfer ? transferForm(ctx, reg) : null, sheet(ctx, reg),
    reg.more ? h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: () => { view.limit = reg.rows.length + 500; ctx.render(); } }, `Show older rows (${reg.more} more)`)) : null,
    !reg.found && view.q ? h("p", { class: "muted pad" }, `No row matches "${view.q}".`) : null,
    !reg.found && !view.q ? h("p", { class: "muted pad" }, "No rows yet. Type the first one in the top row: a date, who was paid, and the amount. Press Enter to save it.") : null,
    h("p", { class: "hint wide-only" }, "Keys: Enter saves, Esc clears, + and - move the date a day. Type ", h("kbd", {}, "Transfer: "), " and an account's name as the category to move money between your accounts."));
  setTimeout(() => {
    const el = node.querySelector(`[data-focus="${view.focus}"]`);
    if (el && view.focus) {
      el.focus();
      if (typeof el.setSelectionRange === "function" && el.type === "search") el.setSelectionRange(el.value.length, el.value.length);
    }
    view.focus = null;
  }, 0);
  return node;
}

function header(ctx, reg) {
  const a = reg.account;
  const search = h("input", { type: "search", value: view.q, placeholder: "Payee, memo, number or amount", "aria-label": "Search", "data-focus": "search" });
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { view.q = search.value; view.limit = 0; view.focus = "search"; ctx.render(); }, 350);
  });
  search.addEventListener("keydown", (e) => { if (e.key === "Escape") { view.q = ""; view.focus = "payee"; ctx.render(); } });
  const told = (t) => h("b", { class: t.neg ? "neg" : "" }, t.text);
  return h("div", {},
    h("div", { class: "bar" },
      field("Account", ctx.accountPicker()),
      field("Search", search),
      h("button", { type: "button", class: "btn", onclick: () => { view.transfer = !view.transfer; ctx.render(); } }, view.transfer ? "Close transfer" : "Transfer")),
    h("div", { class: "facts" },
      h("span", {}, a.owed ? "Owed today " : "Today ", told(a.today)),
      h("span", {}, "Cleared ", told(a.cleared)),
      a.later ? h("span", {}, "After rows dated later ", told(a.ending)) : null,
      a.closed ? h("span", { class: "tag warn" }, "closed") : null,
      view.q ? h("span", {}, `${reg.found} found`) : null));
}

function columns(a) {
  return a.owed ? ["Charge", "Payment"] : ["Payment", "Deposit"];
}

function sheet(ctx, reg) {
  const [out, inn] = columns(reg.account);
  const lists = h("div", { hidden: true },
    h("datalist", { id: "payee-list" }, reg.payees.map((p) => h("option", { value: p.name }))),
    h("datalist", { id: "cat-list" }, [...reg.categories, ...reg.transfers].map((c) => h("option", { value: c }))));
  return h("div", { class: "sheet reg" }, lists, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, "Date"), h("th", { class: "no", title: "Check number" }, "No."), h("th", {}, "Payee"), h("th", { class: "cat" }, "Category"), h("th", { class: "memo" }, "Memo"),
      h("th", { class: "num" }, out), h("th", { class: "num" }, inn), h("th", { title: "Cleared" }, "Clr"), h("th", { class: "num" }, "Balance"))),
    h("tbody", {},
      reg.account.closed ? null : entryRow(ctx, reg),
      reg.rows.flatMap((row) => (view.editing === row.id ? editRows(ctx, reg, row) : [line(ctx, row)])))));
}

function boxes(ctx, reg, row) {
  const date = h("input", { type: "date", class: "w-date", value: row?.date ?? reg.last_date, "aria-label": "Date", "data-focus": row ? "edit-date" : "date" });
  dateKeys(date);
  const number = h("input", { class: "w-no", value: row?.number ?? "", maxlength: 20, autocomplete: "off", "aria-label": "Check number", placeholder: row ? "" : "No." });
  const payee = h("input", { value: row ? row.payee_typed : "", list: "payee-list", autocomplete: "off", maxlength: 200, "aria-label": "Payee", placeholder: row ? "" : "Payee", "data-focus": row ? "edit-payee" : "payee" });
  const category = h("input", { value: row?.category ?? "", list: "cat-list", autocomplete: "off", maxlength: 80, "aria-label": "Category", placeholder: row ? "" : "Category" });
  const memo = h("input", { value: row?.memo ?? "", maxlength: 500, "aria-label": "Memo", placeholder: row ? "" : "Memo" });
  const payment = h("input", { class: "num w-num", value: row?.payment ?? "", inputmode: "decimal", autocomplete: "off", "aria-label": columns(reg.account)[0] });
  const deposit = h("input", { class: "num w-num", value: row?.deposit ?? "", inputmode: "decimal", autocomplete: "off", "aria-label": columns(reg.account)[1] });
  const touched = { category: Boolean(row), amount: Boolean(row) };
  category.addEventListener("input", () => { touched.category = true; });
  payment.addEventListener("input", () => { touched.amount = true; });
  deposit.addEventListener("input", () => { touched.amount = true; });
  // Choosing a payee used before fills in its category and amount from last time.
  payee.addEventListener("change", () => {
    if (row) return;
    const known = knownPayee(reg.payees, payee.value);
    if (!known) return;
    if (!touched.category) category.value = known.category;
    if (!touched.amount) { payment.value = known.payment; deposit.value = known.deposit; }
  });
  return { date, number, payee, category, memo, payment, deposit,
    // Esc clears the boxes: a payee chosen after that fills in category and amount again.
    reset: () => { touched.category = false; touched.amount = false; },
    values: () => ({ date: date.value, number: number.value, payee: payee.value, category: category.value, memo: memo.value, payment: payment.value, deposit: deposit.value }),
    all: [date, number, payee, category, memo, payment, deposit] };
}

function entryRow(ctx, reg) {
  const b = boxes(ctx, reg, null);
  const problem = h("span", { class: "problem small", role: "alert" });
  const tools = h("tr", { class: "tools", hidden: true }, h("td", { colspan: "9" }, problem));
  const tell = (text) => { problem.textContent = text; tools.hidden = !text; };
  let sending = false;
  async function save() {
    if (sending) return;
    sending = true;
    tell("");
    try {
      const done = await ctx.call("txn.add", { account: reg.account.id, ...b.values() });
      ctx.say(done.said, "ok");
      view.focus = "payee";
      await ctx.refresh();
    } catch (error) {
      tell(reason(error));
      sending = false;
    }
  }
  function clear() {
    for (const el of [b.number, b.payee, b.category, b.memo, b.payment, b.deposit]) el.value = "";
    b.reset();
    tell("");
    b.payee.focus();
  }
  for (const el of b.all) el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    if (e.key === "Escape") { e.preventDefault(); clear(); }
  });
  return [
    h("tr", { class: "entry" },
      h("td", {}, b.date), h("td", { class: "no" }, b.number), h("td", {}, b.payee), h("td", { class: "cat" }, b.category), h("td", { class: "memo" }, b.memo),
      h("td", {}, b.payment), h("td", {}, b.deposit), h("td", {}),
      h("td", { class: "num" }, h("button", { type: "button", class: "btn small primary", onclick: save }, "Add"))),
    tools,
  ];
}

function mark(ctx, row) {
  const words = { "": "Not cleared", c: "Cleared", R: "Reconciled" }[row.state] ?? "";
  return h("button", { type: "button", class: `clear-mark ${row.state}`.trim(), title: words, "aria-label": `${words}. Press to change.`,
    onclick: async (e) => {
      e.stopPropagation();
      try {
        const done = await withConfirm(ctx, "txn.clear", { id: row.id }, "Take the reconciled mark off?");
        if (done) await ctx.refresh();
      } catch (error) { ctx.fail(error); }
    } }, row.state === "c" ? "c" : row.state === "R" ? "R" : "");
}

function line(ctx, row) {
  const open = () => { view.editing = row.id; view.focus = "edit-payee"; ctx.render(); };
  const tags = [
    row.transfer ? h("span", { class: "tag" }, "transfer") : null,
    row.from_reminder ? h("span", { class: "tag" }, "reminder") : null,
  ];
  return h("tr", { class: "line", tabindex: "0", onclick: open, onkeydown: (e) => { if (e.key === "Enter") open(); } },
    h("td", { class: "date", title: dayWords(row.date, null) }, row.date),
    h("td", { class: "no" }, row.number),
    h("td", { class: "payee" }, row.payee || h("span", { class: "muted" }, "(no payee)"), tags),
    h("td", { class: "cat" }, row.category),
    h("td", { class: "memo" }, row.memo),
    h("td", { class: "amount" }, row.payment),
    h("td", { class: "amount" }, row.deposit),
    h("td", { class: "clr" }, mark(ctx, row)),
    h("td", { class: `amount ${row.neg ? "neg" : ""}`.trim() }, row.balance));
}

function editRows(ctx, reg, row) {
  const b = boxes(ctx, reg, row);
  const move = h("select", { "aria-label": "Account" }, ctx.openAccounts().map((a) => h("option", { value: String(a.id), selected: a.id === reg.account.id }, a.name)));
  const problem = h("span", { class: "problem small", role: "alert" });
  const cancel = () => { view.editing = null; ctx.render(); };
  async function save() {
    problem.textContent = "";
    try {
      const args = { id: row.id, ...b.values() };
      if (Number(move.value) !== reg.account.id) args.account = Number(move.value);
      const done = await withConfirm(ctx, "txn.edit", args);
      if (!done) return;
      ctx.say(done.said, "ok");
      view.editing = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  async function remove() {
    const both = row.transfer ? " Both sides of the transfer are deleted." : "";
    const locked = row.state === "R" ? " It is reconciled, so that reconciliation will no longer balance." : "";
    if (!(await ask("Delete this row?", `${row.date}, ${row.payee || "no payee"}, ${row.payment || row.deposit}. It is deleted for good.${both}${locked}`, "Delete", "danger"))) return;
    try {
      const done = await ctx.call("txn.delete", { id: row.id, confirm: row.state === "R" });
      ctx.say(done.said, "ok");
      view.editing = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  for (const el of b.all) el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    if (e.key === "Escape") { e.preventDefault(); cancel(); }
  });
  return [
    h("tr", { class: "editing" },
      h("td", {}, b.date), h("td", { class: "no" }, b.number), h("td", {}, b.payee), h("td", { class: "cat" }, b.category), h("td", { class: "memo" }, b.memo),
      h("td", {}, b.payment), h("td", {}, b.deposit), h("td", { class: "clr" }, mark(ctx, row)),
      h("td", { class: `amount ${row.neg ? "neg" : ""}`.trim() }, row.balance)),
    h("tr", { class: "tools" }, h("td", { colspan: "9" },
      h("div", { class: "row spread" },
        h("div", { class: "row" }, h("label", { class: "check-line small" }, "Account ", move), problem,
          row.state === "R" ? h("span", { class: "tag" }, "reconciled") : null,
          row.transfer ? h("span", { class: "hint" }, "A change to the date or amount is made to the other side too.") : null),
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn small danger", onclick: remove }, "Delete"),
          h("button", { type: "button", class: "btn small", onclick: cancel }, "Cancel"),
          h("button", { type: "button", class: "btn small primary", onclick: save }, "Save"))))),
  ];
}

function transferForm(ctx, reg) {
  const others = ctx.openAccounts().filter((a) => a.id !== reg.account.id);
  if (!others.length) return h("div", { class: "form" }, h("p", {}, "Add a second account under Accounts to move money between them."));
  const to = h("select", {}, others.map((a) => h("option", { value: String(a.id) }, a.name)));
  const way = h("select", {}, h("option", { value: "out" }, `Out of ${reg.account.name}, into`), h("option", { value: "in" }, `Into ${reg.account.name}, out of`));
  const date = h("input", { type: "date", value: reg.last_date });
  dateKeys(date);
  const amount = h("input", { class: "num", inputmode: "decimal", placeholder: "0.00", "data-focus": "transfer" });
  const memo = h("input", { maxlength: 500 });
  const problem = h("p", { class: "problem", role: "alert" });
  async function send() {
    problem.textContent = "";
    const pair = way.value === "out" ? { from: reg.account.id, to: Number(to.value) } : { from: Number(to.value), to: reg.account.id };
    try {
      const done = await ctx.call("transfer.add", { ...pair, date: date.value, amount: amount.value, memo: memo.value });
      ctx.say(done.said, "ok");
      view.transfer = false;
      view.focus = "payee";
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  for (const el of [date, amount, memo]) el.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
  view.focus = "transfer";
  return h("div", { class: "form" },
    h("h2", {}, "Move money between your accounts"),
    h("div", { class: "row" }, field("Direction", way), field("Account", to), field("Date", date), field("Amount", amount), field("Memo", memo)),
    problem,
    h("p", { class: "hint" }, "This makes two rows, one in each register. Changing the date or amount of one changes the other, and deleting one deletes both. A transfer is never counted as spending."),
    h("div", { class: "row end" }, h("button", { type: "button", class: "btn primary", onclick: send }, "Move money")));
}
