// Typed client for Memory: the Library of plain Markdown notes, the facts in
// them, search, status, and mining (sources, proposed facts, the timeline).
// Shapes follow the server's responses field for field (see docs/memory.md).
// Every response type lives in this file; a field the server adds is ignored.

import { request } from "../../api";

/** One fact: a single line in a note, with what the index knows about it. */
export type Fact = {
  /** 26-character ULID, upper case. */
  id: string;
  text: string;
  /** The note it is in, relative to the Library. */
  note: string;
  /** 1-based line in that note. */
  line: number;
  kind: string;
  /** 0 to 1. */
  confidence: number;
  /** The owner has said it is true. */
  confirmed: boolean;
  /** "owner", "owner:disk", "agent:<id>", "system", ... */
  saved_by: string;
  /** Short references such as "tape:<event id>" or "chat:<message id>". */
  evidence: string[];
  /** Unix milliseconds. */
  created_ms: number;
  updated_ms: number;
  retired_ms: number | null;
  retired_reason: string | null;
};

export type TreeNote = { path: string; size: number; modified_ms: number; facts: number };
export type Tree = { folders: string[]; notes: TreeNote[] };

export type Note = {
  path: string;
  text: string;
  /** SHA-256 of the note's bytes, 64 hex characters. Sent back with a save. */
  revision: string;
  size: number;
  modified_ms: number;
  /** Live facts in line order. */
  facts: Fact[];
};

export type VersionSummary = {
  /** The id of the Tape event that recorded this version. */
  version: string;
  ts: number;
  actor: string;
  size: number;
  /** How many facts the note held. */
  facts: number;
  /** The path the note had then. */
  note: string;
  /** False when the text of this version is no longer stored. */
  available: boolean;
};
export type NoteHistory = { path: string; versions: VersionSummary[] };
export type NoteVersion = { version: string; ts: number; actor: string; note: string; size: number; text: string };

export type FactsPage = { facts: Fact[]; next_before: string | null };
export type FactsQuery = { kind?: string; q?: string; confirmed?: boolean; note?: string; limit?: number; before?: string };
export type FactResult = { fact: Fact };
/** `created` is false when a live fact already said this; `fact` is then that one. */
export type AddFactResult = { fact: Fact; created: boolean };
export type NewFact = { text: string; kind: string; note?: string };
export type FactChange = { text?: string; kind?: string; confirmed?: boolean };

export type Why = "keyword" | "meaning" | "both";
export type SearchMethod = "keyword" | "meaning";

export type Passage = {
  /** The fact id, or "c:" and the start of the passage's hash for free text. */
  id: string;
  text: string;
  note: string;
  /** "" when the passage is under no heading. */
  heading: string;
  fact_id: string | null;
  score: number;
  why: Why;
  /** null for free text. */
  confirmed: boolean | null;
  chars: number;
};
export type TrimmedPassage = { id: string; note: string; fact_id: string | null; chars: number };

export type SearchResult = {
  passages: Passage[];
  /** Ranked high enough, but did not fit the budget. */
  trimmed: TrimmedPassage[];
  methods: SearchMethod[];
  meaning: { used: boolean; reason: string | null; model: string | null };
  scope: string[];
  k: number;
  budget_chars: number;
  shown_chars: number;
  receipt_id: string | null;
};

export type ScanResult = {
  added: number;
  changed: number;
  removed: number;
  moved: number;
  facts_added: number;
  facts_edited: number;
  facts_retired: number;
  at_ms: number;
  notes: number;
  ids_written: number;
  /** Notes still being written, left for the next scan. */
  deferred: number;
  skipped: number;
  took_ms: number;
  /** Only after a rebuild: how many notes were cut into passages again. */
  reindexed?: number;
};
export type ScanResponse = { scan: ScanResult };

export type SkippedFile = { path: string; reason: string };

export type Status = {
  /** Where the Library is on disk. Shown only when the server sends it. */
  library_path?: string | null;
  counts: { notes: number; facts: number; chunks: number; chunks_with_vectors: number };
  index_version: number | null;
  last_scan: ScanResult | null;
  skipped: SkippedFile[];
  meaning_search: {
    on: boolean;
    reason: string | null;
    /** "provider/model", or null when none is usable. */
    model: string | null;
    local_only: boolean;
    ready: number;
    waiting: number | null;
  };
  watch_disk: boolean;
};

// ---- deleting for good ----

/** What a delete with history removed. */
export type NotePurge = { versions: number; bodies: number };
export type DeleteNoteResult = { ok: true };
export type DeleteNoteForGoodResult = { ok: true; purged: NotePurge };
/** What "forget everywhere" removed. `notes` is how many notes had versions deleted. */
export type FactPurge = { versions: number; bodies: number; notes: number };
/** The fact comes back retired, with its `text` empty. */
export type ForgetEverywhereResult = { fact: Fact; purged: FactPurge };

// ---- mining ----

/** Why a proposed fact was thrown away. The server may add reasons; an unknown one is shown by its name. */
export type DropReason = "duplicate" | "declined" | "no_evidence" | "not_from_owner" | "secret" | "too_long" | "invalid";

/** What mining did with what it read, for one pass or for all time. */
export type Tally = {
  /** Items shown to the model. */
  read: number;
  /** Items passed over, by reason. */
  skipped: Record<string, number>;
  skipped_total: number;
  /** Facts the model proposed. proposed = saved + sent + ignored + dropped_total. */
  proposed: number;
  /** Saved automatically. */
  saved: number;
  /** Sent to the Bench. */
  sent: number;
  ignored: number;
  dropped: Partial<Record<DropReason, number>> & Record<string, number>;
  dropped_total: number;
};

/** One source's counts from `POST /mine`: a tally without the two totals, with the pass's error. */
export type MineCounts = Omit<Tally, "skipped_total" | "dropped_total"> & { error: string | null };

/**
 * The totals of `POST /mine`, flat: `read`, `proposed`, `saved`, `sent`,
 * `ignored`, `skipped` (a number), and one `dropped.<reason>` and
 * `skipped.<reason>` per reason. `lib/mining.ts` turns this into a Tally.
 */
export type FlatCounts = Record<string, number>;

export type MineResult = {
  /** False when no pass could run; `reason` then says why and what to change. */
  ran: boolean;
  reason: string | null;
  /** Only the sources that had something new to read. */
  sources: Record<string, MineCounts>;
  totals: FlatCounts;
  /** Bench cards filed after the pass. */
  filed: number;
};

export type SourceError = { message: string; at_ms: number };

export type Source = {
  id: string;
  label: string;
  description: string;
  on: boolean;
  /** The key of the ordinary setting that switches this source on and off. */
  setting: string;
  last_read_ms: number | null;
  items_read: number;
  /** null until the worker has counted, or for a source that cannot count. */
  items_waiting: number | null;
  /** True when `items_waiting` means "at least that many". */
  items_waiting_more: boolean;
  items_waiting_as_of_ms: number | null;
  last_run: Tally;
  all_time: Tally;
  last_error: SourceError | null;
};

export type MiningModel = {
  role: string;
  /** "provider/model", or null when no model can be asked. */
  name: string | null;
  is_local: boolean | null;
  local_only: boolean;
};

export type Mining = {
  /** The background switch. */
  on: boolean;
  /** Whether a pass would do anything. */
  working: boolean;
  /** null when working; otherwise the one reason, with what to change. */
  reason: string | null;
  /** Set when the two thresholds are in an order that sends nothing to the Bench. */
  thresholds_note?: string | null;
  /** Seconds one pass may take before the rest waits for the next. */
  pass_seconds?: number;
  /** Sources the last pass did not reach in its time. */
  unfinished?: string[];
  last_run_ms: number | null;
  next_run_ms: number | null;
  interval_s: number;
  model: MiningModel;
  /** Confidence at or above this is saved without asking. Above 1 means never. */
  auto_save_above: number;
  /** Confidence below this is ignored. 0 ignores nothing. */
  ignore_below: number;
  open_cards: number;
  max_open_cards: number;
  waiting_for_bench: number;
};

export type SourcesResponse = { sources: Source[]; mining: Mining };

export type CandidateState = "waiting" | "bench" | "saved" | "declined" | "ignored" | "withdrawn";
/** What the list can be asked for: a state, or `review` (saved automatically, not yet confirmed). */
export type CandidateFilter = CandidateState | "review";

export type Quote = {
  /** Such as "tape:<event id>". */
  ref: string;
  /** "owner", "assistant" or another actor. */
  who: string;
  when_ms: number;
  /** Such as "Chat: Plans". */
  where: string;
  /** Untrusted text: shown as plain text only. */
  quote: string;
};

export type Candidate = {
  id: string;
  source: string;
  /** Untrusted text. Empty once "forget everywhere" has erased it. */
  statement: string;
  kind: string;
  confidence: number;
  evidence: string[];
  quotes: Quote[];
  proposed_ms: number;
  state: CandidateState;
  state_ms: number;
  /** Why it was declined, ignored or withdrawn. */
  reason: string | null;
  fact_id: string | null;
  card_id: string | null;
  saved_automatically: boolean;
  /** Saved automatically and neither confirmed nor forgotten yet. */
  needs_review: boolean;
};

export type CandidateCounts = Record<CandidateFilter, number>;
export type CandidatesPage = { candidates: Candidate[]; next_before: string | null; counts: CandidateCounts };
export type CandidatesQuery = { state?: CandidateFilter; source?: string; limit?: number; before?: string };
export type CandidateResult = { candidate: Candidate };

export type ActivityType =
  | "fact_saved"
  | "fact_edited"
  | "fact_confirmed"
  | "fact_forgotten"
  | "note_saved"
  | "note_removed"
  | "mined"
  | "candidate_kept"
  | "candidate_declined"
  | "history_purged";

/** The fact an entry is about, looked up when the timeline is read: `text` is empty once it was purged. */
export type ActivityFact = { id: string; text: string; kind: string | null; retired: boolean };

/**
 * One line of the timeline. `detail` depends on `type`:
 * fact_saved `{kind, confirmed, confidence, revived}`; fact_edited and
 * fact_confirmed `{changed: string[]}`; fact_forgotten `{reason}`; note_saved
 * `{created, moved_from}`; note_removed `{}`; mined `{source, trigger}` and
 * flat counts; candidate_kept and candidate_declined `{candidate_id, via}`;
 * history_purged `{why, versions}`. `lib/activity.ts` reads it defensively.
 */
export type ActivityEntry = {
  id: string;
  ts: number;
  /** One of ActivityType; a type this screen does not know is shown by its name. */
  type: ActivityType | (string & {});
  actor: string;
  note: string | null;
  detail: Record<string, unknown>;
  fact: ActivityFact | null;
};
export type ActivityPage = { activity: ActivityEntry[]; next_before: string | null };

const BASE = "/api/memory";

/** A note's address: each part of the path escaped, the slashes kept. */
export function noteUrl(path: string, tail = ""): string {
  return `${BASE}/notes/${path.split("/").map(encodeURIComponent).join("/")}${tail}`;
}

function factUrl(id: string): string {
  return `${BASE}/facts/${encodeURIComponent(id)}`;
}

export const memoryApi = {
  tree: () => request<Tree>("GET", `${BASE}/tree`),
  status: () => request<Status>("GET", `${BASE}/status`),
  scan: () => request<ScanResponse>("POST", `${BASE}/scan`),
  reindex: () => request<ScanResponse>("POST", `${BASE}/reindex`),

  note: (path: string) => request<Note>("GET", noteUrl(path)),
  /** With `revision` the server refuses (409) if the note changed since it was read. Use the text that comes back. */
  saveNote: (path: string, text: string, revision?: string) =>
    request<Note>("PUT", noteUrl(path), { body: revision === undefined ? { text } : { text, revision } }),
  deleteNote: (path: string) => request<DeleteNoteResult>("DELETE", noteUrl(path)),
  /** Deletes the note and every stored version of it. Cannot be undone. */
  deleteNoteForGood: (path: string) => request<DeleteNoteForGoodResult>("DELETE", noteUrl(path, "?history=true")),
  moveNote: (from: string, to: string) => request<Note>("POST", `${BASE}/notes/move`, { body: { from, to } }),
  history: (path: string) => request<NoteHistory>("GET", noteUrl(path, "/history")),
  version: (path: string, version: string) => request<NoteVersion>("GET", noteUrl(path, `/history/${encodeURIComponent(version)}`)),

  facts: (query: FactsQuery = {}) => {
    const params = new URLSearchParams();
    if (query.kind) params.set("kind", query.kind);
    if (query.q) params.set("q", query.q);
    if (query.confirmed !== undefined) params.set("confirmed", String(query.confirmed));
    if (query.note) params.set("note", query.note);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    if (query.before) params.set("before", query.before);
    const tail = params.toString();
    return request<FactsPage>("GET", tail ? `${BASE}/facts?${tail}` : `${BASE}/facts`);
  },
  fact: (id: string) => request<FactResult>("GET", factUrl(id)),
  addFact: (body: NewFact) => request<AddFactResult>("POST", `${BASE}/facts`, { body }),
  changeFact: (id: string, body: FactChange) => request<FactResult>("PATCH", factUrl(id), { body }),
  forgetFact: (id: string, reason?: string) => request<FactResult>("DELETE", factUrl(id), { body: reason ? { reason } : {} }),

  /** Retires the fact and deletes every stored version of any note that holds its line. Cannot be undone. */
  forgetEverywhere: (id: string, reason?: string) =>
    request<ForgetEverywhereResult>("DELETE", `${factUrl(id)}?everywhere=true`, { body: reason ? { reason } : {} }),

  sources: () => request<SourcesResponse>("GET", `${BASE}/sources`),
  /** One bounded pass now, over every source that is on or over one. */
  mine: (source?: string) => request<MineResult>("POST", `${BASE}/mine`, { body: source ? { source } : {} }),
  candidates: (query: CandidatesQuery = {}) => {
    const params = new URLSearchParams();
    if (query.state) params.set("state", query.state);
    if (query.source) params.set("source", query.source);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    if (query.before) params.set("before", query.before);
    const tail = params.toString();
    return request<CandidatesPage>("GET", tail ? `${BASE}/candidates?${tail}` : `${BASE}/candidates`);
  },
  /** Saves it as a confirmed fact; closes its Bench card if it has one. */
  keepCandidate: (id: string) => request<CandidateResult>("POST", `${BASE}/candidates/${encodeURIComponent(id)}/keep`),
  /** `reason` is required, 1 to 300 characters. Answers its Bench card with No if it has one. */
  declineCandidate: (id: string, reason: string) =>
    request<CandidateResult>("POST", `${BASE}/candidates/${encodeURIComponent(id)}/decline`, { body: { reason } }),
  activity: (query: { limit?: number; before?: string } = {}) => {
    const params = new URLSearchParams();
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    if (query.before) params.set("before", query.before);
    const tail = params.toString();
    return request<ActivityPage>("GET", tail ? `${BASE}/activity?${tail}` : `${BASE}/activity`);
  },

  /** Always the POST form: a question in a URL can end up in a log. */
  search: (q: string, scope: string[] = ["*"], k?: number) =>
    request<SearchResult>("POST", `${BASE}/search`, { body: k === undefined ? { q, scope } : { q, scope, k } }),
};
