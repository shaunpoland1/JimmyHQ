// Categories and files: the category list, bringing in a bank's CSV file, saving a
// register or everything as a file, and bringing a whole Books file back.
import { ask, field, h } from "../_ui.js";
import { saveWhole, tooLargeWords } from "../lib/saving.js";
import { pieces, plural, reason, when } from "../lib/util.js";

const FIELDS = [
  ["date", "Date"], ["payee", "Payee"], ["amount", "Amount (one column, money in above zero)"],
  ["payment", "Money out"], ["deposit", "Money in"], ["memo", "Memo"], ["category", "Category"], ["number", "Check number"],
];
const FORMATS = [["", "Detect them"], ["ymd", "2026-03-04"], ["mdy", "3/4/2026 is March 4"], ["dmy", "3/4/2026 is 3 April"]];

const view = { file: null, token: null, name: "", preview: null, mapping: null, format: "", cleared: true, problem: "", ready: null };

/** The sentence for a file over the owner's ceiling (the setting "Largest file to bring in"), or "". */
export function tooBig(file, limit) {
  if (!limit || file.size <= limit) return "";
  return `That file is ${Math.round(file.size / 1_000_000 * 10) / 10} MB, and Books is set to bring in at most ${Math.round(limit / 1_000_000)} MB. Use a shorter period, or raise "Largest file to bring in (MB)" in Books' settings.`;
}

/** Hand a file's text to the program in pieces. Returns the token that names it. Each piece
 *  is its own small call, so a big file never makes one long call; the status line counts. */
async function send(ctx, text) {
  const { token, part_chars: size } = await ctx.call("upload.start", {});
  const parts = pieces(text, size);
  for (let i = 0; i < parts.length; i += 1) {
    if (parts.length > 1) ctx.say(`Sending the file: part ${i + 1} of ${parts.length}…`);
    await ctx.call("upload.part", { token, part: i, text: parts[i] });
  }
  return token;
}

/** Fetch a file the program made, in pieces, and hand it to the owner to keep. The whole file
 *  is offered as one: the platform's limit is the owner's setting, and only its refusal is
 *  taken as the limit (lib/saving.js). */
async function exportFile(ctx, args) {
  const first = await ctx.call("export.part", { ...args, part: 0 });
  let text = first.text;
  for (let part = 1; part < first.parts; part += 1) text += (await ctx.call("export.part", { token: first.token, part })).text;
  const file = { name: first.name, type: first.type, text };
  try {
    const done = await saveWhole(file, (f) => ctx.save(f));
    if (done.tooLarge) {
      ctx.say(tooLargeWords(first.bytes, args.what), "bad");
      return;
    }
    ctx.say(`Saved as ${done.saved.name}.${done.packed ? " It was packed onto one line to fit the largest file an app may offer you to keep." : ""}`, "ok");
  } catch (error) {
    view.ready = file;  // a slow export loses the click: one more press saves it
    ctx.say(reason(error), "bad");
    ctx.render();
  }
}

export function render(ctx) {
  return h("div", {}, importer(ctx), files(ctx), categories(ctx));
}

// --- categories ------------------------------------------------------------------------------

function categories(ctx) {
  const cats = ctx.state.data.categories;
  const name = h("input", { maxlength: 60, placeholder: "New category" });
  const income = h("input", { type: "checkbox" });
  const problem = h("p", { class: "problem", role: "alert" });
  async function add() {
    try {
      ctx.say((await ctx.call("category.add", { name: name.value, income: income.checked })).said, "ok");
      await ctx.refresh();
    } catch (error) { problem.textContent = reason(error); }
  }
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
  return h("div", {}, h("h2", { class: "section" }, "Categories"),
    h("div", { class: "row" }, name, h("label", { class: "check-line" }, income, "Income"), h("button", { type: "button", class: "btn", onclick: add }, "Add")),
    problem,
    h("ul", { class: "cats" }, cats.map((c) => category(ctx, c, cats))));
}

function category(ctx, cat, cats) {
  const name = h("input", { value: cat.name, maxlength: 60, "aria-label": `Name of ${cat.name}` });
  const into = h("select", { "aria-label": `Merge ${cat.name} into` }, h("option", { value: "" }, "Merge into…"),
    cats.filter((c) => c.id !== cat.id).map((c) => h("option", { value: String(c.id) }, c.name)));
  async function rename() {
    if (name.value.trim() === cat.name) return;
    try {
      ctx.say((await ctx.call("category.rename", { id: cat.id, name: name.value })).said, "ok");
      await ctx.refresh();
    } catch (error) { ctx.fail(error); }
  }
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") rename(); });
  into.addEventListener("change", async () => {
    if (!into.value) return;
    const target = cats.find((c) => String(c.id) === into.value);
    if (!(await ask(`Merge ${cat.name} into ${target.name}?`, `Every row and reminder in ${cat.name} moves to ${target.name}, and ${cat.name} is gone from the list.`, "Merge", "danger"))) {
      into.value = "";
      return;
    }
    try {
      ctx.say((await ctx.call("category.merge", { id: cat.id, into: target.id })).said, "ok");
      await ctx.refresh();
    } catch (error) { ctx.fail(error); }
  });
  return h("li", {}, h("div", { class: "row" }, name, cat.income ? h("span", { class: "tag good" }, "income") : null,
    h("button", { type: "button", class: "btn small", onclick: rename }, "Rename")), into);
}

// --- a bank's CSV file ------------------------------------------------------------------------

function importer(ctx) {
  const accounts = ctx.openAccounts();
  const wrap = h("div", {}, h("h2", { class: "section" }, "Bring in a bank's CSV file"));
  if (!accounts.length) return h("div", {}, wrap, h("p", { class: "muted" }, "Add an account first, under Accounts."));
  const acct = accounts.find((a) => a.id === ctx.chosen()?.id) ?? accounts[0];
  const input = h("input", { type: "file", accept: ".csv,text/csv,text/plain" });
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    const big = tooBig(file, ctx.state.data.import_bytes);
    if (big) { view.problem = big; ctx.render(); return; }
    try {
      view.token = await send(ctx, await file.text());
      ctx.say("");
      Object.assign(view, { name: file.name, mapping: null, format: "", problem: "" });
      await preview(ctx, acct);
    } catch (error) {
      view.problem = reason(error);
      ctx.render();
    }
  });
  wrap.append(h("div", { class: "bar" }, field("Into", ctx.accountPicker(() => { view.preview = null; view.token = null; }, { includeClosed: false })), field("File", input)));
  if (view.problem) wrap.append(h("p", { class: "problem" }, view.problem));
  if (view.preview && view.token) wrap.append(previewBlock(ctx, acct));
  wrap.append(pastImports(ctx, acct));
  return wrap;
}

async function preview(ctx, acct) {
  try {
    view.preview = await ctx.call("import.preview", { account: acct.id, token: view.token, mapping: view.mapping ?? undefined, date_format: view.format || undefined });
    view.mapping = view.preview.mapping;
    view.problem = "";
  } catch (error) {
    view.problem = reason(error);
    view.preview = null;
  }
  ctx.render();
}

function previewBlock(ctx, acct) {
  const p = view.preview;
  const choose = (key) => {
    const select = h("select", {}, h("option", { value: "" }, "(none)"),
      p.header.map((name, i) => h("option", { value: String(i), selected: p.mapping[key] === i }, name || `Column ${i + 1}`)));
    select.addEventListener("change", () => {
      view.mapping = { ...view.mapping, [key]: select.value === "" ? null : Number(select.value) };
      if (key === "amount" && select.value !== "") Object.assign(view.mapping, { payment: null, deposit: null });
      if ((key === "payment" || key === "deposit") && select.value !== "") view.mapping.amount = null;
      preview(ctx, acct);
    });
    return select;
  };
  const flip = h("input", { type: "checkbox", checked: Boolean(p.mapping.flip) });
  flip.addEventListener("change", () => { view.mapping = { ...view.mapping, flip: flip.checked }; preview(ctx, acct); });
  const format = h("select", {}, FORMATS.map(([id, words]) => h("option", { value: id, selected: (view.format || "") === id }, words)));
  format.addEventListener("change", () => { view.format = format.value; preview(ctx, acct); });
  const cleared = h("input", { type: "checkbox", checked: view.cleared });
  cleared.addEventListener("change", () => { view.cleared = cleared.checked; });
  const go = h("button", { type: "button", class: "btn primary", disabled: Boolean(p.problem) || !p.new }, p.new ? `Import ${plural(p.new, "row")}` : "Nothing new to import");
  go.addEventListener("click", async () => {
    go.disabled = true;
    try {
      const done = await ctx.call("import.commit", { account: acct.id, token: view.token, mapping: view.mapping, date_format: view.format || undefined, cleared: view.cleared, name: view.name });
      ctx.say(done.said, "ok");
      Object.assign(view, { preview: null, token: null, mapping: null, format: "" });
      await ctx.refresh();
    } catch (error) {
      view.problem = reason(error);
      ctx.render();
    }
  });
  const how = { guessed: "Books guessed which column is which from the header. Change any of them.", remembered: "These are the columns you chose last time for this account.", yours: "" }[p.how] ?? "";
  return h("div", { class: "form" },
    h("h2", {}, view.name || "Your file"),
    how ? h("p", { class: "hint" }, how) : null,
    h("div", { class: "mapping" }, FIELDS.map(([key, words]) => field(words, choose(key)))),
    h("div", { class: "row" },
      h("label", { class: "check-line" }, flip, "Purchases are written above zero (as many card files do)"),
      field("Dates", format),
      h("label", { class: "check-line" }, cleared, "Mark the new rows as cleared: the bank has seen them")),
    p.problem ? h("p", { class: "problem" }, p.problem) : null,
    h("p", { class: "small" }, `${plural(p.lines, "line")} in the file: ${p.new} new, ${p.duplicates} already in ${acct.name} (skipped)${p.before_opening ? `, ${p.before_opening} before the account's opening date (left out)` : ""}${p.unread_count ? `, ${p.unread_count} that cannot be read` : ""}.`),
    p.numbers_cut ? h("p", { class: "small note" }, `${plural(p.numbers_cut, "check number")} ${p.numbers_cut === 1 ? "is" : "are"} longer than 20 characters and will be cut to 20.`) : null,
    p.unread.length ? h("ul", { class: "small problem" }, p.unread.slice(0, 5).map((u) => h("li", {}, u))) : null,
    p.rows.length ? h("div", { class: "sheet preview" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", {}, "No."), h("th", {}, "Payee"), h("th", { class: "memo" }, "Memo"), h("th", { class: "cat" }, "Category"), h("th", { class: "num" }, "Out"), h("th", { class: "num" }, "In"), h("th", {}, ""))),
      h("tbody", {}, p.rows.map((r) => h("tr", { class: r.duplicate ? "later" : "" },
        h("td", { class: "date" }, r.date), h("td", {}, r.number), h("td", { class: "payee" }, r.payee), h("td", { class: "memo" }, r.memo), h("td", { class: "cat" }, r.category),
        h("td", { class: "amount" }, r.payment), h("td", { class: "amount" }, r.deposit),
        h("td", {}, r.problem ? h("span", { class: "tag bad" }, "cannot read") : r.duplicate ? h("span", { class: "tag" }, "already there") : r.before_opening ? h("span", { class: "tag" }, "before the opening date") : null)))))) : null,
    h("p", { class: "hint" }, "Nothing is saved until you press Import. The first rows are shown here."),
    h("div", { class: "row end" },
      h("button", { type: "button", class: "btn", onclick: () => { Object.assign(view, { preview: null, token: null }); ctx.render(); } }, "Cancel"), go));
}

function pastImports(ctx, acct) {
  const box = h("div", {});
  ctx.call("import.list", { account: acct.id }).then(({ imports }) => {
    const live = imports.filter((b) => !b.undone);
    if (!live.length) return;
    box.append(h("h3", { class: "section" }, `Past imports into ${acct.name}`),
      h("ul", { class: "rows" }, live.slice(0, 10).map((b) => h("li", { class: "row spread" },
        h("span", { class: "small" }, `${b.name || "A file"}: ${plural(b.added, "row")} added, ${b.skipped} skipped, ${when(b.created, ctx.zone)}`),
        h("button", { type: "button", class: "btn small danger", onclick: () => undo(ctx, b) }, "Undo")))));
  }).catch(() => {});
  return box;
}

async function undo(ctx, batch) {
  if (!(await ask("Undo this import?", `The ${plural(batch.added, "row")} it added are deleted for good, even ones you changed since. Rows you typed are not touched.`, "Undo it", "danger"))) return;
  try {
    let done = await ctx.call("import.undo", { id: batch.id });
    if (done.needs_confirm) {
      if (!(await ask("Some of them are reconciled", done.needs_confirm, "Undo it anyway", "danger"))) return;
      done = await ctx.call("import.undo", { id: batch.id, confirm: true });
    }
    ctx.say(done.said, "ok");
    await ctx.refresh();
  } catch (error) { ctx.fail(error); }
}

// --- files out, and a whole Books back --------------------------------------------------------

function files(ctx) {
  const acct = ctx.chosen();
  const restore = h("input", { type: "file", accept: ".json,application/json" });
  restore.addEventListener("change", async () => {
    const file = restore.files?.[0];
    if (!file) return;
    const big = tooBig(file, ctx.state.data.import_bytes);
    if (big) { ctx.say(big, "bad"); return; }
    if (!(await ask("Bring in this Books file?", `${file.name} fills Books with its accounts, rows, reminders, debts and categories. This works only while Books has no accounts.`, "Bring it in"))) return;
    try {
      const token = await send(ctx, await file.text());
      ctx.say("Reading the file…");
      ctx.say((await ctx.call("everything.import", { token })).said, "ok");
      ctx.state.account = null;
      await ctx.refresh();
    } catch (error) { ctx.fail(error); }
  });
  return h("div", {}, h("h2", { class: "section" }, "Files"),
    h("div", { class: "row" },
      acct ? h("button", { type: "button", class: "btn", onclick: () => exportFile(ctx, { what: "csv", account: acct.id }) }, `Save ${acct.name} as CSV`) : null,
      h("button", { type: "button", class: "btn", onclick: () => exportFile(ctx, { what: "csv" }) }, "Save every account as CSV"),
      h("button", { type: "button", class: "btn", onclick: () => exportFile(ctx, { what: "json" }) }, "Export everything"),
      view.ready ? h("button", { type: "button", class: "btn primary", onclick: async () => {
        try {
          const done = await saveWhole(view.ready, (f) => ctx.save(f));
          if (done.tooLarge) { ctx.say(tooLargeWords(view.ready.text.length, view.ready.type === "application/json" ? "json" : "csv"), "bad"); return; }
          view.ready = null;
          ctx.say(`Saved as ${done.saved.name}.`, "ok");
          ctx.render();
        } catch (error) { ctx.fail(error); }
      } }, "Save the file") : null),
    h("p", { class: "hint" }, "Export everything makes one JSON file with every account, row, reminder, debt, category and reconciliation: a backup, or a way to move Books. Its shape is described in the Books guide. It is saved as one file, however long your history is, as long as it is within the largest file an app may offer you to keep (Settings, Apps)."),
    h("div", { class: "row" }, field("Import everything", restore, "A file made by Export everything. It fills an empty Books.")),
    h("div", { class: "row" }, h("button", { type: "button", class: "btn danger", onclick: () => erase(ctx) }, "Erase everything")));
}

async function erase(ctx) {
  if (!(await ask("Erase everything in Books?", "Every account, row, reminder, debt, category, reconciliation and import is deleted for good, and cannot be brought back unless you saved a file with Export everything first.", "Erase everything", "danger"))) return;
  try {
    ctx.say((await ctx.call("everything.erase", { confirm: true })).said, "ok");
    ctx.state.account = null;
    await ctx.refresh();
  } catch (error) { ctx.fail(error); }
}
