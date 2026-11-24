// The Articles page: an article's header image, and editing an article already sent.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { NO_HEADER, PRACTICE_HEADER, headerSaid, isPlaceholder, madeSaid, makeWords, showsHeader } from "../../apps/articles/web/lib/header.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { EDIT_UNANSWERED, canEditSent, editSentTone, editSentWords, editUnanswered } from "../../apps/articles/web/lib/sent.js";

const placeholder = { ref: "uploads/a.png", provider: "practice", sent_with_draft: false };
const real = { ref: "uploads/b.png", provider: "other", sent_with_draft: true };

test("the header image is shown only when it is switched on and the article is ready, approved or sent", () => {
  for (const state of ["ready", "approved", "sent"]) assert.equal(showsHeader({ header_images: true }, { state }), true);
  for (const state of ["drafting", "reviewing", "publishing", "unsure", "published", "declined", "dropped"]) {
    assert.equal(showsHeader({ header_images: true }, { state }), false);
  }
  assert.equal(showsHeader({ header_images: false }, { state: "ready" }), false);
  assert.equal(showsHeader({}, { state: "ready" }), false);
  assert.equal(showsHeader(null, null), false);
});

test("a Practice placeholder is said plainly to be one that is never sent", () => {
  assert.equal(isPlaceholder(placeholder), true);
  assert.equal(isPlaceholder(real), false);
  assert.equal(isPlaceholder(null), false);
  assert.equal(headerSaid(placeholder, { state: "approved" }, { publisher: "wordpress" }), PRACTICE_HEADER);
  assert.match(PRACTICE_HEADER, /Practice placeholder/);
  assert.match(PRACTICE_HEADER, /never sent to your site/);
  assert.match(madeSaid(placeholder), /Practice placeholder.*never sent/);
  assert.equal(madeSaid(real), "A header image was made.");
});

test("what the page says about a header image never promises more than happens", () => {
  assert.equal(headerSaid(null, { state: "ready" }, { publisher: "wordpress" }), NO_HEADER);
  assert.match(headerSaid(real, { state: "approved" }, { publisher: "wordpress" }), /uploaded with it as the draft's featured image/);
  assert.match(headerSaid(real, { state: "sent" }, { publisher: "wordpress" }), /one made since is kept here only/);
  assert.match(headerSaid(real, { state: "approved" }, { publisher: "by_hand" }), /kept here only/);
  assert.match(headerSaid(real, { state: "approved" }, null), /kept here only/);
  assert.equal(makeWords(null), "Make a header image");
  assert.equal(makeWords(real), "Make another header image");
});

test("only an article that was sent, and has text, can be edited and sent again", () => {
  assert.equal(canEditSent({ piece: { state: "sent" }, revision: { n: 2 } }), true);
  assert.equal(canEditSent({ piece: { state: "sent" }, revision: null }), false);
  for (const state of ["ready", "approved", "publishing", "unsure", "published"]) {
    assert.equal(canEditSent({ piece: { state }, revision: { n: 1 } }), false);
  }
  assert.equal(canEditSent(null), false);
});

test("the editor of a sent article says whether anything will be sent", () => {
  const wordpress = editSentWords({ publisher: "wordpress" });
  assert.equal(wordpress.label, "Save and send");
  assert.equal(wordpress.button, "Edit and send again");
  assert.match(wordpress.intro, /only while it is still a draft there/);
  for (const publisher of ["by_hand", "practice"]) {
    const kept = editSentWords({ publisher });
    assert.equal(kept.label, "Save");
    assert.equal(kept.button, "Edit"); // never "send" where nothing is sent
    assert.match(kept.intro, /Nothing is sent/);
  }
  assert.match(editSentWords(null).intro, /Nothing is sent/);
});

test("an edit's answer is shown as good news only when the site said it took it", () => {
  const before = [{ id: 1, outcome: "sent" }];
  assert.equal(editSentTone(before, [...before, { id: 2, outcome: "sent" }]), "ok");
  assert.equal(editSentTone(before, [...before, { id: 2, outcome: "refused" }]), "warn");
  assert.equal(editSentTone(before, [...before, { id: 2, outcome: "unsure" }]), "warn");
  assert.equal(editSentTone(before, before), "warn"); // nothing was sent: the site was not changed
  assert.equal(editSentTone(undefined, undefined), "warn");
});

test("an edit that got no answer is held until the owner has looked on the site", () => {
  assert.equal(editUnanswered({ state: "sent", attempt: { outcome: "unsure" } }), true);
  assert.equal(editUnanswered({ state: "sent", attempt: { outcome: "sent" } }), false);
  assert.equal(editUnanswered({ state: "unsure", attempt: { outcome: "unsure" } }), false); // the article itself: its own row says so
  assert.equal(editUnanswered({ state: "sent", attempt: null }), false);
  assert.match(EDIT_UNANSWERED, /Look there first/);
});
