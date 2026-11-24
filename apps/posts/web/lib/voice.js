// "Fill in from its posts": what the button needs, which fields it may fill, and the one line
// it says afterwards. The app reads the posts and makes the proposals (`voice.fill`); this
// only decides what goes into the form: a field that already has something is never
// overwritten, and nothing is saved until Save. Pure.

import { nameList } from "./tools.js";

export const BUTTON = "Fill in from its posts";
export const FIELDS = [
  ["register", "the sentence on how it sounds"],
  ["samples", "the writing samples"],
  ["tags", "the tags"],
];

const blank = (value) => !String(value ?? "").trim();

/**
 * Whether the form has what the button needs: at least one account with an id, on a tool
 * that can read the posts it published (`reads_posts`). Returns {ok: true, accounts} with
 * those accounts, or {ok: false, said}: the sentence that says what to add.
 */
export function readableAccounts(rows, offered) {
  const reads = new Set((offered ?? []).filter((s) => s.reads_posts).map((s) => s.id));
  const accounts = (rows ?? []).filter((r) => reads.has(r.scheduler) && !blank(r.account_id))
    .map((r) => ({ scheduler: r.scheduler, account_id: String(r.account_id).trim(), label: String(r.label ?? "").trim() }));
  if (accounts.length) return { ok: true, accounts };
  const names = nameList((offered ?? []).filter((s) => s.reads_posts).map((s) => s.label));
  return {
    ok: false,
    said: names
      ? `${BUTTON} needs at least one account on ${names}, which can list what it has published. Add one under “This brand's accounts” first.`
      : `${BUTTON} needs a tool that can list what a brand has published, and Posts offers none.`,
  };
}

export const FILL_HELP = "Reads what this brand's accounts have published and proposes the sentence, writing samples and tags below. It only fills what is empty, and nothing is saved until you press Save.";
export const ADD_FIRST = "Add one of this brand's accounts above first; then this can read what it has published.";
export const NO_READER = "None of the tools Posts offers can list what a brand has published, so this is not available.";

/**
 * The button's state for the accounts in the form now (saved or not): {enabled, help}. It is
 * enabled once a row has an id on a tool that can read posts; otherwise `help` says why.
 */
export function fillState(rows, offered) {
  if (readableAccounts(rows, offered).ok) return { enabled: true, help: FILL_HELP };
  const any = (offered ?? []).some((s) => s.reads_posts);
  return { enabled: false, help: any ? ADD_FIRST : NO_READER };
}

/** The fields still empty in the form, as the app's `want`: ["register", "samples", "tags"] or fewer. */
export function wanted(values) {
  return FIELDS.map(([key]) => key).filter((key) => blank(values?.[key]));
}

/** The words for a list of field keys: "the writing samples and the tags". */
function words(keys) {
  return nameList(FIELDS.filter(([key]) => keys.includes(key)).map(([, said]) => said));
}

export const NOTHING_TO_FILL = "Everything it can fill in already has something, so nothing was changed.";

/**
 * What the form takes from the app's answer to `voice.fill` ({ok, said, notes, posts, register,
 * samples, tags, on_this_computer}), given the form's values now. Returns {fills, line}: `fills` holds
 * only the fields that were empty and have a proposal (`tags` as the form's space-separated
 * text); `line` is the one sentence for the owner: what was filled, what was left alone, what
 * had nothing to offer, anything that went wrong on the way, and that nothing is saved yet.
 */
export function proposals(result, values) {
  if (!result?.ok) return { fills: {}, line: result?.said || "Posts could not read its posts, so nothing was filled in." };
  const asked = wanted(values);
  const fills = {};
  const offered = {
    register: String(result.register ?? "").trim(),
    samples: String(result.samples ?? "").trim(),
    tags: (result.tags ?? []).join(" "),
  };
  const filled = [];
  const nothing = [];
  for (const key of asked) {
    if (offered[key]) {
      fills[key] = offered[key];
      filled.push(key);
    } else {
      nothing.push(key);
    }
  }
  const kept = FIELDS.map(([key]) => key).filter((key) => !asked.includes(key));
  const n = Number(result.posts) || 0;
  const parts = [];
  if (filled.length) {
    const source = filled.includes("register") && result.on_this_computer !== null && result.on_this_computer !== undefined
      ? ` (the sentence came from ${result.on_this_computer ? "a model on this computer" : "a hosted model"})` : "";
    parts.push(`Filled in ${words(filled)} from ${n} post${n === 1 ? "" : "s"}${source}.`);
  } else {
    parts.push(`Read ${n} post${n === 1 ? "" : "s"}, but found nothing to put in what was empty.`);
  }
  if (kept.length) parts.push(`Left alone, since you had written something: ${words(kept)}.`);
  if (nothing.length && filled.length) parts.push(`Nothing to offer for ${words(nothing)}.`);
  for (const note of result.notes ?? []) parts.push(String(note).trim());
  parts.push(filled.length ? "Edit them, then press Save: nothing is saved yet." : "Nothing was changed.");
  return { fills, line: parts.join(" ") };
}
