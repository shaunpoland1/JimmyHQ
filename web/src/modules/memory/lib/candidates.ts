// Proposed facts: which ones each filter lists, how their state and confidence
// are worded, and how keeping or declining one is shown before the server answers.

import type { Candidate, CandidateCounts, CandidateFilter, CandidatesPage, Mining } from "../api.ts";

// ---- filters ----

/** What the panel can list: what needs the owner (the default), or one of the server's states. */
export type CandidateView = "needs" | CandidateFilter;

export const DEFAULT_VIEW: CandidateView = "needs";

export const VIEWS: readonly { id: CandidateView; label: string; empty: string }[] = [
  { id: "needs", label: "Needs you", empty: "Nothing is waiting for you. Facts found for you will appear here to keep or decline." },
  { id: "bench", label: "On the Bench", empty: "No proposed fact has a card on the Bench." },
  { id: "review", label: "Saved automatically, not confirmed", empty: "Nothing saved automatically is waiting to be confirmed." },
  { id: "waiting", label: "Waiting for a Bench slot", empty: "No proposed fact is waiting for room on the Bench." },
  { id: "saved", label: "Saved", empty: "No proposed fact has been saved yet." },
  { id: "declined", label: "Declined", empty: "You have not declined a proposed fact." },
  { id: "ignored", label: "Ignored", empty: "Nothing has been ignored for low confidence." },
  { id: "withdrawn", label: "Withdrawn", empty: "No proposed fact has been withdrawn." },
];

export function isView(value: string): value is CandidateView {
  return VIEWS.some((view) => view.id === value);
}

function whole(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

/** How many proposed facts a view lists, from the counts the server sends with every page. */
export function viewCount(counts: Partial<CandidateCounts> | undefined, view: CandidateView): number {
  if (!counts) return 0;
  if (view === "needs") return whole(counts.bench) + whole(counts.review);
  return whole(counts[view]);
}

/** Whether the server's list for `filter` holds this candidate. */
export function matchesFilter(candidate: Pick<Candidate, "state" | "needs_review">, filter: CandidateFilter): boolean {
  return filter === "review" ? candidate.state === "saved" && candidate.needs_review : candidate.state === filter;
}

/** The candidates of several pages or lists in order, each once. */
export function uniqueCandidates(lists: readonly (readonly Candidate[])[]): Candidate[] {
  const seen = new Set<string>();
  const out: Candidate[] = [];
  for (const list of lists) {
    for (const candidate of list) {
      if (seen.has(candidate.id)) continue;
      seen.add(candidate.id);
      out.push(candidate);
    }
  }
  return out;
}

// ---- wording ----

/** A candidate's state in a few words. */
export function stateWords(candidate: Pick<Candidate, "state" | "needs_review" | "saved_automatically">): string {
  switch (candidate.state) {
    case "waiting":
      return "Waiting for a Bench slot";
    case "bench":
      return "On the Bench";
    case "saved":
      if (candidate.needs_review) return "Saved automatically, not confirmed";
      return candidate.saved_automatically ? "Saved automatically, confirmed" : "Saved";
    case "declined":
      return "Declined";
    case "ignored":
      return "Ignored";
    case "withdrawn":
      return "Withdrawn";
    default:
      return String(candidate.state);
  }
}

/** Confidence as the number the thresholds are set in: "0.70". */
export function confidenceNumber(confidence: number): string {
  const value = Number.isFinite(confidence) ? Math.min(1, Math.max(0, confidence)) : 0;
  return value.toFixed(2);
}

/** Which side of the two thresholds a confidence falls on today. */
export function routeFor(confidence: number, mining: Pick<Mining, "ignore_below" | "auto_save_above">): "ignored" | "bench" | "saved" {
  if (confidence >= mining.auto_save_above) return "saved";
  if (confidence < mining.ignore_below) return "ignored";
  return "bench";
}

/** Who a quote is by: "you", "the model", or the actor's name. */
export function quoteAuthor(who: string): string {
  if (who === "owner") return "you";
  if (who === "assistant") return "the model";
  return who || "unknown";
}

// ---- what the owner may do ----

/** Keep saves or confirms. There is nothing to keep once it is saved and confirmed, or its words were erased. */
export function canKeep(candidate: Pick<Candidate, "state" | "needs_review" | "statement">): boolean {
  if (candidate.statement === "") return false;
  return candidate.state !== "saved" || candidate.needs_review;
}

/** Declining twice is refused by the server. */
export function canDecline(candidate: Pick<Candidate, "state">): boolean {
  return candidate.state !== "declined";
}

export const MAX_REASON = 300;
/** The server's own sentence when a decline carries no reason. */
export const REASON_NEEDED = "A reason is needed to decline. Say why in a sentence.";

/** The reason as it will be sent, or the sentence that says what is wrong with it. */
export function checkReason(typed: string): { ok: true; reason: string } | { ok: false; error: string } {
  const reason = typed.split(/\s+/).filter(Boolean).join(" ");
  if (!reason) return { ok: false, error: REASON_NEEDED };
  if (reason.length > MAX_REASON) return { ok: false, error: `The reason cannot be longer than ${MAX_REASON} characters.` };
  return { ok: true, reason };
}

// ---- showing a decision at once ----

/** The candidate as the server will most likely return it. */
export function predict(candidate: Candidate, action: "keep" | "decline", reason: string | null, now: number): Candidate {
  if (action === "keep") return { ...candidate, state: "saved", needs_review: false, reason: null, state_ms: now };
  return { ...candidate, state: "declined", needs_review: false, reason, state_ms: now };
}

/** The counts after one candidate went from `before` to `after`. Never below zero. */
export function countsAfter(counts: CandidateCounts, before: Pick<Candidate, "state" | "needs_review">, after: Pick<Candidate, "state" | "needs_review">): CandidateCounts {
  const next: CandidateCounts = { ...counts };
  const move = (key: CandidateFilter, by: number) => {
    next[key] = Math.max(0, whole(next[key]) + by);
  };
  if (before.state !== after.state) {
    move(before.state, -1);
    move(after.state, 1);
  }
  const was = before.state === "saved" && before.needs_review;
  const is = after.state === "saved" && after.needs_review;
  if (was !== is) move("review", is ? 1 : -1);
  return next;
}

export type CandidatePages = { pages: CandidatesPage[]; pageParams: unknown[] };

/**
 * One cached list after a candidate changed: replaced where it still belongs,
 * taken out where it no longer does (`filter` is what the list was asked for;
 * null lists every state), and the counts moved to match on every page.
 */
export function settleInPages(data: CandidatePages, filter: CandidateFilter | null, before: Candidate, after: Candidate): CandidatePages {
  const stays = filter === null || matchesFilter(after, filter);
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      counts: countsAfter(page.counts, before, after),
      candidates: stays
        ? page.candidates.map((candidate) => (candidate.id === after.id ? after : candidate))
        : page.candidates.filter((candidate) => candidate.id !== after.id),
    })),
  };
}

export function flattenCandidates(data: CandidatePages | undefined): Candidate[] {
  return uniqueCandidates((data?.pages ?? []).map((page) => page.candidates));
}

/** The newest counts a list holds: those of its first page, which is read again most often. */
export function countsOf(data: CandidatePages | undefined): CandidateCounts | undefined {
  return data?.pages[0]?.counts;
}
