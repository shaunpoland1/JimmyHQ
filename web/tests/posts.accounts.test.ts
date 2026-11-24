// The Posts page: a page's accounts as rows of a label, an id and a scheduler.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { ADD_SELECTED, NOTHING_TICKED, TYPE_INSTEAD, accountProblem, addedLine, blankAccount, canPick, checkAccounts, noneNeeded, pickerChoices, pickerIntro, pickerTool, schedulerChoices, withPicked } from "../../apps/posts/web/lib/accounts.js";

const OFFERED = [
  { id: "practice", label: "Practice", sends_out: false },
  { id: "by_hand", label: "By hand", sends_out: false },
  { id: "socialbu", label: "SocialBu", sends_out: true },
];

test("a row may choose only the schedulers the app offers", () => {
  assert.deepEqual(schedulerChoices(OFFERED).map((s: { id: string }) => s.id), ["practice", "by_hand", "socialbu"]);
});

test("a stored account on a scheduler no longer offered is shown as such, never dropped silently", () => {
  const choices = schedulerChoices(OFFERED, [{ scheduler: "gone", account_id: "1" }]);
  assert.deepEqual(choices.at(-1), { id: "gone", label: "gone (no longer offered)", offered: false });
  assert.match(checkAccounts([{ scheduler: "gone", account_id: "1", label: "" }], OFFERED).error, /^Account 1 is on a scheduler Posts does not offer/);
});

test("a new row starts on the scheduler that needs accounts", () => {
  assert.deepEqual(blankAccount(OFFERED), { scheduler: "socialbu", account_id: "", label: "" });
  assert.deepEqual(blankAccount([]), { scheduler: "", account_id: "", label: "" });
});

test("the rows say which schedulers need no account, by name", () => {
  assert.equal(noneNeeded(OFFERED), "Practice and By hand need no account: Posts books the brand itself there.");
  assert.equal(noneNeeded([OFFERED[0]]), "Practice needs no account: Posts books the brand itself there.");
  assert.equal(noneNeeded([OFFERED[2]]), "");
});

test("the rows become what is stored, trimmed, leaving out rows with nothing typed", () => {
  const rows = [{ scheduler: "socialbu", account_id: " 42 ", label: " Main " }, { scheduler: "socialbu", account_id: "", label: "" }];
  assert.deepEqual(checkAccounts(rows, OFFERED), { accounts: [{ scheduler: "socialbu", account_id: "42", label: "Main" }] });
  assert.deepEqual(checkAccounts([], OFFERED), { accounts: [] });
});

test("each problem is a sentence naming the row as it is shown", () => {
  assert.match(accountProblem({ scheduler: "socialbu", account_id: "", label: "Main" }, 2, OFFERED), /^Account 2 needs its account id/);
  assert.match(accountProblem({ scheduler: "socialbu", account_id: "x".repeat(121), label: "" }, 1, OFFERED), /keep it to 120/);
  assert.match(accountProblem({ scheduler: "socialbu", account_id: "1", label: "x".repeat(81) }, 1, OFFERED), /keep it to 80/);
  assert.equal(accountProblem({ scheduler: "socialbu", account_id: "1", label: "" }, 1, OFFERED), null);
  const twice = [{ scheduler: "socialbu", account_id: "1", label: "" }, { scheduler: "practice", account_id: "1", label: "" }, { scheduler: "socialbu", account_id: "1", label: "Again" }];
  assert.equal(checkAccounts(twice, OFFERED).error, "Accounts 1 and 3 are the same account. Remove one. Nothing was saved.");
});

// --- "Add an account" picks from the tool's own list ---------------------------------------

const FOUND = {
  scheduler: "socialbu", label: "SocialBu", can_list: true, said: "",
  accounts: [
    { account_id: "1001", label: "Garden notes page", network: "facebook" },
    { account_id: "1002", label: "garden_notes", network: "instagram" },
    { account_id: "1003", label: "Garden notes group", network: "linkedin" },
  ],
};
const BOTH = [...OFFERED, { id: "buffer", label: "Buffer", sends_out: true }];

test("the picker opens on the tool the form books through, else Posts' setting, else the first that sends out", () => {
  assert.equal(pickerTool(BOTH, "buffer", "socialbu"), "buffer");
  assert.equal(pickerTool(BOTH, "", "buffer"), "buffer");
  assert.equal(pickerTool(BOTH, "gone", "also_gone"), "socialbu");
  assert.equal(pickerTool(BOTH, "", ""), "socialbu");
  assert.equal(pickerTool([], "", ""), "");
});

test("an account the form already has on that tool is shown as added, and one on another tool is not", () => {
  const rows = [{ scheduler: "socialbu", account_id: " 1002 ", label: "Typed" }, { scheduler: "buffer", account_id: "1001", label: "" }];
  const choices = pickerChoices(FOUND, rows, "socialbu");
  assert.deepEqual(choices.map((c: { added: boolean }) => c.added), [false, true, false]);
  assert.equal(choices[0].network, "facebook");
});

test("adding the ticked accounts appends rows in the tool's order, skips the added, and drops blank rows", () => {
  const rows = [{ scheduler: "socialbu", account_id: "1002", label: "Typed" }, { scheduler: "socialbu", account_id: "", label: "" }];
  const done = withPicked(rows, FOUND, new Set(["1003", "1001", "1002", "nope"]), "socialbu");
  assert.equal(done.added, 2);
  assert.deepEqual(done.rows.map((r: { account_id: string }) => r.account_id), ["1002", "1001", "1003"]);
  assert.deepEqual(done.rows[1], { scheduler: "socialbu", account_id: "1001", label: "Garden notes page" });
  // Nothing was changed in the rows given: nothing is saved until Save.
  assert.equal(rows.length, 2);
  // What it makes passes the same check the typed rows do.
  assert.equal(checkAccounts(done.rows, OFFERED).error, undefined);
});

test("a ticked account is never added twice, even from a repeated list", () => {
  const twice = { ...FOUND, accounts: [...FOUND.accounts, FOUND.accounts[0]] };
  assert.equal(withPicked([], twice, new Set(["1001"]), "socialbu").added, 1);
});

test("a long label from the tool is kept to the form's own limit", () => {
  const long = { accounts: [{ account_id: "9", label: "x".repeat(200), network: "" }] };
  assert.equal(withPicked([], long, new Set(["9"]), "socialbu").rows[0].label.length, 80);
});

test("a tool that cannot list says why in the app's own sentence and offers typing it in", () => {
  const cannot = { scheduler: "practice", can_list: false, accounts: [], said: "Practice books nothing out, so it has no accounts to list." };
  assert.equal(canPick(cannot), false);
  assert.equal(pickerIntro(cannot, "Practice"), "Practice books nothing out, so it has no accounts to list. You can type it in instead.");
  assert.equal(canPick({ can_list: true, accounts: [] }), false);
  assert.match(pickerIntro({ can_list: true, accounts: [], said: "" }, "SocialBu"), /^SocialBu gave no accounts\. You can type it in instead\.$/);
  assert.equal(pickerIntro(null, "SocialBu"), "Asking SocialBu…");
  assert.match(pickerIntro(FOUND, "SocialBu"), /^The 3 accounts you have on SocialBu\. Tick the ones/);
  assert.equal(canPick(FOUND), true);
});

test("the typed entry still starts a row on the tool the owner is looking at, and as before with none", () => {
  assert.deepEqual(blankAccount(BOTH, "buffer"), { scheduler: "buffer", account_id: "", label: "" });
  assert.deepEqual(blankAccount(BOTH, "gone"), { scheduler: "socialbu", account_id: "", label: "" });
  assert.deepEqual(blankAccount(BOTH), { scheduler: "socialbu", account_id: "", label: "" });
});

test("the words of the picker's buttons and what it says after", () => {
  assert.equal(ADD_SELECTED, "Add selected");
  assert.equal(TYPE_INSTEAD, "Type it in instead");
  assert.match(NOTHING_TICKED, /Type it in instead/);
  assert.equal(addedLine(1, "SocialBu"), "Added 1 account from SocialBu. Nothing is saved until you press Save.");
  assert.equal(addedLine(3, "Buffer"), "Added 3 accounts from Buffer. Nothing is saved until you press Save.");
});
