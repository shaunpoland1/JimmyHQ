import assert from "node:assert/strict";
import { test } from "node:test";
import type { Tree } from "../src/modules/memory/api.ts";
import { folderOf, isNotePath, nameOf, parseMemoryRoute, parseStoredPath, pathProblem, starterText, takenBy, tidyNewPath, titleOf } from "../src/modules/memory/lib/paths.ts";
import { buildTree, filterTree, findNote, folderChoices, foldersAbove, parseCollapsed, treeRows } from "../src/modules/memory/lib/tree.ts";
import type { TreeNode } from "../src/modules/memory/lib/tree.ts";

test("an ordinary note path is accepted", () => {
  for (const path of ["shared/ideas.md", "ideas.md", "agents/scout/notes.md", "shared/sub folder/y.md", "shared/Café plans.MD", "a/history/b.md"]) {
    assert.equal(pathProblem(path), null, path);
    assert.equal(isNotePath(path), true, path);
  }
});

test("every path the server refuses is refused here first, with a sentence", () => {
  const refused = [
    "",
    "/shared/ideas.md",
    "~/ideas.md",
    "shared\\ideas.md",
    "C:/ideas.md",
    "shared/what?.md",
    "shared/a:b.md",
    'shared/"x".md',
    "shared//ideas.md",
    "shared/ideas.md/",
    "shared/../ideas.md",
    "./ideas.md",
    "shared/.hidden.md",
    "shared/ ideas.md",
    "shared/ideas.md ",
    "shared/folder./ideas.md",
    "shared/con.md",
    "NUL/ideas.md",
    "shared/com1.txt.md",
    "shared/ideas.txt",
    "shared/ideas",
    "shared/.md",
    "shared/tab\tname.md",
    `shared/${"n".repeat(121)}.md`,
    `${"a/".repeat(12)}x.md`,
    `${"folder/".repeat(60)}x.md`,
    "shared/\ud800.md",
  ];
  for (const path of refused) {
    const problem = pathProblem(path);
    assert.equal(typeof problem, "string", JSON.stringify(path));
    assert.ok((problem ?? "").length > 10, JSON.stringify(path));
  }
});

test("a name of 120 characters fits unless its bytes are too many for a disk", () => {
  assert.equal(pathProblem(`shared/${"n".repeat(117)}.md`), null);
  assert.match(pathProblem(`shared/${"\u{1F600}".repeat(70)}.md`) ?? "", /too long for a disk/);
});

test("twelve parts is the deepest a note can be", () => {
  assert.equal(pathProblem(`${"a/".repeat(11)}x.md`), null);
  assert.match(pathProblem(`${"a/".repeat(12)}x.md`) ?? "", /12 folders deep/);
});

test("a new path gets its ending added and its outer spaces removed", () => {
  assert.equal(tidyNewPath("  shared/ideas  "), "shared/ideas.md");
  assert.equal(tidyNewPath("shared/ideas.md"), "shared/ideas.md");
  assert.equal(tidyNewPath("shared/Ideas.MD"), "shared/Ideas.MD");
  assert.equal(tidyNewPath("shared/"), "shared/");
  assert.equal(tidyNewPath(""), "");
});

test("a spelling that differs only in case or accent form is taken by the one already there", () => {
  const existing = ["shared/Ideas.md", "shared/caf\u00e9.md"];
  assert.equal(takenBy("shared/ideas.md", existing), "shared/Ideas.md");
  assert.equal(takenBy("shared/cafe\u0301.md", existing), "shared/caf\u00e9.md");
  assert.equal(takenBy("shared/other.md", existing), null);
});

test("a path splits into folder, name and title", () => {
  assert.equal(folderOf("agents/scout/notes.md"), "agents/scout");
  assert.equal(folderOf("notes.md"), "");
  assert.equal(nameOf("agents/scout/notes.md"), "notes.md");
  assert.equal(titleOf("shared/about-me.md"), "about-me");
  assert.equal(starterText("shared/trip-ideas.md"), "# Trip ideas\n\n");
});

test("only a usable note path is taken from the address or from storage", () => {
  assert.equal(parseMemoryRoute("shared/ideas.md"), "shared/ideas.md");
  assert.equal(parseMemoryRoute("shared"), null);
  assert.equal(parseMemoryRoute("../secret.md"), null);
  assert.equal(parseMemoryRoute(null), null);
  assert.equal(parseStoredPath("shared/ideas.md"), "shared/ideas.md");
  assert.equal(parseStoredPath(null), null);
  assert.equal(parseStoredPath(7), undefined);
  assert.equal(parseStoredPath("/etc/x.md"), undefined);
});

const TREE: Tree = {
  folders: ["agents", "agents/scout", "shared"],
  notes: [
    { path: "shared/people.md", size: 10, modified_ms: 1, facts: 3 },
    { path: "shared/About-me.md", size: 10, modified_ms: 2, facts: 1 },
    { path: "shared/trips/japan.md", size: 10, modified_ms: 3, facts: 2 },
    { path: "top.md", size: 10, modified_ms: 4, facts: 0 },
  ],
};

function names(nodes: readonly TreeNode[]): string[] {
  return nodes.map((node) => `${node.type}:${node.name}`);
}

test("flat paths become nested folders, folders first, with counts summed upward", () => {
  const tree = buildTree(TREE);
  assert.deepEqual(names(tree), ["folder:agents", "folder:shared", "note:top.md"]);
  const shared = tree[1];
  assert.ok(shared && shared.type === "folder");
  assert.deepEqual(names(shared.children), ["folder:trips", "note:About-me.md", "note:people.md"]);
  assert.equal(shared.notes, 3);
  assert.equal(shared.facts, 6);
  const agents = tree[0];
  assert.ok(agents && agents.type === "folder");
  assert.deepEqual(names(agents.children), ["folder:scout"]);
  assert.equal(agents.notes, 0);
});

test("a folder that is not listed but holds a note still appears", () => {
  const tree = buildTree({ folders: [], notes: [{ path: "a/b/c.md", size: 1, modified_ms: 1, facts: 1 }] });
  assert.deepEqual(treeRows(tree, new Set()).map((row) => `${row.depth}:${row.node.path}`), ["0:a", "1:a/b", "2:a/b/c.md"]);
});

test("a collapsed folder hides what is under it", () => {
  const rows = treeRows(buildTree(TREE), new Set(["shared", "agents"]));
  assert.deepEqual(rows.map((row) => row.node.path), ["agents", "shared", "top.md"]);
  assert.equal(rows[1]?.open, false);
});

test("filtering keeps matching notes and their folders, and opens collapsed ones", () => {
  const kept = filterTree(buildTree(TREE), "  JAPAN ");
  const rows = treeRows(kept, new Set(["shared"]), true);
  assert.deepEqual(rows.map((row) => row.node.path), ["shared", "shared/trips", "shared/trips/japan.md"]);
  const shared = kept[0];
  assert.ok(shared && shared.type === "folder");
  assert.equal(shared.notes, 1);
  assert.equal(shared.facts, 2);
  assert.equal(filterTree(buildTree(TREE), "").length, 3);
  assert.deepEqual(filterTree(buildTree(TREE), "nothing like this"), []);
});

test("the folders above a note, the folder choices and the stored collapsed set", () => {
  assert.deepEqual(foldersAbove("shared/trips/japan.md"), ["shared", "shared/trips"]);
  assert.deepEqual(foldersAbove("top.md"), []);
  assert.deepEqual(folderChoices(TREE), ["agents", "agents/scout", "shared", "shared/trips"]);
  assert.deepEqual(parseCollapsed(["a", "b"]), ["a", "b"]);
  assert.equal(parseCollapsed(["a", 1]), undefined);
  assert.equal(parseCollapsed("a"), undefined);
  assert.equal(findNote(TREE, "top.md")?.modified_ms, 4);
  assert.equal(findNote(TREE, null), undefined);
});
