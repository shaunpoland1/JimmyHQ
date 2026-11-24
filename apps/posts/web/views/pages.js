// Pages: who Posts writes for (name, how it sounds, writing samples, time zone, posting
// hours, accounts, required tags), and the owner's own rules for the drafts.

import { addedLine, blankAccount, noneNeeded, pickerTool, schedulerChoices } from "../lib/accounts.js";
import { pageFromForm } from "../lib/forms.js";
import { pausedWords, removalQuestion } from "../lib/pages.js";
import { bookThroughChoices, bookedThroughWords } from "../lib/tools.js";
import { BUTTON, NOTHING_TO_FILL, fillState, proposals, readableAccounts, wanted } from "../lib/voice.js";
import { ask, dialog, field, fill, h, say, why } from "../_ui.js";
import { pickAccounts } from "./accountPicker.js";
import { zonePicker } from "./zonePicker.js";

const RULE_KINDS = [
  ["phrase", "A phrase to flag", "Posts warns when a draft says it."],
  ["tag", "A tag every post carries", "Posts warns when a draft leaves it out."],
  ["question", "A question to ask", "Asked on every draft, for you to answer."],
];

export const FIRST_RUN = "Posts writes for a brand: a name, one sentence on how it sounds, and when it posts. Add your first one to start.";

export function render(ctx) {
  const data = ctx.data();
  const editing = ctx.state.editingPage;
  const parts = [];
  if (!data.pages.length) parts.push(h("p", { class: "banner" }, FIRST_RUN));
  if (editing !== null) {
    parts.push(pageForm(ctx, editing === "new" ? null : data.pages.find((p) => p.id === editing) ?? null));
  } else {
    parts.push(h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", onclick: () => { ctx.state.editingPage = "new"; ctx.render(); } }, "Add a brand")));
  }
  parts.push(h("ul", { class: "cards" }, data.pages.map((page) => h("li", { class: `card${page.paused ? " paused" : ""}` },
    h("div", { class: "row spread" },
      h("h3", {}, page.name, page.paused ? [" ", h("span", { class: "chip warn" }, "Paused")] : null),
      h("div", { class: "row wrap" },
        h("button", { type: "button", class: "btn small", "aria-label": `Edit ${page.name}`, onclick: () => { ctx.state.editingPage = page.id; ctx.render(); } }, "Edit"),
        h("button", { type: "button", class: "btn small", "aria-label": `${page.paused ? "Resume" : "Pause"} ${page.name}`, onclick: () => pause(ctx, page) }, page.paused ? "Resume" : "Pause"),
        h("button", { type: "button", class: "btn small danger", "aria-label": `Remove ${page.name}`, onclick: () => remove(ctx, page) }, "Remove"))),
    page.paused ? h("p", { class: "small muted" }, "Paused: no daily ask to the researcher, no ideas from Reader and no new pieces. What it already has carries on.") : null,
    page.register ? h("p", {}, page.register) : h("p", { class: "muted" }, "No sentence on how it sounds yet."),
    h("p", { class: "small muted" }, `Posts at ${(page.hours ?? []).join(", ")} · ${page.timezone || `your time zone (${ctx.ownerZone})`}`
      + `${page.tags?.length ? ` · carries ${page.tags.join(" ")}` : ""}${page.accounts?.length ? ` · ${page.accounts.length} account${page.accounts.length === 1 ? "" : "s"}` : ""}`
      + `${bookedThroughWords(page, data.scheduler) ? ` · ${bookedThroughWords(page, data.scheduler)}` : ""}`)))));
  if (data.pages.length) parts.push(rules(ctx));
  return h("div", { class: "pages" }, parts);
}

async function pause(ctx, page) {
  try {
    const saved = await ctx.call("pages.pause", { id: page.id, paused: !page.paused });
    say(pausedWords(saved), "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

/** Remove a page, after asking. The app says first what goes with it, or why it may not go. */
async function remove(ctx, page) {
  let found;
  try {
    found = await ctx.call("pages.removal", { id: page.id });
  } catch (error) {
    return say(why(error), "bad");
  }
  if (found.refused) return say(found.refused, "bad");
  const q = removalQuestion(found);
  const chosen = await dialog({ title: q.title, intro: q.intro, cancel: q.no,
    actions: [{ label: q.yes, kind: "danger", run: async () => { await ctx.call("pages.remove", { id: page.id }); } }] });
  if (chosen !== q.yes) return undefined;
  if (ctx.state.editingPage === page.id) ctx.state.editingPage = null;
  say(`Removed ${found.name}.`, "ok");
  try {
    await ctx.refresh();
    await ctx.loadRules();
    ctx.render();
  } catch (error) {
    say(why(error), "bad");
  }
  return undefined;
}

function pageForm(ctx, page) {
  const v = (x) => x ?? "";
  const name = h("input", { type: "text", maxlength: 80, value: v(page?.name), required: true });
  const register = h("input", { type: "text", maxlength: 400, value: v(page?.register), placeholder: "Warm, plain and practical, in the first person." });
  const samples = h("textarea", { rows: 6, value: v(page?.samples), placeholder: "Paste a few posts this brand has published. They teach the voice; their words are not reused." });
  const timezone = zonePicker({ value: v(page?.timezone), ownerZone: ctx.ownerZone, labelId: "page-zone-label",
    onchange: () => { ctx.state.typed = true; } });
  const hours = h("input", { type: "text", value: (page?.hours ?? ["08:00"]).join(", ") });
  const offered = ctx.data().schedulers ?? [];
  const stored = page?.book_through ?? "";
  const accounts = accountRows(ctx, page?.accounts ?? [], offered, () => { ctx.state.typed = true; },
    () => pickerTool(offered, bookThrough.value, ctx.data().scheduler?.id), () => syncFill());
  const bookThrough = h("select", { onchange: () => { ctx.state.typed = true; } },
    bookThroughChoices(offered, ctx.data().scheduler, stored).map((c) => h("option", { value: c.value, selected: c.value === stored }, c.label)));
  const tags = h("input", { type: "text", value: (page?.tags ?? []).join(" "), placeholder: "#garden" });
  const save = async () => {
    const made = pageFromForm({ name: name.value, register: register.value, samples: samples.value, timezone: timezone.value(),
      hours: hours.value, accounts: accounts.rows(), tags: tags.value, book_through: bookThrough.value }, page?.id, offered);
    if (made.error) return say(made.error, "bad");
    try {
      const saved = await ctx.call("pages.save", made.page);
      ctx.state.editingPage = null;
      say(`Saved ${saved.name}.`, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    }
  };
  const fillStatus = h("span", { class: "help", role: "status", "aria-live": "polite" });
  const fillHelp = h("span", { class: "help" });
  let filling = false;
  function syncFill() {
    const state = fillState(accounts.rows(), offered);
    fillIn.disabled = filling || !state.enabled;
    fillHelp.textContent = state.help;
  }
  const fillIn = h("button", { type: "button", class: "btn small", onclick: async () => {
    const values = { register: register.value, samples: samples.value, tags: tags.value };
    const need = readableAccounts(accounts.rows(), offered);
    if (!need.ok) return fill(fillStatus, need.said);
    const want = wanted(values);
    if (!want.length) return fill(fillStatus, NOTHING_TO_FILL);
    filling = true;
    syncFill();
    fill(fillStatus, "Reading its posts…");
    try {
      const result = await ctx.call("voice.fill", { accounts: need.accounts, want });
      const done = proposals(result, { register: register.value, samples: samples.value, tags: tags.value });
      if ("register" in done.fills) register.value = done.fills.register;
      if ("samples" in done.fills) samples.value = done.fills.samples;
      if ("tags" in done.fills) tags.value = done.fills.tags;
      if (Object.keys(done.fills).length) ctx.state.typed = true;
      fill(fillStatus, done.line);
    } catch (error) {
      fill(fillStatus, why(error));
    }
    filling = false;
    syncFill();
  } }, BUTTON);
  syncFill();
  return h("form", { class: "form card", onsubmit: (event) => { event.preventDefault(); save(); } },
    h("h3", {}, page ? `Edit ${page.name}` : "A new brand"),
    field("Name", name),
    field("Book through", bookThrough, "The tool that books this brand's posts. Posts' setting is used unless you choose one here. A tool that sends posts out needs its accounts listed below."),
    accounts.el,
    h("div", { class: "field" }, h("div", { class: "row wrap" }, fillIn, fillStatus), fillHelp),
    field("How it sounds", register, "One sentence."),
    field("Writing samples", samples, "Up to 10,000 characters."),
    field("Tags every post carries", tags, "Separated by spaces."),
    // Not a <label>: a click on the list inside it would press the button again.
    h("div", { class: "field" }, h("span", { class: "label", id: "page-zone-label" }, "Time zone"), timezone.el,
      h("span", { class: "help" }, "Where the posting times are read. “Use mine” follows your own time zone.")),
    field("Posting times", hours, "Times of day, such as 08:00, 17:30."),
    h("div", { class: "row" }, h("button", { type: "submit", class: "btn primary" }, "Save"),
      h("button", { type: "button", class: "btn", onclick: () => { ctx.state.editingPage = null; ctx.render(); } }, "Cancel")));
}

/**
 * The page's accounts as rows of a label, the account's id and its scheduler, with Add
 * and Remove. "Add an account" opens a dialog to tick accounts from the tool's own list, or to
 * type one in instead. Returns {el, rows()}: `rows()` is what the owner has in the rows now.
 * `toolNow()` is the tool the form books through now.
 */
function accountRows(ctx, stored, offered, changed, toolNow, rowsChanged = () => {}) {
  const rows = stored.map((a) => ({ scheduler: a.scheduler, account_id: a.account_id ?? "", label: a.label ?? "" }));
  const list = h("div", { class: "account-rows" });
  const draw = () => fill(list, rows.length ? rows.map((row, i) => {
    const n = i + 1;
    const label = h("input", { type: "text", maxlength: 80, value: row.label, placeholder: "Main account",
      oninput: () => { row.label = label.value; } });
    const id = h("input", { type: "text", maxlength: 120, value: row.account_id, autocomplete: "off", spellcheck: "false",
      placeholder: "As the scheduler shows it", oninput: () => { row.account_id = id.value; rowsChanged(); } });
    const scheduler = h("select", { onchange: () => { row.scheduler = scheduler.value; changed(); rowsChanged(); } },
      schedulerChoices(offered, [row]).map((s) => h("option", { value: s.id, selected: s.id === row.scheduler }, s.label)));
    return h("div", { class: "account-row", role: "group", "aria-label": `Account ${n}` },
      field("Label", label), field("Account id", id), field("Scheduler", scheduler),
      h("button", { type: "button", class: "btn small", "aria-label": `Remove account ${n}`,
        onclick: () => { rows.splice(i, 1); changed(); draw(); rowsChanged(); } }, "Remove"));
  }) : h("p", { class: "small muted" }, "No accounts."));
  draw();
  const typeOne = (scheduler) => {
    rows.push(blankAccount(offered, scheduler));
    changed();
    draw();
    rowsChanged();
    list.querySelectorAll("input")[(rows.length - 1) * 2]?.focus();
  };
  const add = h("button", { type: "button", class: "btn small", onclick: async () => {
    const result = await pickAccounts(ctx, { offered, rows: rows.map((r) => ({ ...r })), tool: toolNow() });
    if (!result) return;
    if (result.typed) return typeOne(result.scheduler);
    rows.splice(0, rows.length, ...result.rows);
    changed();
    draw();
    rowsChanged();
    say(addedLine(result.added, result.label), "ok");
  } }, "Add an account");
  const help = noneNeeded(offered);
  return {
    el: h("fieldset", { class: "field accounts" }, h("legend", { class: "label" }, "This brand's accounts"),
      help ? h("span", { class: "help" }, help) : null, list, h("div", { class: "row" }, add)),
    rows: () => rows.map((r) => ({ ...r })),
  };
}

function rules(ctx) {
  const data = ctx.data();
  const list = ctx.state.rules ?? [];
  const scope = h("select", {}, h("option", { value: "" }, "Every brand"), data.pages.map((p) => h("option", { value: String(p.id) }, p.name)));
  const kind = h("select", {}, RULE_KINDS.map(([value, words]) => h("option", { value }, words)));
  const text = h("input", { type: "text", maxlength: 300, placeholder: "The phrase, the tag, or the question" });
  const says = h("input", { type: "text", maxlength: 300, placeholder: "Why, in a few words (optional)" });
  const add = async () => {
    try {
      await ctx.call("rules.save", { page_id: scope.value ? Number(scope.value) : null, kind: kind.value, text: text.value, says: says.value });
      say("Rule added. It applies to drafts from now on.", "ok");
      await ctx.loadRules();
      ctx.render();
    } catch (error) {
      say(why(error), "bad");
    }
  };
  const words = Object.fromEntries(RULE_KINDS.map(([value, w]) => [value, w]));
  return h("section", { class: "rules", "aria-labelledby": "rules-h" },
    h("h2", { id: "rules-h" }, "Your rules"),
    h("p", { class: "small muted" }, RULE_KINDS.map(([, w, help]) => `${w}: ${help}`).join(" ")),
    list.length ? h("ul", { class: "rows" }, list.map((rule) => h("li", { class: "row spread" },
      h("span", {}, h("span", { class: "chip" }, words[rule.kind] ?? rule.kind), " ", h("span", { class: "strong" }, rule.text),
        rule.says ? h("span", { class: "muted" }, ` · ${rule.says}`) : null,
        h("span", { class: "small muted" }, ` · ${rule.page_id ? data.pages.find((p) => p.id === rule.page_id)?.name ?? "a brand you removed" : "every brand"}`)),
      h("button", { type: "button", class: "btn small danger", onclick: async () => {
        if (!(await ask("Delete this rule?", `“${rule.text}” stops applying to new drafts.`, "Delete it", "danger"))) return;
        try {
          await ctx.call("rules.delete", { id: rule.id });
          await ctx.loadRules();
          ctx.render();
        } catch (error) {
          say(why(error), "bad");
        }
      } }, "Delete")))) : h("p", { class: "muted" }, "No rules yet."),
    h("form", { class: "form", onsubmit: (event) => { event.preventDefault(); add(); } },
      h("div", { class: "row wrap" }, field("For", scope), field("Kind", kind)),
      field("Rule", text), field("What it says", says),
      h("div", { class: "row" }, h("button", { type: "submit", class: "btn" }, "Add the rule"))));
}
