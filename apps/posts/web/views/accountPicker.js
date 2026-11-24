// "Add an account": a dialog of the page's own that lists the accounts the owner already has
// on a tool (SocialBu, Buffer) to tick from, or says in a sentence why it cannot and offers
// typing the account in instead. Nothing is saved here: it answers which rows to add to the
// form, and the form is saved with Save.

import { ADD_SELECTED, NOTHING_TICKED, TYPE_INSTEAD, canPick, pickerChoices, pickerIntro, withPicked } from "../lib/accounts.js";
import { dialog, field, fill, h, why } from "../_ui.js";

/**
 * Open the dialog on `tool` (a scheduler id) for the form's current `rows`. Resolves null when
 * the owner closed it, {typed: true, scheduler} for "Type it in instead", or {rows, added,
 * scheduler} with the form's new rows after "Add selected".
 */
export async function pickAccounts(ctx, { offered, rows, tool }) {
  let current = tool;
  let found = null;
  let loading = 0;
  const ticked = new Set();
  const labelOf = () => offered.find((s) => s.id === current)?.label ?? current;
  const intro = h("p", { class: "muted", "aria-live": "polite" });
  const list = h("div", { class: "picker-list" });
  const select = h("select", { onchange: () => { current = select.value; load(); } },
    offered.map((s) => h("option", { value: s.id, selected: s.id === current }, s.label)));

  const draw = () => {
    fill(intro, pickerIntro(found, labelOf()));
    fill(list, canPick(found) ? pickerChoices(found, rows, current).map((choice) => {
      const box = h("input", { type: "checkbox", disabled: choice.added, checked: choice.added || ticked.has(choice.account_id),
        onchange: () => { if (box.checked) ticked.add(choice.account_id); else ticked.delete(choice.account_id); } });
      return h("label", { class: "pick" }, box, h("span", { class: "strong" }, choice.label),
        h("span", { class: "small muted" }, [choice.network, choice.added ? "already added" : ""].filter(Boolean).join(" · ")));
    }) : null);
  };
  async function load() {
    const mine = ++loading;
    found = null;
    ticked.clear();
    draw();
    let answer;
    try {
      answer = await ctx.call("accounts.list", { scheduler: current });
    } catch (error) {
      answer = { scheduler: current, can_list: false, accounts: [], said: why(error) };
    }
    if (mine !== loading) return; // the owner chose another tool meanwhile
    found = answer;
    draw();
  }

  load();
  const chosen = await dialog({
    title: "Add an account",
    body: h("div", { class: "picker" }, field("Tool", select), intro, list),
    actions: [
      { label: ADD_SELECTED, kind: "primary", run: () => (ticked.size ? undefined : NOTHING_TICKED) },
      { label: TYPE_INSTEAD },
    ],
  });
  if (chosen === TYPE_INSTEAD) return { typed: true, scheduler: current };
  if (chosen !== ADD_SELECTED) return null;
  return { ...withPicked(rows, found, ticked, current), scheduler: current, label: labelOf() };
}
