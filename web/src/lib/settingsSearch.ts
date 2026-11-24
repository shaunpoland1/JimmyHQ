// Searching every setting at once: what can be found, which entries a query
// matches, in what order they are shown, and which letters to mark.
//
// Every word typed must match. A word may match a setting's own words (its
// label, help, key, choices) or where it lives (its page, sub-heading, group),
// so "memory timeout" finds the timeouts on the Memory page. A setting that
// matches only by where it lives is left out: its page is listed instead.

import type { Setting } from "../api.ts";
import type { NavGroup } from "./settingsNav.ts";
import { settingsHash } from "./settingsNav.ts";

export type SearchEntry = {
  /** A whole page, a headed part of one, or one setting. */
  kind: "page" | "part" | "setting";
  id: string;
  /** Where choosing the result goes. */
  href: string;
  label: string;
  pageId: string;
  pageLabel: string;
  /** The sub-heading it sits under; empty for a page, or a setting directly on its page. */
  partLabel: string;
  groupLabel: string;
  /** A setting's help line, or a page's description. */
  help: string;
  /** A setting's key; empty for a page. */
  key: string;
  choices: string[];
  /** Extra words a page can be found by. */
  keywords: string[];
  advanced: boolean;
};

type SearchSetting = Pick<Setting, "key" | "section" | "label" | "help" | "choices" | "items" | "advanced">;

/**
 * Everything the search box can find, in the order the screen lists it.
 * `keywords` are the extra words for a page, by its section id.
 */
export function buildSearchIndex(groups: readonly NavGroup[], settings: readonly SearchSetting[], keywords: Readonly<Record<string, readonly string[]>> = {}): SearchEntry[] {
  const out: SearchEntry[] = [];
  const bySection = new Map<string, SearchSetting[]>();
  for (const setting of settings) {
    const list = bySection.get(setting.section);
    if (list) list.push(setting);
    else bySection.set(setting.section, [setting]);
  }
  for (const group of groups) {
    for (const page of group.pages) {
      const place = { pageId: page.id, pageLabel: page.label, groupLabel: group.label };
      out.push({ ...place, kind: "page", id: `page:${page.id}`, href: settingsHash(page.id), label: page.label, partLabel: "", help: page.description, key: "", choices: [], keywords: [...(keywords[page.id] ?? [])], advanced: false });
      const rows = (sectionId: string, partLabel: string) => {
        for (const setting of bySection.get(sectionId) ?? []) {
          const choices = [...(setting.choices ?? []), ...(setting.items ?? [])].map((choice) => choice.label);
          out.push({ ...place, kind: "setting", id: `setting:${setting.key}`, href: settingsHash(page.id, setting.key), label: setting.label, partLabel, help: setting.help, key: setting.key, choices, keywords: [], advanced: setting.advanced === true });
        }
      };
      rows(page.id, "");
      for (const part of page.parts) {
        // A part with no settings is not drawn, so there is nothing to go to.
        if (!bySection.has(part.id)) continue;
        out.push({ ...place, kind: "part", id: `part:${part.id}`, href: settingsHash(part.id), label: part.label, partLabel: "", help: part.description, key: "", choices: [], keywords: [...(keywords[part.id] ?? [])], advanced: false });
        rows(part.id, part.label);
      }
    }
  }
  return out;
}

/**
 * Other words people type for the same thing. Each word typed also matches
 * any of the words listed for it. Kept short on purpose: a page's own
 * keywords (see `customSections.tsx`) are the place for anything specific.
 */
export const SYNONYMS: Readonly<Record<string, readonly string[]>> = {
  timezone: ["time zone"],
  colour: ["theme"],
  color: ["theme"],
  colours: ["theme"],
  colors: ["theme"],
  llm: ["model"],
  ai: ["model"],
  beat: ["turn"],
  beats: ["turns"],
  embed: ["meaning search", "embeddings"],
  embedding: ["meaning search", "embeddings"],
  login: ["sign in", "signed in"],
  logout: ["sign out"],
  backup: ["export", "copy of your settings"],
  restore: ["import"],
  quota: ["limit", "most", "largest"],
  quotas: ["limits", "most", "largest"],
};

/** The words of a query, lower case, each once. */
export function queryWords(query: string): string[] {
  const words = query.toLowerCase().split(/\s+/).filter((word) => word.length > 0);
  return [...new Set(words)];
}

export type Range = [start: number, end: number];

function isWordChar(char: string | undefined): boolean {
  return char !== undefined && /[\p{L}\p{N}]/u.test(char);
}

type Found = { at: number; length: number; wordStart: boolean; exact: boolean };

/** Where a word, or one of its synonyms, first appears in a text. */
function locate(text: string, word: string): Found | null {
  const lower = text.toLowerCase();
  const tries = [word, ...(SYNONYMS[word] ?? [])];
  let best: Found | null = null;
  for (const [index, needle] of tries.entries()) {
    let at = lower.indexOf(needle);
    let fallback: Found | null = null;
    while (at >= 0) {
      const found: Found = { at, length: needle.length, wordStart: !isWordChar(lower[at - 1]), exact: index === 0 };
      if (found.wordStart) {
        fallback = found;
        break;
      }
      fallback ??= found;
      at = lower.indexOf(needle, at + 1);
    }
    if (fallback && (!best || (fallback.wordStart && !best.wordStart))) best = fallback;
    if (best?.wordStart && best.exact) break;
  }
  return best;
}

/** A key as words, letter for letter: `village.headcount_cap` also reads "village headcount cap". */
function keyWords(key: string): string {
  return key.replace(/[._-]/g, " ");
}

// What a match is worth, by where the word was found.
const WORTH = { label: 100, keyword: 60, key: 55, choice: 45, help: 35, part: 20, page: 15, group: 8 } as const;
/** A match inside a word ("zone" in "timezone") or through a synonym counts for less. */
const INSIDE = 0.7;
const SYNONYM = 0.8;
const ALL_IN_LABEL = 30;
const WHOLE_PAGE = 15;
const ADVANCED_COST = 5;

function worth(base: number, found: Found | null): number {
  if (!found) return 0;
  return base * (found.wordStart ? 1 : INSIDE) * (found.exact ? 1 : SYNONYM);
}

type WordScore = { own: number; place: number; inLabel: boolean };

function scoreWord(entry: SearchEntry, word: string): WordScore {
  const label = locate(entry.label, word);
  // The first part of a key (`memory.` in `memory.related_max`) names the module, which
  // is where the setting lives, not what it is: every key on a page shares it.
  const inKey = locate(entry.key, word) ?? locate(keyWords(entry.key), word);
  const dot = entry.key.indexOf(".");
  const keyIsPlace = inKey !== null && dot >= 0 && inKey.at + inKey.length <= dot;
  const own = Math.max(
    worth(WORTH.label, label),
    keyIsPlace ? 0 : worth(WORTH.key, inKey),
    ...entry.choices.map((choice) => worth(WORTH.choice, locate(choice, word))),
    ...entry.keywords.map((keyword) => worth(WORTH.keyword, locate(keyword, word))),
    worth(WORTH.help, locate(entry.help, word)),
  );
  // Where it lives. A page is not found by its own group alone ("agents" would list every page there).
  const place =
    entry.kind === "page"
      ? 0
      : Math.max(
          worth(WORTH.part, locate(entry.partLabel, word)),
          worth(WORTH.page, locate(entry.pageLabel, word)),
          keyIsPlace ? worth(WORTH.page, inKey) : 0,
          entry.kind === "setting" ? worth(WORTH.group, locate(entry.groupLabel, word)) : 0,
        );
  return { own, place, inLabel: label !== null };
}

/** How well an entry matches the words; 0 when any word is missing or none is its own. */
export function scoreEntry(entry: SearchEntry, words: readonly string[]): number {
  if (words.length === 0) return 0;
  let total = 0;
  let anyOwn = false;
  let allInLabel = true;
  for (const word of words) {
    const score = scoreWord(entry, word);
    const best = Math.max(score.own, score.place);
    if (best === 0) return 0;
    if (score.own > 0) anyOwn = true;
    if (!score.inLabel) allInLabel = false;
    total += best;
  }
  if (!anyOwn) return 0;
  if (allInLabel) total += ALL_IN_LABEL + (entry.kind === "setting" ? 0 : WHOLE_PAGE);
  if (entry.advanced) total -= ADVANCED_COST;
  return total;
}

/** The stretches of a text to mark for these words, in order and not overlapping. */
export function matchRanges(text: string, words: readonly string[]): Range[] {
  const ranges: Range[] = [];
  for (const word of words) {
    const found = locate(text, word);
    if (found) ranges.push([found.at, found.at + found.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Range[] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([range[0], range[1]]);
  }
  return merged;
}

export type Snippet = {
  /** What the text is: part of the help line, a choice, the key, or another word for the page. */
  from: "help" | "choice" | "key" | "keyword";
  text: string;
  ranges: Range[];
};

const SNIPPET_CHARS = 110;

/** A stretch of a long text around its first match, cut at spaces, with the marks moved to fit. */
export function excerpt(text: string, words: readonly string[], size = SNIPPET_CHARS): { text: string; ranges: Range[] } {
  const ranges = matchRanges(text, words);
  const first = ranges[0];
  if (text.length <= size || !first) {
    const cut = text.length <= size ? text : `${text.slice(0, text.lastIndexOf(" ", size) > 0 ? text.lastIndexOf(" ", size) : size)}…`;
    return { text: cut, ranges: ranges.filter((range) => range[1] <= cut.length) };
  }
  let start = Math.max(0, first[0] - Math.floor(size / 3));
  if (start > 0) {
    const space = text.indexOf(" ", start);
    start = space >= 0 && space < first[0] ? space + 1 : first[0];
  }
  let end = Math.min(text.length, start + size);
  if (end < text.length) {
    const space = text.lastIndexOf(" ", end);
    if (space > first[1]) end = space;
  }
  const lead = start > 0 ? "…" : "";
  const tail = end < text.length ? "…" : "";
  const moved = ranges.filter((range) => range[0] >= start && range[1] <= end).map((range): Range => [range[0] - start + lead.length, range[1] - start + lead.length]);
  return { text: `${lead}${text.slice(start, end)}${tail}`, ranges: moved };
}

/**
 * The line under a result that shows why it matched, when its label alone does
 * not: the first word the label lacks, shown where it was found. A page shows
 * its description either way, since that says what is on it.
 */
export function snippetFor(entry: SearchEntry, words: readonly string[]): Snippet | null {
  const missing = words.filter((word) => !locate(entry.label, word));
  const help = (): Snippet | null => (entry.help ? { from: "help", ...excerpt(entry.help, words) } : null);
  for (const word of missing) {
    if (locate(entry.help, word)) return help();
    const choice = entry.choices.find((item) => locate(item, word));
    if (choice !== undefined) return { from: "choice", text: choice, ranges: matchRanges(choice, words) };
    if (entry.key && locate(entry.key, word)) return { from: "key", text: entry.key, ranges: matchRanges(entry.key, words) };
    if (entry.key && locate(keyWords(entry.key), word)) return { from: "key", text: entry.key, ranges: matchRanges(keyWords(entry.key), words) };
    const keyword = entry.keywords.find((item) => locate(item, word));
    if (keyword !== undefined) return { from: "keyword", text: keyword, ranges: matchRanges(keyword, words) };
  }
  return entry.kind === "setting" ? null : help();
}

export type SearchResult = {
  entry: SearchEntry;
  score: number;
  labelRanges: Range[];
  snippet: Snippet | null;
};

export type SearchResultGroup = { pageId: string; pageLabel: string; groupLabel: string; results: SearchResult[] };

export type SearchOutcome = {
  /** Results under the page they belong to; the page with the best match first. */
  groups: SearchResultGroup[];
  /** The same results in the order shown, for moving through with the arrow keys. */
  flat: SearchResult[];
  /** How many entries matched, including any not shown. */
  total: number;
};

export const SEARCH_LIMIT = 60;

/**
 * Search the index. Results are grouped by page. Pages are ordered by their
 * best match, then by their place on the screen; within a page, results are
 * ordered by score, then by their place on the page. At most `limit` results
 * are kept, the best ones.
 */
export function searchSettings(index: readonly SearchEntry[], query: string, limit = SEARCH_LIMIT): SearchOutcome {
  const words = queryWords(query);
  if (words.length === 0) return { groups: [], flat: [], total: 0 };
  const matched = index.map((entry, place) => ({ entry, place, score: scoreEntry(entry, words) })).filter((item) => item.score > 0);
  const kept = [...matched].sort((a, b) => b.score - a.score || a.place - b.place).slice(0, Math.max(limit, 0));

  const groups = new Map<string, { best: number; first: number; items: typeof kept }>();
  for (const item of kept) {
    const group = groups.get(item.entry.pageId);
    if (group) {
      group.items.push(item);
      group.first = Math.min(group.first, item.place);
    } else {
      groups.set(item.entry.pageId, { best: item.score, first: item.place, items: [item] });
    }
  }
  const ordered = [...groups.values()].sort((a, b) => b.best - a.best || a.first - b.first);
  const out: SearchResultGroup[] = ordered.map((group) => {
    const head = group.items[0]!.entry;
    return {
      pageId: head.pageId,
      pageLabel: head.pageLabel,
      groupLabel: head.groupLabel,
      results: group.items
        .sort((a, b) => b.score - a.score || a.place - b.place)
        .map((item) => ({ entry: item.entry, score: item.score, labelRanges: matchRanges(item.entry.label, words), snippet: snippetFor(item.entry, words) })),
    };
  });
  return { groups: out, flat: out.flatMap((group) => group.results), total: matched.length };
}

/** A text cut into marked and plain pieces, for drawing the marks. */
export function markPieces(text: string, ranges: readonly Range[]): { text: string; marked: boolean }[] {
  const pieces: { text: string; marked: boolean }[] = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start < at || end > text.length || start >= end) continue;
    if (start > at) pieces.push({ text: text.slice(at, start), marked: false });
    pieces.push({ text: text.slice(start, end), marked: true });
    at = end;
  }
  if (at < text.length) pieces.push({ text: text.slice(at), marked: false });
  return pieces;
}

/** The next result to point at when an arrow key is pressed; it stops at the ends. */
export function moveActive(current: number, count: number, key: "ArrowDown" | "ArrowUp" | "Home" | "End"): number {
  if (count <= 0) return -1;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === "ArrowDown") return Math.min(current + 1, count - 1);
  return Math.max(current - 1, 0);
}

/** What a screen reader is told as the results change. */
export function resultsAnnouncement(query: string, outcome: Pick<SearchOutcome, "total" | "flat">): string {
  if (queryWords(query).length === 0) return "";
  if (outcome.total === 0) return "No settings match.";
  const shown = outcome.flat.length;
  if (shown < outcome.total) return `${outcome.total} results. The best ${shown} are shown.`;
  return outcome.total === 1 ? "1 result." : `${outcome.total} results.`;
}
