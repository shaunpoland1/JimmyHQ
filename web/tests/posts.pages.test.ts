// The Posts page: pausing and removing a page, and which pages take new work.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { activePages, pausedWords, removalQuestion } from "../../apps/posts/web/lib/pages.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { pageName } from "../../apps/posts/web/lib/lists.js";

const PAGES = [{ id: 1, name: "Garden notes", paused: 0 }, { id: 2, name: "Shed notes", paused: 1 }];

test("a paused page is left out of the pickers for new work", () => {
  assert.deepEqual(activePages(PAGES).map((p: { id: number }) => p.id), [1]);
  assert.deepEqual(activePages(undefined), []);
});

test("a paused brand still has its name everywhere, and only a removed one reads as removed", () => {
  assert.equal(pageName(PAGES, 2), "Shed notes");
  assert.equal(pageName(PAGES, 9), "A brand you removed");
});

test("pausing says what stops and what carries on, and resuming says it takes work again", () => {
  assert.match(pausedWords(PAGES[1]), /Paused Shed notes\. It gets no new work until you resume it/);
  assert.match(pausedWords(PAGES[1]), /queued or booked for it carries on/);
  assert.match(pausedWords(PAGES[0]), /Resumed Garden notes/);
});

test("removing a brand asks first and says what happens to its drafts and its rules", () => {
  const q = removalQuestion({ name: "Shed notes", working: 3, drafts: 2, rules: 1, kept: 4 });
  assert.equal(q.title, "Remove Shed notes?");
  assert.match(q.intro, /3 open pieces are dropped, and 2 drafts among them declined/);
  assert.match(q.intro, /Its 1 rule and what it learned from your decisions are deleted/);
  assert.match(q.intro, /Rules for every brand stay/);
  assert.match(q.intro, /4 posts that went out or were dropped stay in their lists/);
  assert.match(q.intro, /cannot be undone/);
  assert.match(q.intro, /pause the brand instead/);
});

test("a brand with nothing open says so, and one open piece reads as one", () => {
  assert.match(removalQuestion({ name: "Shed notes" }).intro, /no drafts and nothing being worked on/);
  assert.match(removalQuestion({ name: "Shed notes", working: 1 }).intro, /Its 1 open piece is dropped\./);
});

test("the way out of removing a brand keeps the brand and is never called Cancel", () => {
  const q = removalQuestion({ name: "Shed notes" });
  assert.equal(q.yes, "Remove the brand");
  assert.equal(q.no, "Keep the brand");
});

test("the tab, the form and the hints the owner reads say brand, not page", async () => {
  const { readFileSync } = await import("node:fs");
  const read = (file: string) => readFileSync(new URL(`../../apps/posts/web/${file}`, import.meta.url), "utf8");
  assert.ok(read("app.js").includes('["pages", "Brands", pagesView.render]'));
  const views = read("views/pages.js");
  for (const words of ["Add a brand", "A new brand", "This brand's accounts", "Every brand"]) assert.ok(views.includes(words), words);
  for (const old of ["Add a page", "A new page", "Every page", "Set up a page", "A page you removed"]) {
    for (const file of ["views/pages.js", "views/new.js", "views/lists.js", "lib/today.js", "lib/lists.js", "lib/pages.js"]) assert.ok(!read(file).includes(old), `${file} still says ${old}`);
  }
});
