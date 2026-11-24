import assert from "node:assert/strict";
import { test } from "node:test";
import { runSessionEndHooks } from "../src/lib/session.ts";
import type { Fact, Note } from "../src/modules/memory/api.ts";
import { draftFor, dropDraft, hasUnsaved, isDirty, keepDraft } from "../src/modules/memory/lib/drafts.ts";
import {
  NO_FILTERS,
  confidenceLabel,
  confidenceLevel,
  confidenceWords,
  describeActor,
  dropNoteFact,
  dropPagedFact,
  factIdsIn,
  factsQuery,
  filtersActive,
  flattenFacts,
  idsLostByEdit,
  isKind,
  kindChoices,
  parseEvidence,
  patchNoteFact,
  patchPagedFact,
  putFactBack,
} from "../src/modules/memory/lib/facts.ts";
import type { FactPages } from "../src/modules/memory/lib/facts.ts";

const ID = (n: number) => `01JABCDEFGHJKMNPQRSTVWXY${String(n).padStart(2, "0")}`;

function fact(n: number, over: Partial<Fact> = {}): Fact {
  return {
    id: ID(n),
    text: `Fact ${n}`,
    note: "shared/ideas.md",
    line: n,
    kind: "note",
    confidence: 1,
    confirmed: true,
    saved_by: "owner",
    evidence: [],
    created_ms: 1000,
    updated_ms: 1000,
    retired_ms: null,
    retired_reason: null,
    ...over,
  };
}

test("confidence is said in words, from a guess to certain", () => {
  assert.deepEqual([0, 0.1, 0.25, 0.5, 0.7, 0.75, 0.94, 0.95, 1].map(confidenceLevel), [0, 0, 1, 2, 2, 3, 3, 4, 4]);
  assert.equal(confidenceWords(1), "Certain");
  assert.equal(confidenceWords(0.7), "Fairly sure");
  assert.equal(confidenceWords(0), "A guess");
  assert.equal(confidenceLabel(0.7), "Fairly sure (70%)");
  assert.equal(confidenceLabel(Number.NaN), "A guess (0%)");
  assert.equal(confidenceLabel(4), "Certain (100%)");
});

test("the actor on the record is named the way the owner would say it", () => {
  assert.equal(describeActor("owner"), "you");
  assert.equal(describeActor("owner:disk"), "an edit on disk");
  assert.equal(describeActor("system"), "JimmyHQ");
  assert.equal(describeActor("agent:scout"), "agent scout");
  assert.equal(describeActor("token:reader"), "token reader");
  assert.equal(describeActor(""), "unknown");
});

test("an evidence reference is split into where it points, and stays plain text", () => {
  assert.deepEqual(parseEvidence(`tape:${ID(1)}`), { raw: `tape:${ID(1)}`, source: "tape", id: ID(1), label: "Tape event …STVWXY01" });
  assert.equal(parseEvidence("chat:m42").label, "Chat message m42");
  assert.equal(parseEvidence("web:page-7").label, "web page-7");
  assert.deepEqual(parseEvidence("loose words"), { raw: "loose words", source: "", id: "loose words", label: "loose words" });
});

test("the kinds on offer come from the owner's setting, then note, then those in use", () => {
  assert.deepEqual(kindChoices(["preference=shared/preferences.md", "person = shared/people.md", "Bad Kind=x.md", 7], ["trip", "person"]), [
    "preference",
    "person",
    "note",
    "trip",
  ]);
  assert.deepEqual(kindChoices(undefined), ["note"]);
  assert.equal(isKind("preference"), true);
  assert.equal(isKind("Preference"), false);
  assert.equal(isKind(""), false);
});

test("a change to a fact shows in the open note and in every loaded page at once", () => {
  const note: Note = { path: "shared/ideas.md", text: "", revision: "r", size: 0, modified_ms: 0, facts: [fact(1), fact(2)] };
  assert.equal(patchNoteFact(note, ID(1), { confirmed: false }).facts[0]?.confirmed, false);
  assert.equal(patchNoteFact(note, ID(9), { confirmed: false }), note);
  assert.deepEqual(dropNoteFact(note, ID(1)).facts.map((item) => item.id), [ID(2)]);
  assert.equal(dropNoteFact(note, ID(9)), note);

  const pages: FactPages = { pages: [{ facts: [fact(3), fact(2)], next_before: "x" }, { facts: [fact(2), fact(1)], next_before: null }], pageParams: [null, "x"] };
  const patched = patchPagedFact(pages, ID(2), { kind: "person" });
  assert.deepEqual(patched.pages.map((page) => page.facts.map((item) => item.kind)), [["note", "person"], ["person", "note"]]);
  assert.equal(patchPagedFact(pages, ID(9), { kind: "person" }), pages);
  assert.deepEqual(flattenFacts(dropPagedFact(pages, ID(2))).map((item) => item.id), [ID(3), ID(1)]);
  assert.deepEqual(flattenFacts(pages).map((item) => item.id), [ID(3), ID(2), ID(1)]);
  assert.deepEqual(flattenFacts(undefined), []);
});

test("the list filters become the server's query, with nothing sent for 'any'", () => {
  assert.deepEqual(factsQuery(NO_FILTERS), {});
  assert.equal(filtersActive(NO_FILTERS), false);
  const filters = { kind: "person", confirmed: "no" as const, q: "  tea  " };
  assert.deepEqual(factsQuery(filters), { kind: "person", confirmed: false, q: "tea" });
  assert.equal(filtersActive(filters), true);
  assert.deepEqual(factsQuery({ kind: "Not A Kind", confirmed: "yes", q: "" }), { confirmed: true });
});

const BEFORE = `# Ideas\n\n- Likes tea <!-- m:${ID(1)} -->\n- Walks at noon <!-- m:${ID(2)} -->\n  with a friend\n\nFree text.\n`;
const AFTER = `# Ideas\n\n- Likes tea <!-- m:${ID(1)} -->\n\nFree text.\n`;

test("the fact ids in a note's text are found, and those an edit would retire are counted", () => {
  assert.deepEqual(factIdsIn(BEFORE), [ID(1), ID(2)]);
  assert.deepEqual(idsLostByEdit(BEFORE, AFTER), [ID(2)]);
  assert.deepEqual(idsLostByEdit(BEFORE, `${BEFORE}- A new line\n`), []);
});

test("undoing forget puts the fact's line and what was nested under it back where it was", () => {
  assert.equal(putFactBack(AFTER, BEFORE, ID(2)), BEFORE);
});

test("undoing forget keeps whatever else changed in the note since", () => {
  const since = `# Ideas (renamed)\n\n- Likes tea <!-- m:${ID(1)} -->\n\nFree text.\n- Added later\n`;
  const back = putFactBack(since, BEFORE, ID(2)) ?? "";
  assert.ok(back.startsWith("# Ideas (renamed)\n"));
  assert.ok(back.includes(`- Walks at noon <!-- m:${ID(2)} -->\n  with a friend\n`));
  assert.ok(back.endsWith("- Added later\n"));
});

test("a fact is put back at the end when the note has become shorter, and never twice", () => {
  assert.equal(putFactBack("# Ideas\n", BEFORE, ID(2)), `# Ideas\n- Walks at noon <!-- m:${ID(2)} -->\n  with a friend\n`);
  assert.equal(putFactBack("# Ideas", BEFORE, ID(2)), `# Ideas\n- Walks at noon <!-- m:${ID(2)} -->\n  with a friend`);
  assert.equal(putFactBack(BEFORE, BEFORE, ID(2)), null);
  assert.equal(putFactBack(AFTER, BEFORE, ID(7)), null);
});

test("an edit in progress is kept by note until it is dropped or the session ends", () => {
  keepDraft("shared/ideas.md", { text: "typed", baseText: "was", revision: "r1" });
  keepDraft("shared/people.md", { text: "same", baseText: "same", revision: "r2" });
  assert.equal(hasUnsaved("shared/ideas.md"), true);
  assert.equal(hasUnsaved("shared/people.md"), false);
  assert.equal(hasUnsaved("shared/other.md"), false);
  assert.equal(hasUnsaved(null), false);
  assert.equal(isDirty(draftFor("shared/ideas.md")), true);
  assert.equal(isDirty(undefined), false);
  dropDraft("shared/ideas.md");
  assert.equal(draftFor("shared/ideas.md"), undefined);
  runSessionEndHooks();
  assert.equal(draftFor("shared/people.md"), undefined);
});
