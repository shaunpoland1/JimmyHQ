// A page's accounts as rows: a label, the account's id, and the scheduler it is on. The
// app checks everything again and refuses with a sentence; this says what is wrong
// before anything is sent. What is stored is unchanged: {scheduler, account_id, label}.
// Pure.

export const MOST_ID = 120;
export const MOST_LABEL = 80;

/**
 * The schedulers a row may choose: those the app offers ({id, label, sends_out}), and
 * one a stored account names that the app no longer offers, so editing a page never
 * loses it silently.
 */
export function schedulerChoices(offered, rows = []) {
  const out = (offered ?? []).map((s) => ({ id: s.id, label: s.label, offered: true }));
  for (const row of rows) {
    if (row?.scheduler && !out.some((s) => s.id === row.scheduler)) {
      out.push({ id: row.scheduler, label: `${row.scheduler} (no longer offered)`, offered: false });
    }
  }
  return out;
}

/**
 * A new empty row, on the first scheduler that sends out (the one that needs accounts), or
 * on `preferred` when that is one the app offers.
 */
export function blankAccount(offered, preferred = "") {
  const first = (offered ?? []).find((s) => s.id === preferred)
    ?? (offered ?? []).find((s) => s.sends_out) ?? (offered ?? [])[0];
  return { scheduler: first?.id ?? "", account_id: "", label: "" };
}

/** The sentence under the rows: "Practice and By hand need none." */
export function noneNeeded(offered) {
  const names = (offered ?? []).filter((s) => !s.sends_out).map((s) => s.label);
  if (!names.length) return "";
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `${list} need${names.length === 1 ? "s" : ""} no account: Posts books the brand itself there.`;
}

function blank(row) {
  return !String(row?.account_id ?? "").trim() && !String(row?.label ?? "").trim();
}

/** One row's own problem, as a sentence naming it by number, or null. */
export function accountProblem(row, number, offered) {
  const id = String(row?.account_id ?? "").trim();
  const label = String(row?.label ?? "").trim();
  if (!id) return `Account ${number} needs its account id, as the scheduler shows it. Nothing was saved.`;
  if (id.length > MOST_ID) return `Account ${number}'s id is ${id.length} characters; keep it to ${MOST_ID}. Nothing was saved.`;
  if (label.length > MOST_LABEL) return `Account ${number}'s label is ${label.length} characters; keep it to ${MOST_LABEL}. Nothing was saved.`;
  if (!(offered ?? []).some((s) => s.id === row?.scheduler)) {
    return `Account ${number} is on a scheduler Posts does not offer. Choose one from the list, or remove it. Nothing was saved.`;
  }
  return null;
}

/**
 * The rows as `pages.save` takes them, leaving out rows with nothing typed in them.
 * Returns {accounts} or {error}: the first row's problem, or two rows naming the same
 * account on the same scheduler.
 */
export function checkAccounts(rows, offered) {
  const accounts = [];
  const seen = new Map();
  for (const [index, row] of (rows ?? []).entries()) {
    if (blank(row)) continue;
    const number = index + 1; // as the rows are shown: Account 1, Account 2…
    const problem = accountProblem(row, number, offered);
    if (problem) return { error: problem };
    const account = { scheduler: row.scheduler, account_id: String(row.account_id).trim(), label: String(row.label ?? "").trim() };
    const key = `${account.scheduler}\u0000${account.account_id}`;
    if (seen.has(key)) return { error: `Accounts ${seen.get(key)} and ${number} are the same account. Remove one. Nothing was saved.` };
    seen.set(key, number);
    accounts.push(account);
  }
  return { accounts };
}

// --- "Add an account": choosing from the tool's own list ----------------------------------

export const ADD_SELECTED = "Add selected";
export const TYPE_INSTEAD = "Type it in instead";
export const NOTHING_TICKED = `Tick at least one account, or choose “${TYPE_INSTEAD}”.`;

/**
 * The tool the picker opens on: the one the form books through when it chose one, else
 * Posts' setting, when the app offers it; otherwise the first tool that sends out.
 */
export function pickerTool(offered, bookThrough, settingId) {
  const known = (id) => (offered ?? []).some((s) => s.id === id);
  if (bookThrough && known(bookThrough)) return bookThrough;
  if (settingId && known(settingId)) return settingId;
  return blankAccount(offered).scheduler;
}

/**
 * What the picker lists for one answer of `accounts.list` ({can_list, accounts, said}),
 * each {account_id, label, network, added}: `added` when the form already has that
 * account on that tool (so it is shown and cannot be ticked twice).
 */
export function pickerChoices(found, rows, scheduler) {
  const have = new Set((rows ?? []).filter((r) => r.scheduler === scheduler).map((r) => String(r.account_id ?? "").trim()));
  return (found?.accounts ?? []).map((a) => ({
    account_id: String(a.account_id), label: String(a.label ?? "") || String(a.account_id), network: String(a.network ?? ""),
    added: have.has(String(a.account_id)),
  }));
}

/** The line above the list: the app's own sentence when the tool cannot list, else what to do. */
export function pickerIntro(found, toolLabel) {
  if (!found) return `Asking ${toolLabel}…`;
  if (!found.can_list || !found.accounts?.length) {
    return `${found.said || `${toolLabel} gave no accounts.`} You can type it in instead.`;
  }
  const n = found.accounts.length;
  return `${n === 1 ? "The account" : `The ${n} accounts`} you have on ${toolLabel}. Tick the ones this brand posts to.`;
}

/** Whether the list is one to tick from (the tool answered with accounts). */
export function canPick(found) {
  return Boolean(found?.can_list && found.accounts?.length);
}

/**
 * The form's rows with the ticked accounts added: blank rows are dropped first, and an
 * account the form already has on that tool is never added twice. Rows come in the order
 * the tool listed them. Returns {rows, added}.
 */
export function withPicked(rows, found, ticked, scheduler) {
  const kept = (rows ?? []).filter((r) => !blank(r));
  const have = new Set(kept.filter((r) => r.scheduler === scheduler).map((r) => String(r.account_id).trim()));
  const added = [];
  for (const a of found?.accounts ?? []) {
    const id = String(a.account_id);
    if (!ticked.has(id) || have.has(id)) continue;
    have.add(id);
    added.push({ scheduler, account_id: id, label: String(a.label ?? "").slice(0, MOST_LABEL) });
  }
  return { rows: [...kept, ...added], added: added.length };
}

/** The status line after "Add selected": nothing is saved until Save. */
export function addedLine(n, toolLabel) {
  return `Added ${n} account${n === 1 ? "" : "s"} from ${toolLabel}. Nothing is saved until you press Save.`;
}
