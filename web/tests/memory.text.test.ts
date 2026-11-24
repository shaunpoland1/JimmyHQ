import assert from "node:assert/strict";
import { test } from "node:test";
import type { Passage, ScanResult } from "../src/modules/memory/api.ts";
import { describeDiff, diffText } from "../src/modules/memory/lib/diff.ts";
import { isTyping, memoryCommand } from "../src/modules/memory/lib/keys.ts";
import { hideFactIds, onlyHeadings, safeNoteHref } from "../src/modules/memory/lib/markdown.ts";
import { countLine, describeMethods, groupPassages, searchText, trimmedLine, whyLabel } from "../src/modules/memory/lib/search.ts";
import { changeFrom, describeScan, vectorProgress } from "../src/modules/memory/lib/status.ts";

const ID = "01JABCDEFGHJKMNPQRSTVWXY01";

test("two versions of a note are compared line by line", () => {
  const diff = diffText("a\nb\nc\nd\n", "a\nB\nc\nd\ne\n");
  assert.equal(diff.same, false);
  assert.equal(diff.added, 2);
  assert.equal(diff.removed, 1);
  assert.deepEqual(diff.lines, [
    { type: "removed", line: 2, text: "b" },
    { type: "added", line: 2, text: "B" },
    { type: "added", line: 5, text: "e" },
  ]);
  assert.equal(describeDiff(diff), "It has 2 lines yours does not, and lacks 1 line of yours.");
});

test("the same text, or one that differs only in line endings, is the same", () => {
  assert.equal(diffText("a\nb\n", "a\nb\n").same, true);
  assert.equal(diffText("a\r\nb\r\n", "a\nb\n").same, true);
  assert.equal(describeDiff(diffText("", "")), "It has the same text as yours.");
});

test("only added or only removed lines are described on their own", () => {
  assert.equal(describeDiff(diffText("a\n", "a\nb\n")), "It has 1 line yours does not.");
  assert.equal(describeDiff(diffText("a\nb\nc\n", "a\n")), "It lacks 2 lines of yours.");
});

test("a long difference is counted in full but previewed only in part", () => {
  const mine = Array.from({ length: 40 }, (_, index) => `mine ${index}`).join("\n");
  const theirs = Array.from({ length: 30 }, (_, index) => `theirs ${index}`).join("\n");
  const diff = diffText(mine, theirs, 5);
  assert.equal(diff.removed, 40);
  assert.equal(diff.added, 30);
  assert.equal(diff.lines.length, 5);
  assert.equal(diff.more, true);
});

test("a very large difference is still counted without matching every line", () => {
  const mine = Array.from({ length: 700 }, (_, index) => `m${index}`).join("\n");
  const theirs = Array.from({ length: 700 }, (_, index) => `t${index}`).join("\n");
  const diff = diffText(`same\n${mine}\nend`, `same\n${theirs}\nend`);
  assert.equal(diff.removed, 700);
  assert.equal(diff.added, 700);
  assert.equal(diff.lines[0]?.line, 2);
});

const SCREEN = { hasNote: true, editing: false };

test("single keys search, start a note and edit, when not typing", () => {
  assert.equal(memoryCommand({ ...SCREEN, key: "/" }), "search");
  assert.equal(memoryCommand({ ...SCREEN, key: "n" }), "newNote");
  assert.equal(memoryCommand({ ...SCREEN, key: "N", shiftKey: true }), "newNote");
  assert.equal(memoryCommand({ ...SCREEN, key: "e" }), "edit");
  assert.equal(memoryCommand({ ...SCREEN, key: "e", hasNote: false }), null);
  assert.equal(memoryCommand({ ...SCREEN, key: "e", editing: true }), null);
  assert.equal(memoryCommand({ ...SCREEN, key: "x" }), null);
});

test("no key acts while typing in a field, under a dialog, outside the screen or when held", () => {
  for (const key of ["/", "n", "e"]) {
    assert.equal(memoryCommand({ ...SCREEN, key, targetTag: "INPUT" }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, targetTag: "textarea" }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, targetTag: "SELECT" }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, editable: true }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, dialogOpen: true }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, outside: true }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, repeat: true }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, ctrlKey: true }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, metaKey: true }), null);
    assert.equal(memoryCommand({ ...SCREEN, key, altKey: true }), null);
  }
  assert.equal(isTyping({ targetTag: "input" }), true);
  assert.equal(isTyping({ targetTag: "BUTTON" }), false);
});

test("saving and leaving are the editor's keys: they work in its text area and only while editing", () => {
  const editing = { hasNote: true, editing: true };
  assert.equal(memoryCommand({ ...editing, key: "s", metaKey: true, targetTag: "TEXTAREA", inEditor: true }), "save");
  assert.equal(memoryCommand({ ...editing, key: "S", ctrlKey: true, targetTag: "TEXTAREA", inEditor: true }), "save");
  assert.equal(memoryCommand({ ...editing, key: "s", ctrlKey: true, targetTag: "BUTTON" }), "save");
  assert.equal(memoryCommand({ ...editing, key: "Escape", targetTag: "TEXTAREA", inEditor: true }), "leaveEdit");
  assert.equal(memoryCommand({ ...editing, key: "Escape", targetTag: "BODY" }), "leaveEdit");
  // In another field the keys keep their usual meaning.
  assert.equal(memoryCommand({ ...editing, key: "s", ctrlKey: true, targetTag: "INPUT" }), null);
  assert.equal(memoryCommand({ ...editing, key: "Escape", targetTag: "INPUT" }), null);
  // Not editing, under a dialog, outside the screen: nothing.
  assert.equal(memoryCommand({ ...SCREEN, key: "s", ctrlKey: true }), null);
  assert.equal(memoryCommand({ ...SCREEN, key: "Escape" }), null);
  assert.equal(memoryCommand({ ...editing, key: "s", ctrlKey: true, inEditor: true, targetTag: "TEXTAREA", dialogOpen: true }), null);
  assert.equal(memoryCommand({ ...editing, key: "Escape", outside: true }), null);
  assert.equal(memoryCommand({ ...editing, key: "s", ctrlKey: true, altKey: true }), null);
  assert.equal(memoryCommand({ ...editing, key: "s", ctrlKey: true, repeat: true, inEditor: true, targetTag: "TEXTAREA" }), null);
});

test("a link in a note opens only a web or mail address", () => {
  assert.equal(safeNoteHref("https://example.com/a?b=1"), "https://example.com/a?b=1");
  assert.equal(safeNoteHref("http://example.com"), "http://example.com/");
  assert.equal(safeNoteHref("mailto:someone@example.com"), "mailto:someone@example.com");
  for (const href of [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "java\tscript:alert(1)",
    " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:x",
    "file:///etc/passwd",
    "people.md",
    "/api/auth/logout",
    "#/settings",
    "//example.com/x",
    "https://user:secret@example.com/",
    "http://",
    "mailto:",
    "",
    null,
    undefined,
  ]) {
    assert.equal(safeNoteHref(href), null, String(href));
  }
});

test("fact id comments are hidden for reading, except inside a code block", () => {
  const text = `# Ideas\n- Likes tea <!-- m:${ID} -->\n- Trailing space <!--m:${ID}-->  \n\`\`\`\n- code <!-- m:${ID} -->\n\`\`\`\n- After <!-- m:${ID} -->\n`;
  assert.equal(hideFactIds(text), `# Ideas\n- Likes tea\n- Trailing space  \n\`\`\`\n- code <!-- m:${ID} -->\n\`\`\`\n- After\n`);
  assert.equal(hideFactIds("- An ordinary <!-- comment -->"), "- An ordinary <!-- comment -->");
});

test("a note with only headings and comments has nothing to read yet", () => {
  assert.equal(onlyHeadings("# About me\n\n<!-- What this note\nis for. -->\n"), true);
  assert.equal(onlyHeadings(`# About me\n\n- A fact <!-- m:${ID} -->\n`), false);
  assert.equal(onlyHeadings(""), true);
});

function passage(n: number, note: string, over: Partial<Passage> = {}): Passage {
  return { id: `c:${n}`, text: `Passage ${n}`, note, heading: "", fact_id: null, score: 1 / n, why: "keyword", confirmed: null, chars: 9, ...over };
}

test("search results are grouped by note in the order of each note's best match", () => {
  const groups = groupPassages([passage(1, "b.md"), passage(2, "a.md"), passage(3, "b.md")]);
  assert.deepEqual(groups.map((group) => group.note), ["b.md", "a.md"]);
  assert.deepEqual(groups[0]?.passages.map((item) => item.rank), [1, 3]);
  assert.deepEqual(groupPassages([]), []);
  assert.equal(countLine(3, 2), "3 passages in 2 notes");
  assert.equal(countLine(1, 1), "1 passage in 1 note");
  assert.equal(countLine(0, 0), "Nothing matched.");
});

test("a search says which methods ran, and why meaning search did not", () => {
  const reason = "No embeddings model is chosen.";
  assert.deepEqual(describeMethods({ methods: ["keyword"], meaning: { used: false, reason, model: null } }), {
    line: "Searched by words only. Meaning search did not run.",
    reason,
    model: null,
  });
  assert.deepEqual(describeMethods({ methods: ["keyword", "meaning"], meaning: { used: true, reason: null, model: "local/embedder" } }), {
    line: "Searched by words and by meaning.",
    reason: null,
    model: "local/embedder",
  });
  assert.match(describeMethods({ methods: [], meaning: { used: false, reason: null, model: null } }).line, /Nothing was searched/);
  assert.equal(whyLabel("both").label, "words + meaning");
  assert.equal(whyLabel("keyword").label, "words");
  assert.equal(whyLabel("meaning").label, "related");
});

test("an empty search is not sent, and left-out matches are mentioned", () => {
  assert.equal(searchText("   "), null);
  assert.equal(searchText("  tea  "), "tea");
  assert.equal(searchText("x".repeat(5000))?.length, 4000);
  assert.equal(trimmedLine(0), null);
  assert.equal(trimmedLine(1), "1 more match was left out to keep the result short.");
  assert.match(trimmedLine(3) ?? "", /^3 more matches/);
});

const SCAN: ScanResult = {
  added: 0, changed: 0, removed: 0, moved: 0, facts_added: 0, facts_edited: 0, facts_retired: 0,
  at_ms: 1000, notes: 8, ids_written: 0, deferred: 0, skipped: 0, took_ms: 0,
};

test("a scan is described by what it changed", () => {
  assert.equal(describeScan(SCAN), "Nothing had changed. 8 notes looked at.");
  assert.equal(describeScan({ ...SCAN, added: 1, facts_added: 2, skipped: 1, notes: 1 }), "1 note added, 2 facts added, 1 file skipped. 1 note looked at.");
  assert.equal(describeScan({ ...SCAN, reindexed: 8 }), "Nothing had changed. 8 notes looked at. The index was rebuilt from 8 notes.");
});

test("meaning search progress is given only when a model is in use and there are passages", () => {
  const meaning = { on: true, reason: null, model: "local/embedder", local_only: true, ready: 3, waiting: 2 };
  assert.equal(vectorProgress({ counts: { notes: 1, facts: 1, chunks: 5, chunks_with_vectors: 3 }, meaning_search: meaning }), "3 of 5 passages are ready for meaning search.");
  assert.equal(vectorProgress({ counts: { notes: 1, facts: 1, chunks: 5, chunks_with_vectors: 5 }, meaning_search: meaning }), "All 5 passages are ready for meaning search.");
  assert.equal(vectorProgress({ counts: { notes: 1, facts: 1, chunks: 0, chunks_with_vectors: 0 }, meaning_search: meaning }), null);
  assert.equal(vectorProgress({ counts: { notes: 1, facts: 1, chunks: 5, chunks_with_vectors: 0 }, meaning_search: { ...meaning, model: null } }), null);
});

test("a live memory event names the notes to read again, and a search receipt names none", () => {
  assert.deepEqual(changeFrom({ kind: "memory.fact.edited", payload: { fact_id: ID, note: "a.md", from_note: "b.md" } }), { notes: ["a.md", "b.md"], movedTo: null });
  assert.deepEqual(changeFrom({ kind: "memory.note.removed", payload: { note: "a.md", moved_to: "c.md", reason: null } }), {
    notes: ["a.md", "c.md"],
    movedTo: { from: "a.md", to: "c.md" },
  });
  assert.deepEqual(changeFrom({ kind: "memory.note.removed", payload: { note: "a.md", moved_to: null } }), { notes: ["a.md"], movedTo: null });
  assert.equal(changeFrom({ kind: "memory.recalled", payload: { caller: "memory.search" } }), null);
  assert.equal(changeFrom({ kind: "bench.filed", payload: { note: "a.md" } }), null);
});

test("a difference can be described against something other than the editor", () => {
  assert.equal(describeDiff(diffText("a\n", "a\nb\n"), "The version on disk"), "The version on disk has 1 line yours does not.");
  assert.equal(describeDiff(diffText("a\n", "a\n"), "It", "the note now"), "It has the same text as the note now.");
});
