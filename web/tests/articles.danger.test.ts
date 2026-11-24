// What cannot be taken back on the Articles page: it asks first in the page's own dialog,
// starts on Cancel, and says what goes and what stays. And every field has a name.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { DROP_ARTICLE, FORGET_REPLIES, askSafely, dismissIdea, safeButton } from "../../apps/articles/web/lib/danger.js";

const views = new URL("../../apps/articles/web/views/", import.meta.url);
const view = (name: string) => readFileSync(new URL(name, views), "utf8");

/** A page whose dialog has a Cancel button, and a dialog that the test answers. */
function fakeUi(answer: string | null) {
  const cancel = { focused: 0, focus() { this.focused += 1; } };
  const asked: Array<{ title: string; intro: string; actions: Array<{ label: string; kind: string }> }> = [];
  const selectors: string[] = [];
  return {
    cancel, asked, selectors,
    ui: {
      dialog: (what: (typeof asked)[number]) => { asked.push(what); return Promise.resolve(answer); },
      document: { querySelector: (selector: string) => { selectors.push(selector); return asked.length ? cancel : null; } },
    },
  };
}

test("a question before a danger starts with the keyboard on Cancel, not on the danger button", async () => {
  const page = fakeUi("Delete them");
  assert.equal(await askSafely(page.ui, "Delete every reply?", "Why.", "Delete them"), true);
  assert.equal(page.cancel.focused, 1);
  assert.deepEqual(page.asked[0]?.actions, [{ label: "Delete them", kind: "danger" }]);
  assert.deepEqual(page.selectors, ['[role="dialog"] .row.end button']); // the first button of the dialog's row: Cancel
  assert.equal(safeButton(null), null);
});

test("Cancel, Esc and a click outside all mean no", async () => {
  assert.equal(await askSafely(fakeUi(null).ui, "Drop?", "Why.", "Drop it"), false);
  assert.equal(await askSafely(fakeUi("Cancel").ui, "Drop?", "Why.", "Drop it"), false);
});

test("deleting the replies says what is deleted for good and what is kept", () => {
  assert.match(FORGET_REPLIES.intro, /Every reply you pasted is deleted for good/);
  assert.match(FORGET_REPLIES.intro, /cannot be brought back/);
  assert.match(FORGET_REPLIES.intro, /ideas already found stay/);
  assert.match(FORGET_REPLIES.intro, /articles started from them/);
});

test("dismissing an idea says it does not come back, and dropping an article that it cannot be picked up again", () => {
  const words = dismissIdea("Compost for clay soil");
  assert.match(words.intro, /Compost for clay soil/);
  assert.match(words.intro, /not proposed again/);
  assert.match(words.intro, /cannot be undone/);
  assert.match(words.intro, /replies it rested on are kept/);
  assert.match(DROP_ARTICLE.intro, /nothing is published/);
  assert.match(DROP_ARTICLE.intro, /cannot be picked up again/);
});

test("the Ideas view offers Delete every reply, and it and Dismiss ask first", () => {
  const ideas = view("ideas.js");
  assert.match(ideas, /askSafely\(\{ dialog, document \}, FORGET_REPLIES\.title[^\n]*\n\s*try \{\n\s*const got = await ctx\.call\("replies\.forget"/);
  assert.ok(ideas.includes('"Delete every reply"'));
  assert.match(ideas, /askSafely\(\{ dialog, document \}, words\.title[^\n]*\n\s*await act\("idea\.dismiss"/);
  assert.equal(ideas.split("idea.dismiss").length, 2); // the one place, behind the question
  assert.equal(ideas.split("replies.forget").length, 2);
});

test("no view asks a danger question that starts on the danger button", () => {
  for (const file of readdirSync(views).map(String)) {
    assert.ok(!/\bask\([^\n]*"danger"\)/.test(view(file)), `${file} asks a danger question with ask`);
  }
  assert.match(view("decide.js"), /askSafely\(\{ dialog, document \}, DROP_ARTICLE\.title[^\n]*\n\s*await run\(ctx, "piece\.drop"/);
});

test("starting an idea reads the site from the row's own list and refuses when none is chosen", () => {
  const ideas = view("ideas.js");
  assert.ok(ideas.includes("ideaStart(idea.id, site.value)"));
  assert.ok(ideas.includes('h("option", { value: "" }, "Choose a site")'));
  assert.ok(!ideas.includes("newForm.site")); // not the New form's site, which starts on the first
});

test("every field of the page has a label a screen reader announces", () => {
  let fields = 0;
  for (const file of readdirSync(views).map(String)) {
    const text = view(file);
    for (const made of text.matchAll(/const (\w+) = h\("(?:input|select|textarea)", \{(?=([^\n]*(?:\n[^\n]*){0,2}))/g)) {
      fields += 1;
      const name = made[1] ?? "";
      const rest = made[2] ?? "";
      const named = /"aria-(label|labelledby|hidden)"/.test(rest)
        || new RegExp(`field\\((?:"[^"]*"|[^;"])*?,\\s*${name}[,)]`).test(text)
        || new RegExp(`h\\("label",[^;]*?\\b${name}\\b`).test(text);
      assert.ok(named, `${file}: the field ${name} has no label`);
    }
    // No field is made without a name to check it by.
    assert.ok(!/(?<!const \w+ = )h\("(?:input|select|textarea)"/.test(text), `${file} makes a field with no name`);
  }
  assert.ok(fields >= 25);
});
