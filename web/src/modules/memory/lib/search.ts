// Search results: grouped by note, and said plainly how they were found.

import type { Passage, SearchResult, Why } from "../api.ts";

export const MAX_QUERY = 4000;

/** What is typed in the search box, as it is sent: null when there is nothing to search for. */
export function searchText(typed: string): string | null {
  const text = typed.trim().slice(0, MAX_QUERY);
  return text ? text : null;
}

export type RankedPassage = Passage & { /** 1 for the best match. */ rank: number };
export type PassageGroup = { note: string; passages: RankedPassage[] };

/** Passages gathered under their note. Notes are in the order of their best passage; passages keep their rank. */
export function groupPassages(passages: readonly Passage[]): PassageGroup[] {
  const groups: PassageGroup[] = [];
  const byNote = new Map<string, PassageGroup>();
  passages.forEach((passage, index) => {
    let group = byNote.get(passage.note);
    if (!group) {
      group = { note: passage.note, passages: [] };
      byNote.set(passage.note, group);
      groups.push(group);
    }
    group.passages.push({ ...passage, rank: index + 1 });
  });
  return groups;
}

export type SplitResult = {
  /** Passages that share a word with the search, ranked. */
  matches: PassageGroup[];
  /** Passages found by meaning alone, ranked after the matches. */
  related: PassageGroup[];
  matchCount: number;
  relatedCount: number;
};

/**
 * Matches (they share a word with the search) apart from passages that only seem related in
 * meaning, each gathered under their note. Ranks run on from the matches into the related ones.
 */
export function splitPassages(passages: readonly Passage[]): SplitResult {
  const matches = passages.filter((p) => p.why !== "meaning");
  const related = passages.filter((p) => p.why === "meaning");
  const later = (groups: PassageGroup[], by: number) =>
    groups.map((g) => ({ note: g.note, passages: g.passages.map((p) => ({ ...p, rank: p.rank + by })) }));
  return {
    matches: groupPassages(matches),
    related: later(groupPassages(related), matches.length),
    matchCount: matches.length,
    relatedCount: related.length,
  };
}

/** "2 matches in 1 note", "No passage contains these words.", and how many are only related. */
export function splitLine(split: Pick<SplitResult, "matches" | "matchCount" | "relatedCount">): string {
  const { matchCount, relatedCount } = split;
  if (matchCount === 0 && relatedCount === 0) return "Nothing matched.";
  const notes = split.matches.length;
  const head =
    matchCount === 0
      ? "No passage contains these words."
      : `${matchCount} ${matchCount === 1 ? "match" : "matches"} in ${notes} ${notes === 1 ? "note" : "notes"}.`;
  if (relatedCount === 0) return head;
  return `${head} ${relatedCount} more ${relatedCount === 1 ? "seems" : "seem"} related in meaning.`;
}

const WHY: Record<Why, { label: string; title: string }> = {
  keyword: { label: "words", title: "Matched the words of the question" },
  meaning: { label: "related", title: "Does not contain the words of the search, but seems close in meaning" },
  both: { label: "words + meaning", title: "Matched both the words and the meaning of the question" },
};

export function whyLabel(why: Why): { label: string; title: string } {
  return WHY[why] ?? { label: String(why), title: "" };
}

export type MethodsSummary = {
  /** Which methods ran, in a sentence. */
  line: string;
  /** Why meaning search did not run and what to do about it, in the server's words. */
  reason: string | null;
  /** The embeddings model, when meaning search ran. */
  model: string | null;
};

/** Which methods a search used, and when meaning search did not run, why. */
export function describeMethods(result: Pick<SearchResult, "methods" | "meaning">): MethodsSummary {
  const keyword = result.methods.includes("keyword");
  const meaning = result.methods.includes("meaning");
  let line: string;
  if (keyword && meaning) line = "Searched by words and by meaning.";
  else if (meaning) line = "Searched by meaning only.";
  else if (keyword) line = "Searched by words only. Meaning search did not run.";
  else line = "Nothing was searched: the question had no words to look for.";
  return { line, reason: meaning ? null : result.meaning.reason, model: meaning ? result.meaning.model : null };
}

/** "3 passages in 2 notes" */
export function countLine(passages: number, notes: number): string {
  if (passages === 0) return "Nothing matched.";
  return `${passages} ${passages === 1 ? "passage" : "passages"} in ${notes} ${notes === 1 ? "note" : "notes"}`;
}

/** What to say about matches that ranked but were left out for space. */
export function trimmedLine(count: number): string | null {
  if (count <= 0) return null;
  return count === 1 ? "1 more match was left out to keep the result short." : `${count} more matches were left out to keep the result short.`;
}
