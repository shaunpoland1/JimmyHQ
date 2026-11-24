// Facts: how their details are worded, and how a change is shown on screen
// before the server has answered.

import type { Fact, FactsPage, Note } from "../api.ts";

// ---- wording ----

/** 0 (a guess) to 4 (certain): how many of the four small bars are filled. */
export function confidenceLevel(confidence: number): 0 | 1 | 2 | 3 | 4 {
  if (!Number.isFinite(confidence)) return 0;
  if (confidence >= 0.95) return 4;
  if (confidence >= 0.75) return 3;
  if (confidence >= 0.5) return 2;
  if (confidence >= 0.25) return 1;
  return 0;
}

const CONFIDENCE_WORDS = ["A guess", "Unsure", "Fairly sure", "Very likely", "Certain"] as const;

export function confidenceWords(confidence: number): string {
  return CONFIDENCE_WORDS[confidenceLevel(confidence)];
}

/** "Certain (100%)", for a tooltip and for screen readers. */
export function confidenceLabel(confidence: number): string {
  const percent = Math.round(Math.min(1, Math.max(0, Number.isFinite(confidence) ? confidence : 0)) * 100);
  return `${confidenceWords(confidence)} (${percent}%)`;
}

/** Who did something, from the actor on the record: "you", "an edit on disk", "agent scout". */
export function describeActor(actor: string): string {
  if (actor === "owner") return "you";
  if (actor === "owner:disk") return "an edit on disk";
  if (actor === "system") return "JimmyHQ";
  if (actor === "system:memory") return "mining";
  const at = actor.indexOf(":");
  if (at > 0) {
    const role = actor.slice(0, at);
    const name = actor.slice(at + 1);
    if (!name) return role;
    if (role === "agent") return `agent ${name}`;
    if (role === "token") return `token ${name}`;
    return `${role} ${name}`;
  }
  return actor || "unknown";
}

/** The actor mining saves a fact as. */
export const MINING_ACTOR = "system:memory";

/** A fact mining saved without asking that the owner has not confirmed yet. */
export function awaitsReview(fact: Pick<Fact, "saved_by" | "confirmed" | "retired_ms">): boolean {
  return fact.saved_by === MINING_ACTOR && !fact.confirmed && fact.retired_ms === null;
}

export type EvidenceRef = { raw: string; source: string; id: string; label: string };

const EVIDENCE_SOURCES: Record<string, string> = { tape: "Tape event", chat: "Chat message", bench: "Bench card", note: "Note" };

/** One evidence reference, split into where it points. Shown as plain text for now. */
export function parseEvidence(raw: string): EvidenceRef {
  const at = raw.indexOf(":");
  if (at <= 0 || at === raw.length - 1) return { raw, source: "", id: raw, label: raw };
  const source = raw.slice(0, at);
  const id = raw.slice(at + 1);
  const name = EVIDENCE_SOURCES[source] ?? source;
  // A long id is shortened to its end, which is the part that differs between neighbours.
  const short = id.length > 12 ? `…${id.slice(-8)}` : id;
  return { raw, source, id, label: `${name} ${short}` };
}

// ---- kinds ----

const KIND = /^[a-z][a-z0-9_-]{0,39}$/;
export const DEFAULT_KIND = "note";

export function isKind(value: string): boolean {
  return KIND.test(value);
}

/** The kinds to offer: those the owner's `memory.kind_notes` setting names, then "note", then any already in use. */
export function kindChoices(setting: unknown, inUse: readonly string[] = []): string[] {
  const kinds: string[] = [];
  const add = (kind: string) => {
    if (isKind(kind) && !kinds.includes(kind)) kinds.push(kind);
  };
  if (Array.isArray(setting)) {
    for (const item of setting) if (typeof item === "string") add((item.split("=", 1)[0] ?? "").trim());
  }
  add(DEFAULT_KIND);
  inUse.forEach(add);
  return kinds;
}

// ---- showing a change at once ----

export type FactPatch = Partial<Pick<Fact, "text" | "kind" | "confirmed">>;

export function patchNoteFact(note: Note, id: string, patch: FactPatch): Note {
  if (!note.facts.some((fact) => fact.id === id)) return note;
  return { ...note, facts: note.facts.map((fact) => (fact.id === id ? { ...fact, ...patch } : fact)) };
}

export function dropNoteFact(note: Note, id: string): Note {
  if (!note.facts.some((fact) => fact.id === id)) return note;
  return { ...note, facts: note.facts.filter((fact) => fact.id !== id) };
}

export type FactPages = { pages: FactsPage[]; pageParams: unknown[] };

export function patchPagedFact(data: FactPages, id: string, patch: FactPatch): FactPages {
  if (!data.pages.some((page) => page.facts.some((fact) => fact.id === id))) return data;
  return {
    ...data,
    pages: data.pages.map((page) => ({ ...page, facts: page.facts.map((fact) => (fact.id === id ? { ...fact, ...patch } : fact)) })),
  };
}

export function dropPagedFact(data: FactPages, id: string): FactPages {
  if (!data.pages.some((page) => page.facts.some((fact) => fact.id === id))) return data;
  return { ...data, pages: data.pages.map((page) => ({ ...page, facts: page.facts.filter((fact) => fact.id !== id) })) };
}

export function flattenFacts(data: FactPages | undefined): Fact[] {
  const seen = new Set<string>();
  const out: Fact[] = [];
  for (const page of data?.pages ?? []) {
    for (const fact of page.facts) {
      // A fact changed between two page reads can turn up on both.
      if (seen.has(fact.id)) continue;
      seen.add(fact.id);
      out.push(fact);
    }
  }
  return out;
}

/** The filters of the list of all facts, as the server's query. */
export type FactFilters = { kind: string; confirmed: "any" | "yes" | "no"; q: string };
export const NO_FILTERS: FactFilters = { kind: "", confirmed: "any", q: "" };

export function filtersActive(filters: FactFilters): boolean {
  return filters.kind !== "" || filters.confirmed !== "any" || filters.q.trim() !== "";
}

export function factsQuery(filters: FactFilters): { kind?: string; confirmed?: boolean; q?: string } {
  const query: { kind?: string; confirmed?: boolean; q?: string } = {};
  if (filters.kind && isKind(filters.kind)) query.kind = filters.kind;
  if (filters.confirmed !== "any") query.confirmed = filters.confirmed === "yes";
  const q = filters.q.trim().slice(0, 200);
  if (q) query.q = q;
  return query;
}

// ---- fact ids in a note's text ----

const ID_COMMENT = /<!--[ \t]*m:([0-9A-HJKMNP-TV-Z]{26})[ \t]*-->/g;

/** Every fact id written in a note's text, in order. */
export function factIdsIn(text: string): string[] {
  return [...text.matchAll(ID_COMMENT)].map((match) => match[1] ?? "");
}

/** The ids the note had when it was opened that the edited text no longer has: saving retires those facts. */
export function idsLostByEdit(before: string, after: string): string[] {
  const kept = new Set(factIdsIn(after));
  return [...new Set(factIdsIn(before))].filter((id) => !kept.has(id));
}

function splitLines(text: string): string[] {
  return text.split("\n");
}

/**
 * Undoing "forget": the note as it is now, with the forgotten fact's line (and
 * what was nested under it) put back where it was. Typing a retired fact's
 * line back, id included, revives the same fact. null when the line cannot be
 * found in the earlier text, or the id is somehow already there.
 */
export function putFactBack(current: string, before: string, id: string): string | null {
  if (factIdsIn(current).includes(id)) return null;
  const old = splitLines(before);
  const start = old.findIndex((line) => factIdsIn(line).includes(id));
  if (start < 0) return null;
  let end = start + 1;
  // Indented lines directly under a fact belong to it.
  while (end < old.length && /^[ \t]+\S/.test(old[end] ?? "")) end += 1;
  const block = old.slice(start, end);
  const now = splitLines(current);
  // A text that ends with a newline has an empty last piece: never insert after it.
  const last = now.length > 0 && now[now.length - 1] === "" ? now.length - 1 : now.length;
  const at = Math.min(start, last);
  return [...now.slice(0, at), ...block, ...now.slice(at)].join("\n");
}
