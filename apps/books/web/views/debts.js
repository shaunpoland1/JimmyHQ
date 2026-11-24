// Debts: what is owed in total, each debt with its balance (a balance not known is shown
// as not known, never as zero) and a history of dated balances, and a rough payoff order
// that follows the owner's settings (which debt gets the extra money first, and how much
// extra each month). Everything shown, amounts and months, is worked out by the program.
import { ask, field, h } from "../_ui.js";
import { dayWords, monthWords, plural, reason } from "../lib/util.js";

const view = { adding: false, editing: null, balancing: null, history: null };

export async function render(ctx) {
  const list = await ctx.call("debts", {});
  const open = list.items.filter((i) => !i.closed);
  const shut = list.items.filter((i) => i.closed);
  const out = [summary(list), h("div", { class: "row" },
    h("button", { type: "button", class: "btn primary", onclick: () => { Object.assign(view, { adding: true, editing: null, balancing: null }); ctx.render(); } }, "Add a debt"),
    h("span", { class: "small muted" }, `Payoff order: ${list.order_words}. Extra each month: ${list.extra || "none"}. Both are in Books' settings.`))];
  if (list.problem) out.push(h("p", { class: "problem" }, list.problem));
  if (view.adding) out.push(form(ctx, null, list.kinds, list.today));
  if (!list.items.length && !view.adding) {
    out.push(h("div", { class: "empty" }, h("p", { class: "muted" }, "No debts yet. Add each card or loan you are paying off, with what you owe now, its rate and its minimum payment. Books keeps a dated history of each balance and works out roughly when each would be paid off.")));
  }
  // With more than one kind among the open debts, each kind has its own heading and total.
  if (list.groups.length > 1) {
    for (const g of list.groups) {
      out.push(h("h2", { class: "section" }, `${g.words}: ${plural(g.count, "debt")}, ${g.total} owed`,
        g.unknown ? h("span", { class: "small muted" }, ` (${g.unknown} not known)`) : null),
      table(ctx, list, open.filter((i) => i.kind === g.kind)));
    }
  } else if (open.length) out.push(table(ctx, list, open));
  if (list.has_open) out.push(order(list));
  if (shut.length) out.push(h("h2", { class: "section" }, "Closed"), table(ctx, list, shut));
  return h("div", {}, out);
}

function summary(list) {
  const unknown = list.total.unknown;
  return h("div", { class: "sum" },
    h("div", {}, h("div", { class: "label" }, "Owed in total"), h("div", { class: "value" }, list.total.text),
      unknown.length ? h("div", { class: "small note" }, `Not counting ${plural(unknown.length, "debt")} with no balance known: ${unknown.slice(0, 4).join(", ")}${unknown.length > 4 ? "…" : ""}`) : null),
    h("div", {}, h("div", { class: "label" }, "Minimums each month"), h("div", { class: "value" }, list.minimums)),
    h("div", {}, h("div", { class: "label" }, "Debt-free, roughly"),
      h("div", { class: "value" }, list.debt_free ? monthWords(list.debt_free) : "Not worked out"),
      list.stuck.length ? h("div", { class: "small note" }, `${list.stuck.join(", ")} would not be paid off at its payment.`) : null));
}

function payoff(item) {
  if (item.months != null && item.payoff_month) return `About ${monthWords(item.payoff_month)} (${plural(item.months, "month")})`;
  return item.payoff;
}

function table(ctx, list, items) {
  return h("div", { class: "sheet" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Debt"), h("th", { class: "num" }, "Owed"), h("th", { class: "num cat" }, "Rate"), h("th", { class: "num cat" }, "Minimum"),
      h("th", {}, "Paid off"), h("th", {}, ""))),
    h("tbody", {}, items.flatMap((item) => {
      const rows = [line(ctx, list, item)];
      if (view.balancing === item.id) rows.push(h("tr", { class: "tools" }, h("td", { colspan: "6" }, balanceForm(ctx, list, item))));
      if (view.history === item.id) rows.push(h("tr", { class: "tools" }, h("td", { colspan: "6" }, history(item))));
      if (view.editing === item.id) rows.push(h("tr", {}, h("td", { colspan: "6" }, form(ctx, item, list.kinds, list.today))));
      return rows;
    }))));
}

function line(ctx, list, item) {
  const year = list.today.slice(0, 4);
  const target = item.target_date ? h("div", { class: "small muted" }, `Target ${dayWords(item.target_date, year)} `,
    item.standing === "on_track" ? h("span", { class: "tag good" }, "on track") : item.standing === "behind" ? h("span", { class: "tag warn" }, "behind") : null) : null;
  return h("tr", {},
    h("td", { class: "payee" }, item.name, list.groups.length > 1 && !item.closed ? null : h("span", { class: "tag" }, item.kind_words),
      item.account_name ? h("div", { class: "small muted" }, `Linked to ${item.account_name}`) : null, item.note ? h("div", { class: "small muted" }, item.note) : null),
    h("td", { class: "amount" }, item.known
      ? [item.balance, h("div", { class: "small muted" }, item.paid ? `paid off, ${dayWords(item.as_of, year)}` : `on ${dayWords(item.as_of, year)}`)]
      : h("span", { class: "tag warn" }, "not known")),
    h("td", { class: "amount cat" }, item.rate ? `${item.rate}%` : h("span", { class: "muted" }, "not known")),
    h("td", { class: "amount cat" }, item.minimum || h("span", { class: "muted" }, "none")),
    h("td", {}, item.closed ? h("span", { class: "muted" }, "closed") : payoff(item), target),
    h("td", { class: "num" }, h("div", { class: "row end" },
      h("button", { type: "button", class: "btn small primary", onclick: () => { Object.assign(view, { balancing: view.balancing === item.id ? null : item.id, editing: null, adding: false }); ctx.render(); } }, "Update balance"),
      h("button", { type: "button", class: "btn small", onclick: () => { view.history = view.history === item.id ? null : item.id; ctx.render(); } }, "History"),
      h("button", { type: "button", class: "btn small", onclick: () => { Object.assign(view, { editing: item.id, balancing: null, adding: false }); ctx.render(); } }, "Change"))));
}

function history(item) {
  if (!item.history.length) return h("p", { class: "muted" }, "No balance has been noted yet. Use Update balance.");
  return h("div", {}, h("h3", {}, `Balances of ${item.name}`),
    h("ul", { class: "rows" }, item.history.map((e) => h("li", { class: "row spread" }, h("span", {}, dayWords(e.on, null), " ", h("span", { class: "muted small" }, e.on)), h("b", {}, e.text)))));
}

function balanceForm(ctx, list, item) {
  const amount = h("input", { class: "num", inputmode: "decimal", placeholder: "0.00", "aria-label": "What you owe" });
  const on = h("input", { type: "date", value: list.today, max: list.today });
  const problem = h("span", { class: "problem small", role: "alert" });
  const save = h("button", { type: "button", class: "btn small primary" }, "Save balance");
  async function send(args) {
    problem.textContent = "";
    save.disabled = true;  // one press is one balance
    try {
      ctx.say((await ctx.call("debt.balance", { id: item.id, on: on.value, ...args })).said, "ok");
      view.balancing = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
      save.disabled = false;
    }
  }
  save.addEventListener("click", () => send({ balance: amount.value }));
  for (const el of [amount, on]) el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send({ balance: amount.value });
    if (e.key === "Escape") { view.balancing = null; ctx.render(); }
  });
  setTimeout(() => amount.focus(), 0);
  return h("div", { class: "row" },
    h("span", { class: "small" }, `What you owe on ${item.name}`), amount, h("label", { class: "check-line small" }, "on ", on), save,
    item.can_take ? h("button", { type: "button", class: "btn small", onclick: () => send({ from_account: true }) }, `Take it from ${item.account_name}`) : null,
    h("button", { type: "button", class: "btn small", onclick: () => { view.balancing = null; ctx.render(); } }, "Cancel"), problem,
    h("span", { class: "hint" }, "Zero means paid off. The old balances stay in the history."));
}

function order(list) {
  const out = [h("h2", { class: "section" }, `Payoff order: ${list.order_words.toLowerCase()}`)];
  if (!list.steps.length) {
    out.push(h("p", { class: "muted" }, "Nothing to work out yet. A debt needs a balance above zero and a rate to be in the order."));
    return h("div", {}, out);
  }
  out.push(h("div", { class: "sheet" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Debt"), h("th", { class: "num" }, "Owed"), h("th", {}, "Paid off"), h("th", { class: "num cat" }, "Interest on the way"))),
    h("tbody", {}, list.steps.map((s) => h("tr", {},
      h("td", { class: "date" }, String(s.n)), h("td", { class: "payee" }, s.name, s.n === 1 ? h("span", { class: "tag good" }, "extra goes here") : null),
      h("td", { class: "amount" }, s.balance),
      h("td", {}, s.months != null ? `About ${monthWords(s.payoff_month)} (${plural(s.months, "month")})` : s.payoff),
      h("td", { class: "amount cat" }, s.interest)))))));
  out.push(h("p", { class: "hint" }, "Every debt is paid its minimum each month; the extra money goes to the first debt that is left, and when one is paid off its payment moves on to the next. Interest is worked out monthly from each rate, and the first payment is taken as next month. It is rough arithmetic, not advice: rates and payments change."));
  return h("div", {}, out);
}

function form(ctx, item, kinds, today) {
  const accounts = ctx.state.data.accounts;
  const name = h("input", { value: item?.name ?? "", maxlength: 80, placeholder: "Such as: Example Card" });
  const balance = h("input", { class: "num", inputmode: "decimal", placeholder: "Leave empty if you do not know yet", "aria-label": "Balance now" });
  const rate = h("input", { class: "num", value: item?.rate ?? "", inputmode: "decimal", placeholder: "Such as 19.99" });
  const minimum = h("input", { class: "num", value: item?.minimum ?? "", inputmode: "decimal", placeholder: "0.00" });
  const target = h("input", { type: "date", value: item?.target_date ?? "" });
  const account = h("select", {}, h("option", { value: "" }, "(none)"),
    accounts.map((a) => h("option", { value: String(a.id), selected: item?.account === a.id }, a.closed ? `${a.name} (closed)` : a.name)));
  const note = h("input", { value: item?.note ?? "", maxlength: 500 });
  const kind = h("select", {}, kinds.map((k) => h("option", { value: k.id, selected: (item?.kind ?? "other") === k.id }, k.words)));
  const passed = h("span", { class: "small note" });
  // A target date in the past is allowed, but said at once.
  const checkTarget = () => { passed.textContent = target.value && target.value < today ? "That date has passed. You can keep it, but there is nothing left to aim for." : ""; };
  target.addEventListener("input", checkTarget);
  target.addEventListener("change", checkTarget);
  checkTarget();
  const closed = h("input", { type: "checkbox", checked: Boolean(item?.closed) });
  const problem = h("p", { class: "problem", role: "alert" });
  const close = () => { view.adding = false; view.editing = null; ctx.render(); };
  async function save() {
    problem.textContent = "";
    const args = { name: name.value, rate: rate.value, minimum: minimum.value, target_date: target.value, kind: kind.value, account: account.value ? Number(account.value) : undefined, note: note.value };
    try {
      const done = item ? await ctx.call("debt.edit", { id: item.id, ...args, closed: closed.checked })
        : await ctx.call("debt.add", { ...args, balance: balance.value });
      ctx.say(done.said, "ok");
      close();
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  async function remove() {
    if (!(await ask(`Delete ${item.name}?`, "The debt and its whole history of balances are deleted for good. Rows in your registers are not touched.", "Delete", "danger"))) return;
    try {
      ctx.say((await ctx.call("debt.delete", { id: item.id })).said, "ok");
      view.editing = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  for (const el of [name, balance, rate, minimum, target, note]) el.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") close(); });
  setTimeout(() => name.focus(), 0);
  return h("div", { class: "form" },
    h("h2", {}, item ? `Change ${item.name}` : "Add a debt"),
    h("div", { class: "row" }, field("Name", name), field("Kind", kind), item ? null : field("What you owe now", balance, "A plain number, such as 1,200.50. Empty means not known: it is never counted as zero.")),
    h("div", { class: "row" }, field("Rate (percent a year)", rate, "Empty means not known."), field("Minimum payment each month", minimum), field("Pay it off by", target, "Optional. Books says whether the order below meets it."), passed),
    h("div", { class: "row" }, field("Linked account", account, "A card or loan you keep in Books. It lets you take the balance from the account."), field("Note", note)),
    item ? h("label", { class: "check-line" }, closed, "Closed: leave it out of the total and the order (its history is kept)") : null,
    problem,
    h("div", { class: "row spread" },
      h("div", { class: "row" }, item ? h("button", { type: "button", class: "btn danger", onclick: remove }, "Delete") : null),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: close }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: save }, item ? "Save" : "Add"))));
}
