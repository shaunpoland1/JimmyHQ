// The brand form's "Fill in from its posts": which accounts it needs, which fields it may
// fill (only the empty ones), and the one line it says. Pure.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { ADD_FIRST, BUTTON, FILL_HELP, NO_READER, NOTHING_TO_FILL, fillState, proposals, readableAccounts, wanted } from "../../apps/posts/web/lib/voice.js";

const OFFERED = [
  { id: "practice", label: "Practice", sends_out: false, reads_posts: false },
  { id: "socialbu", label: "SocialBu", sends_out: true, reads_posts: true },
  { id: "buffer", label: "Buffer", sends_out: true, reads_posts: true },
];
const RESULT = { ok: true, said: "", notes: [], posts: 12, register: "Warm and plain, in the first person.", samples: "A post of theirs.\n\n---\n\nAnother.", tags: ["#garden", "#soil"], on_this_computer: false };
const EMPTY = { register: "", samples: "", tags: "" };

test("it needs an account with an id on a tool that can read its posts, and names which tools do", () => {
  const ok = readableAccounts([{ scheduler: "socialbu", account_id: " 1001 ", label: " Main " }, { scheduler: "practice", account_id: "a", label: "" }], OFFERED);
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.accounts, [{ scheduler: "socialbu", account_id: "1001", label: "Main" }]);
  const none = readableAccounts([{ scheduler: "practice", account_id: "a", label: "" }, { scheduler: "socialbu", account_id: "  ", label: "x" }], OFFERED);
  assert.equal(none.ok, false);
  assert.match(none.said, /needs at least one account on SocialBu and Buffer, which can list what it has published/);
  assert.match(readableAccounts([], []).said, /Posts offers none/);
});

test("only the fields still empty are asked for", () => {
  assert.deepEqual(wanted(EMPTY), ["register", "samples", "tags"]);
  assert.deepEqual(wanted({ register: "  ", samples: "Mine.", tags: "" }), ["register", "tags"]);
  assert.deepEqual(wanted({ register: "a", samples: "b", tags: "#c" }), []);
  assert.match(NOTHING_TO_FILL, /nothing was changed/);
  assert.equal(BUTTON, "Fill in from its posts");
});

test("everything empty is filled, and the line says what, from how many posts, and that nothing is saved", () => {
  const done = proposals(RESULT, EMPTY);
  assert.deepEqual(done.fills, { register: RESULT.register, samples: RESULT.samples, tags: "#garden #soil" });
  assert.equal(done.line, "Filled in the sentence on how it sounds, the writing samples and the tags from 12 posts (the sentence came from a hosted model). Edit them, then press Save: nothing is saved yet.");
});

test("a field that already has something is never overwritten and is named as left alone", () => {
  const done = proposals(RESULT, { register: "My own sentence.", samples: "", tags: "#mine" });
  assert.deepEqual(Object.keys(done.fills), ["samples"]);
  assert.match(done.line, /^Filled in the writing samples from 12 posts\./);
  assert.match(done.line, /Left alone, since you had written something: the sentence on how it sounds and the tags\./);
});

test("a field with nothing to offer is said so, and a model that answered locally is said", () => {
  const done = proposals({ ...RESULT, tags: [], on_this_computer: true }, EMPTY);
  assert.equal("tags" in done.fills, false);
  assert.match(done.line, /the sentence came from a model on this computer/);
  assert.match(done.line, /Nothing to offer for the tags\./);
});

test("when the model gave no sentence the samples and tags still go in, with the app's note", () => {
  const done = proposals({ ...RESULT, register: "", on_this_computer: null, notes: ["The model could not describe the voice (it was busy). You can still use the samples and tags, and write the sentence yourself."] }, EMPTY);
  assert.deepEqual(Object.keys(done.fills), ["samples", "tags"]);
  assert.match(done.line, /^Filled in the writing samples and the tags from 12 posts\./);
  assert.match(done.line, /Nothing to offer for the sentence on how it sounds\./);
  assert.match(done.line, /The model could not describe the voice/);
  assert.doesNotMatch(done.line, /came from/);
});

test("when nothing could be read the app's own sentence is the line and nothing is filled", () => {
  const done = proposals({ ok: false, said: "SocialBu said no when Posts asked for its published posts (Unauthenticated.). Nothing was changed." }, EMPTY);
  assert.deepEqual(done.fills, {});
  assert.match(done.line, /^SocialBu said no/);
  assert.match(proposals(null, EMPTY).line, /nothing was filled in/);
});

test("one post reads as one post, and posts that give nothing for the empty fields change nothing", () => {
  assert.match(proposals({ ...RESULT, posts: 1 }, EMPTY).line, /from 1 post /);
  const bare = proposals({ ok: true, posts: 3, register: "", samples: "", tags: [], notes: [] }, EMPTY);
  assert.deepEqual(bare.fills, {});
  assert.match(bare.line, /^Read 3 posts, but found nothing to put in what was empty\./);
  assert.match(bare.line, /Nothing was changed\.$/);
});

test("the button is off until the form has an account on a tool that can read posts, and says why", () => {
  const off = fillState([], OFFERED);
  assert.deepEqual(off, { enabled: false, help: ADD_FIRST });
  assert.match(ADD_FIRST, /^Add one of this brand's accounts above first/);
  assert.equal(fillState([{ scheduler: "practice", account_id: "a", label: "" }], OFFERED).enabled, false);
  assert.equal(fillState([{ scheduler: "buffer", account_id: " ", label: "" }], OFFERED).enabled, false);
  assert.deepEqual(fillState([{ scheduler: "buffer", account_id: "7", label: "" }], OFFERED), { enabled: true, help: FILL_HELP });
  assert.deepEqual(fillState([{ scheduler: "buffer", account_id: "7", label: "" }], []), { enabled: false, help: NO_READER });
});
