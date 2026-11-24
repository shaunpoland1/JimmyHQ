// The Articles page: copying an article out, and saying plainly that it cannot download.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { COPY_BY_HAND, SAVE_NOTE, copiedSaid, copyText, fileName, saveRequest } from "../../apps/articles/web/lib/exporting.js";

test("the clipboard is tried first, then the older copy command", async () => {
  const got: string[] = [];
  assert.deepEqual(await copyText("<h1>A</h1>", { clipboard: { writeText: async (t: string) => { got.push(t); } } }), { ok: true, how: "clipboard" });
  assert.deepEqual(got, ["<h1>A</h1>"]);
  const refused = { writeText: async () => { throw new Error("NotAllowedError"); } };
  assert.deepEqual(await copyText("x", { clipboard: refused, copyCommand: () => true }), { ok: true, how: "command" });
});

test("when neither way is allowed it says so and never throws", async () => {
  const refused = { writeText: async () => { throw new Error("NotAllowedError"); } };
  assert.deepEqual(await copyText("x", { clipboard: refused, copyCommand: () => { throw new Error("no"); } }), { ok: false });
  assert.deepEqual(await copyText("x", {}), { ok: false });
  assert.match(copiedSaid({ ok: false }, "markdown"), /shown selected: press Ctrl\+C, or Cmd\+C/);
  assert.equal(copiedSaid({ ok: true }, "html"), "Copied as clean HTML. Paste it where it goes.");
});

test("a file name from the program is made safe, or made from the id", () => {
  assert.equal(fileName("article-12.html", 12, "html"), "article-12.html");
  assert.equal(fileName("../../etc/passwd", 5, "markdown"), "article-5.md");
  assert.equal(fileName("", 7, "html"), "article-7.html");
  assert.equal(fileName("article-3.md", 3, "markdown"), "article-3.md");
});

test("the Markdown is saved under the article's title, or under the program's name when there is none", () => {
  const made = { text: "# A\n\nBody", name: "article-12.md" };
  assert.deepEqual(saveRequest({ id: 12, title: "  Why   bees\nmatter " }, made), { name: "Why bees matter", type: "text/markdown", text: "# A\n\nBody" });
  assert.deepEqual(saveRequest({ id: 12, title: "" }, made), { name: "article-12", type: "text/markdown", text: "# A\n\nBody" });
  assert.deepEqual(saveRequest({ id: 5 }, { text: "x", name: "../../etc/passwd" }), { name: "article-5", type: "text/markdown", text: "x" });
});

test("the page says what Save and Copy do, and no longer says that it cannot download", () => {
  assert.match(SAVE_NOTE, /Save as a file downloads the article as Markdown/);
  assert.match(SAVE_NOTE, /Copy puts it on the clipboard/);
  assert.match(COPY_BY_HAND, /Ctrl\+C/);
  assert.doesNotMatch(`${SAVE_NOTE} ${COPY_BY_HAND}`, /not possible/);
});
