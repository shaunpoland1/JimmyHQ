import assert from "node:assert/strict";
import { test } from "node:test";
import { baseName, breadcrumbs, cleanPath, downloadHref, folderSummary, isFileText, isMarkdownName, joinPath, labelledCrumbs, looksBinary, parentPath, partialNote, rootLabel, shownName, sortItems, zipLabel } from "../src/modules/village/lib/files.ts";
import { safeHref } from "../src/modules/village/lib/markdown.ts";

test("paths are joined without doubled or leading slashes", () => {
  assert.equal(joinPath("", "notes.md"), "notes.md");
  assert.equal(joinPath("reports/", "/march"), "reports/march");
  assert.equal(joinPath("a/b", "c"), "a/b/c");
  assert.equal(cleanPath("./a//b\\c/"), "a/b/c");
});

test("the parent of a path is the folder above it, and the top has none", () => {
  assert.equal(parentPath("a/b/c.md"), "a/b");
  assert.equal(parentPath("a"), "");
  assert.equal(parentPath(""), "");
  assert.equal(baseName("a/b/c.md"), "c.md");
});

test("the breadcrumb trail starts at the top folder and each part is a place to go back to", () => {
  assert.deepEqual(breadcrumbs("", "Wren's files"), [{ label: "Wren's files", path: "" }]);
  assert.deepEqual(breadcrumbs("reports/march/summary.md", "Shared folders"), [
    { label: "Shared folders", path: "" },
    { label: "reports", path: "reports" },
    { label: "march", path: "reports/march" },
    { label: "summary.md", path: "reports/march/summary.md" },
  ]);
});

test("the top folder is named for whose it is", () => {
  assert.equal(rootLabel("own", "Wren"), "Wren's files");
  assert.equal(rootLabel("shared", "Wren"), "Shared folders");
});

test("markdown files are recognised by their name and other files are shown as plain text", () => {
  assert.equal(isMarkdownName("notes.md"), true);
  assert.equal(isMarkdownName("README.MARKDOWN"), true);
  assert.equal(isMarkdownName("data.csv"), false);
  assert.equal(isMarkdownName("md"), false);
});

test("the shared route's answer is told apart as a file when it carries text", () => {
  assert.equal(isFileText({ path: "a.md", text: "hello" }), true);
  assert.equal(isFileText({ path: "", items: [] }), false);
});

test("text full of replacement marks or control characters is treated as a binary file", () => {
  assert.equal(looksBinary("plain words\nand a second line\twith a tab"), false);
  assert.equal(looksBinary(""), false);
  assert.equal(looksBinary("������abc"), true);
  assert.equal(looksBinary("abc\u0000\u0001\u0002def"), true);
});

test("a file cut short says how much is shown and that the rest is missing, and a whole file says nothing", () => {
  const size = (bytes: number) => `${bytes} bytes`;
  assert.equal(partialNote({ truncated: false, size: 10, text: "0123456789" }, size), null);
  assert.equal(partialNote({ size: 10, text: "0123456789" }, size), null);
  const note = partialNote({ truncated: true, size: 900, text: "x".repeat(300) }, size);
  assert.match(note ?? "", /first 300 bytes of 900 bytes is shown/);
  assert.match(note ?? "", /rest is cut off/);
  assert.match(partialNote({ truncated: true, text: "abc" }, size) ?? "", /first 3 bytes is shown/);
});

test("a folder is summarised by how many folders and files it holds", () => {
  assert.equal(folderSummary([]), "Empty");
  assert.equal(folderSummary([{ name: "a", path: "a", dir: true, size: null }]), "1 folder");
  assert.equal(
    folderSummary([
      { name: "a", path: "a", dir: true, size: null },
      { name: "b", path: "b", dir: false, size: 1 },
      { name: "c", path: "c", dir: false, size: 1 },
    ]),
    "1 folder, 2 files",
  );
});

test("folders are listed before files, each by name", () => {
  const names = sortItems([
    { name: "zeta.md", path: "zeta.md", dir: false, size: 1 },
    { name: "Beta", path: "Beta", dir: true, size: null },
    { name: "alpha.md", path: "alpha.md", dir: false, size: 1 },
    { name: "alpha", path: "alpha", dir: true, size: null },
  ]).map((item) => item.name);
  assert.deepEqual(names, ["alpha", "Beta", "alpha.md", "zeta.md"]);
});

test("only web and mail links in a file are kept, so nothing it says can run", () => {
  assert.equal(safeHref("https://example.com/a"), "https://example.com/a");
  assert.equal(safeHref("mailto:someone@example.com"), "mailto:someone@example.com");
  assert.equal(safeHref("javascript:alert(1)"), null);
  assert.equal(safeHref("java\tscript:alert(1)"), null);
  assert.equal(safeHref("data:text/html,hi"), null);
  assert.equal(safeHref("/village/agent-a"), null);
  assert.equal(safeHref("https://user:pass@example.com/"), null);
  assert.equal(safeHref(undefined), null);
});

test("a download link points at the right route for each folder and keeps odd names safe", () => {
  assert.equal(downloadHref("own", "agent-a", "drafts/one.md", false), "/api/village/agents/agent-a/files/download?path=drafts%2Fone.md");
  assert.equal(downloadHref("own", "agent-a", "", true), "/api/village/agents/agent-a/files/zip?path=");
  assert.equal(downloadHref("shared", "agent-a", "agent-a/my notes.md", false), "/api/village/shared/download?path=agent-a%2Fmy%20notes.md");
  assert.equal(downloadHref("shared", "agent-a", "", true), "/api/village/shared/zip?path=");
  assert.equal(downloadHref("own", "agent-a", "a&b#c.md", false).endsWith("path=a%26b%23c.md"), true);
});

test("the zip button says whether it takes one folder or everything", () => {
  assert.equal(zipLabel(""), "Download all as a zip");
  assert.equal(zipLabel("drafts"), "Download this folder as a zip");
});

test("the top folders of the shared area are named by the agent's label, not its id", () => {
  const everyone = [{ id: "agent-a", name: "Wren", role: "writer" }, { id: "agent-b", name: null, role: "" }];
  assert.equal(shownName("shared", "", "agent-a", everyone), "Wren");
  assert.equal(shownName("shared", "", "agent-b", everyone), "Agent B");
  assert.equal(shownName("shared", "", "agent-27", everyone), "Agent 27");
  assert.equal(shownName("shared", "agent-a", "notes", everyone), "notes");
  assert.equal(shownName("own", "", "agent-a", everyone), "agent-a");
  const trail = breadcrumbs("agent-a/notes", "Shared folders");
  assert.deepEqual(labelledCrumbs("shared", trail, everyone).map((crumb) => crumb.label), ["Shared folders", "Wren", "notes"]);
  assert.deepEqual(labelledCrumbs("shared", trail, everyone).map((crumb) => crumb.path), ["", "agent-a", "agent-a/notes"]);
  assert.deepEqual(labelledCrumbs("own", trail, everyone), trail);
});
