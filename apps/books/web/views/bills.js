// Bills and income: what is overdue, what is due soon, and the rest. Post puts it in
// the register (the amount can be changed then) and moves it to its next date; Skip
// moves it without posting. A reminder may be paid another way (no account: Post only
// marks it paid) or be a transfer between two accounts (Post makes both rows). One that
// "happens by itself" is posted by Books on or after its due date (see the Books guide);
// the screen says what was posted that way, and why one that was stopped is waiting.
import { ask, field, h } from "../_ui.js";
import { billAccount, dayWords, dueWords, reason } from "../lib/util.js";

const view = { adding: false, editing: null, posting: null };

const EVERY = [
  ["monthly", "Every month"], ["twice_monthly", "Twice a month"], ["weekly", "Every week"],
  ["biweekly", "Every two weeks"], ["months", "Every few months"], ["yearly", "Every year"], ["once", "Once"],
];

export async function render(ctx) {
  const list = await ctx.call("reminders", {});
  const groups = [
    ["Overdue", list.items.filter((i) => i.standing === "overdue")],
    ["Due today", list.items.filter((i) => i.standing === "today")],
    [`Due in the next ${list.window} days`, list.items.filter((i) => i.standing === "soon")],
    ["Later", list.items.filter((i) => i.standing === "later")],
  ];
  const out = [h("div", { class: "row" },
    h("button", { type: "button", class: "btn primary", onclick: () => { view.adding = true; view.editing = null; ctx.render(); } }, "Add a bill or income"))];
  if (view.adding) out.push(form(ctx, list, null));
  if (list.auto_recent.length) out.push(postedBox(list));
  if (!list.items.length && !view.adding) {
    out.push(h("div", { class: "empty" }, h("p", { class: "muted" }, "No reminders yet. Add the bills you pay and the income you expect, and Books lists what is due, puts it in the register when you press Post, and looks ahead for you.")));
  }
  for (const [title, items] of groups) {
    if (!items.length) continue;
    out.push(h("h2", { class: "section" }, title), table(ctx, list, items));
  }
  if (list.done.length) out.push(h("h2", { class: "section" }, "Done (came once)"), table(ctx, list, list.done));
  return h("div", {}, out);
}

/** What Books posted by itself lately, so a row in a register never appears unexplained. */
function postedBox(list) {
  const year = list.today.slice(0, 4);
  return h("div", { class: "form" }, h("h2", {}, "Posted by itself"),
    h("ul", { class: "rows" }, list.auto_recent.map((e) => h("li", { class: "row spread" },
      h("span", {}, `${e.name} `, h("span", { class: "muted small" },
        e.paid_other ? `for ${dayWords(e.due, year)}: marked paid, paid another way`
          : e.kind === "transfer" ? `for ${dayWords(e.due, year)}: moved from ${e.account_name} to ${e.to_account_name}`
          : `for ${dayWords(e.due, year)}: in ${e.account_name}'s register`),
      ), h("span", { class: "tag" }, `posted automatically ${dayWords(e.on, year)}`)))),
    h("p", { class: "hint" }, "These were put in by Books itself, without you pressing Post. They are in the register uncleared: change or delete one there if it was wrong."));
}

function table(ctx, list, items) {
  return h("div", { class: "sheet" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Due"), h("th", {}, "Name"), h("th", { class: "cat" }, "Account"),
      h("th", { class: "memo" }, "How often"), h("th", { class: "num" }, "Amount"), h("th", {}, ""))),
    h("tbody", {}, items.flatMap((item) => {
      const rows = [line(ctx, list, item)];
      if (view.posting === item.id) rows.push(h("tr", { class: "tools" }, h("td", { colspan: "6" }, postForm(ctx, item))));
      if (view.editing === item.id) rows.push(h("tr", {}, h("td", { colspan: "6" }, form(ctx, list, item))));
      return rows;
    }))));
}

function line(ctx, list, item) {
  const tone = item.standing === "overdue" ? "tag bad" : item.standing === "today" ? "tag warn" : "tag";
  const buttons = item.next_due ? [
    h("button", { type: "button", class: "btn small primary", onclick: () => { view.posting = item.id; view.editing = null; ctx.render(); } }, "Post"),
    h("button", { type: "button", class: "btn small", onclick: () => skip(ctx, item) }, "Skip"),
  ] : [];
  return h("tr", {},
    h("td", { class: "date" }, item.next_due ? item.next_due : "", item.next_due ? h("div", {}, h("span", { class: tone }, dueWords(item.next_due, list.today))) : null),
    h("td", { class: "payee" }, item.name, h("span", { class: item.kind === "income" ? "tag good" : "tag" }, item.kind),
      item.pays_itself ? h("span", { class: "tag good" }, "by itself") : null,
      item.payee && item.payee !== item.name ? h("div", { class: "muted small" }, item.payee) : null,
      item.note ? h("div", { class: "muted small" }, item.note) : null,
      item.auto_said ? h("div", { class: "problem small" }, item.auto_said) : null,
      item.auto && item.about ? h("div", { class: "muted small" }, "Marked as happening by itself, but its amount is only about this much, so Books does not post it: press Post.") : null,
      item.auto && !item.about && !item.pays_itself && !item.auto_said && !list.auto_on ? h("div", { class: "muted small" }, "Posting by itself is switched off in Books' settings, so press Post.") : null),
    h("td", { class: "cat" }, item.kind === "transfer" ? `${item.account_name} to ${item.to_account_name}` : item.account_name),
    h("td", { class: "memo" }, item.every_words),
    h("td", { class: "amount" }, item.about ? h("span", { class: "muted" }, "about ") : null, item.amount),
    h("td", { class: "num" }, h("div", { class: "row end" }, buttons,
      h("button", { type: "button", class: "btn small", onclick: () => { view.editing = item.id; view.posting = null; ctx.render(); } }, "Change"))));
}

function postForm(ctx, item) {
  if (item.paid_other) return paidForm(ctx, item);
  const date = h("input", { type: "date", value: item.next_due });
  const amount = h("input", { class: "num", value: item.amount, inputmode: "decimal" });
  const problem = h("span", { class: "problem small", role: "alert" });
  const go = h("button", { type: "button", class: "btn small primary" }, "Post it");
  async function post() {
    if (go.disabled) return;
    go.disabled = true;  // one press is one post
    problem.textContent = "";
    try {
      const done = await ctx.call("reminder.post", { id: item.id, due: item.next_due, date: date.value, amount: amount.value });
      ctx.say(done.said, "ok");
      view.posting = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
      go.disabled = false;
    }
  }
  go.addEventListener("click", post);
  for (const el of [date, amount]) el.addEventListener("keydown", (e) => { if (e.key === "Enter") post(); if (e.key === "Escape") { view.posting = null; ctx.render(); } });
  setTimeout(() => amount.focus(), 0);
  return h("div", { class: "row" },
    h("span", { class: "small" }, item.kind === "transfer" ? `Move money from ${item.account_name} to ${item.to_account_name}` : `Post ${item.name} into ${item.account_name}`),
    h("label", { class: "check-line small" }, "on ", date), h("label", { class: "check-line small" }, "amount ", amount),
    go, h("button", { type: "button", class: "btn small", onclick: () => { view.posting = null; ctx.render(); } }, "Cancel"), problem);
}

/** A reminder paid another way: marking it paid adds no row to any register. */
function paidForm(ctx, item) {
  const go = h("button", { type: "button", class: "btn small primary" }, "Mark it paid");
  const problem = h("span", { class: "problem small", role: "alert" });
  go.addEventListener("click", async () => {
    if (go.disabled) return;
    go.disabled = true;  // one press is one payment
    problem.textContent = "";
    try {
      ctx.say((await ctx.call("reminder.post", { id: item.id, due: item.next_due })).said, "ok");
      view.posting = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
      go.disabled = false;
    }
  });
  return h("div", { class: "row" },
    h("span", { class: "small" }, `${item.name} is paid another way, so no row goes in a register. Marking it paid moves it to its next date.`),
    go, h("button", { type: "button", class: "btn small", onclick: () => { view.posting = null; ctx.render(); } }, "Cancel"), problem);
}

async function skip(ctx, item) {
  if (!(await ask(`Skip ${item.name} for ${dayWords(item.next_due, ctx.state.data.today.slice(0, 4))}?`, "Nothing goes in the register, and it moves to its next date.", "Skip it"))) return;
  try {
    ctx.say((await ctx.call("reminder.skip", { id: item.id, due: item.next_due })).said, "ok");
    await ctx.refresh();
  } catch (error) {
    ctx.fail(error);
  }
}

function form(ctx, list, item) {
  const data = ctx.state.data;
  const accounts = ctx.openAccounts();
  if (!accounts.length) return h("p", { class: "problem" }, "Add an account first, under Accounts.");
  const name = h("input", { value: item?.name ?? "", maxlength: 80, placeholder: "Such as: Water bill" });
  const payee = h("input", { value: item?.payee ?? "", maxlength: 200, placeholder: "Who is paid, or who pays you" });
  const kind = h("select", {}, h("option", { value: "bill", selected: !item || item.kind === "bill" }, "A bill (money out)"), h("option", { value: "income", selected: item?.kind === "income" }, "Income (money in)"),
    h("option", { value: "transfer", selected: item?.kind === "transfer" }, "A transfer between my accounts"));
  const amount = h("input", { class: "num", value: item?.amount ?? "", inputmode: "decimal", placeholder: "0.00" });
  const about = h("input", { type: "checkbox", checked: Boolean(item?.about) });
  const auto = h("input", { type: "checkbox", checked: Boolean(item?.auto) && !item?.about });
  const note = h("input", { value: item?.note ?? "", maxlength: 500, placeholder: "Anything to remember about it" });
  // An amount that is only about right is not something to post by itself: one cancels the other.
  about.addEventListener("change", () => { if (about.checked) auto.checked = false; auto.disabled = about.checked; });
  auto.disabled = about.checked;
  // A new bill starts on the account the last bill used, else the first bank account; the account the register shows is not a hint.
  const start = billAccount(accounts, [...list.items, ...list.done]);
  const account = h("select", {}, accounts.map((a) => h("option", { value: String(a.id), selected: item ? a.id === item.account : a.id === start?.id }, a.name)),
    h("option", { value: "other", selected: Boolean(item?.paid_other) }, "Paid another way (an account Books does not track)"));
  const defaultTo = accounts.find((a) => a.id !== (item ? item.account : start?.id));
  const toAccount = h("select", {}, accounts.map((a) => h("option", { value: String(a.id), selected: a.id === (item?.to_account ?? defaultTo?.id) }, a.name)));
  const category = h("input", { value: item?.category ?? "", list: "bill-cats", maxlength: 80 });
  const cats = h("datalist", { id: "bill-cats" }, data.categories.map((c) => h("option", { value: c.name })));
  const every = h("select", {}, EVERY.map(([id, words]) => h("option", { value: id, selected: (item?.every ?? "monthly") === id }, words)));
  const due = h("input", { type: "date", value: item?.next_due ?? data.today });
  const day2 = h("input", { type: "number", min: "1", max: "31", value: item && item.every === "twice_monthly" ? String(item.day === Number(item.next_due?.slice(8)) ? item.day2 : item.day) : "", inputmode: "numeric" });
  const n = h("input", { type: "number", min: "1", max: "120", value: String(item?.n ?? 3), inputmode: "numeric" });
  const day2Field = field("Its other day of the month", day2, "Such as 15, with the 1st as the next due date.");
  const nField = field("Every how many months", n);
  const accountField = field("Account", account);
  const toField = field("Into account", toAccount);
  const categoryField = field("Category", category);
  const shape = () => {
    day2Field.hidden = every.value !== "twice_monthly";
    nField.hidden = every.value !== "months";
    const move = kind.value === "transfer";
    accountField.querySelector(".label").textContent = move ? "Out of account" : "Account";
    toField.hidden = !move;
    categoryField.hidden = move;
    // A transfer is always between two accounts Books keeps.
    const other = account.querySelector('option[value="other"]');
    other.hidden = move;
    other.disabled = move;
    if (move && account.value === "other") account.selectedIndex = 0;
  };
  every.addEventListener("change", shape);
  kind.addEventListener("change", shape);
  shape();
  const problem = h("p", { class: "problem", role: "alert" });
  const close = () => { view.adding = false; view.editing = null; ctx.render(); };
  async function save() {
    problem.textContent = "";
    const paidOther = kind.value !== "transfer" && account.value === "other";
    const args = { name: name.value, payee: payee.value, kind: kind.value, amount: amount.value, about: about.checked, auto: auto.checked && !about.checked, note: note.value,
      account: paidOther ? undefined : Number(account.value), paid_other: paidOther,
      to_account: kind.value === "transfer" ? Number(toAccount.value) : undefined,
      category: kind.value === "transfer" ? "" : category.value, every: every.value, next_due: due.value, day2: day2.value, n: n.value };
    try {
      const done = item ? await ctx.call("reminder.edit", { id: item.id, ...args }) : await ctx.call("reminder.add", args);
      ctx.say(done.said, "ok");
      view.adding = false;
      view.editing = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  async function remove() {
    if (!(await ask(`Delete ${item.name}?`, "The reminder is deleted for good. Rows it already put in the register stay there.", "Delete", "danger"))) return;
    try {
      ctx.say((await ctx.call("reminder.delete", { id: item.id })).said, "ok");
      view.editing = null;
      await ctx.refresh();
    } catch (error) {
      problem.textContent = reason(error);
    }
  }
  setTimeout(() => name.focus(), 0);
  return h("div", { class: "form" }, cats,
    h("h2", {}, item ? `Change ${item.name}` : "Add a bill or income"),
    h("div", { class: "row" }, field("Name", name), field("Payee", payee, "What the register row says. Left empty, the name is used.")),
    h("div", { class: "row" }, field("It is", kind), field("Amount", amount), h("label", { class: "check-line" }, about, "The amount is about this much")),
    h("div", { class: "row" }, accountField, toField, categoryField),
    h("div", { class: "row" }, field("How often", every), field("Next due", due), day2Field, nField),
    h("div", { class: "row" },
      h("label", { class: "check-line" }, auto, "This happens by itself (a direct debit, a salary)")),
    h("p", { class: "hint" }, list.auto_on
      ? "Books puts it in the register on its due date, or the first time it looks after, with the date it was due, as Post would, and notes it here. It never posts a date that has not come, and it needs an exact amount. If it has missed many dates it posts only some, then stops and tells you."
      : "Posting by itself is switched off in Books' settings, so this reminder waits for you to press Post until it is switched on."),
    h("div", { class: "row" }, field("Note", note)),
    problem,
    h("div", { class: "row spread" },
      h("div", { class: "row" }, item ? h("button", { type: "button", class: "btn danger", onclick: remove }, "Delete") : null),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: close }, "Cancel"),
        h("button", { type: "button", class: "btn primary", onclick: save }, item ? "Save" : "Add"))));
}
