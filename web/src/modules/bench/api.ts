// Typed client for the Bench: the owner's single inbox. Shapes follow the
// server's responses field for field (see docs/bench.md).

import { request } from "../../api";

export type Lane = "needs" | "done" | "waiting";
export type ActionStyle = "primary" | "default" | "danger";
export type CardStatus = "open" | "decided" | "withdrawn" | "expired";

export type Action = {
  id: string;
  label: string;
  style: ActionStyle;
  needs_reason: boolean;
  closes: boolean;
  moves_to: Lane | null;
};

export type LadderRef = { actor: string; tool: string; action: string; category: string };

type CardCommon = {
  /** 26-character ULID, upper case. */
  id: string;
  /** Dotted, e.g. "posts.draft". */
  kind: string;
  /** Group label. */
  source: string;
  title: string;
  /** May be "". */
  summary: string;
  /** "owner", "token:<name>", "agent:<id>", ... */
  filed_by: string;
  subject: string | null;
  /** The stored lane. A snoozed card keeps "needs" but is listed under `lanes.waiting`. */
  lane: Lane;
  actions: Action[];
  /** Unix milliseconds. */
  filed: number;
  /** Unix milliseconds of the last activity. */
  activity: number;
  /** Unix milliseconds it last entered "needs". */
  needs_since: number | null;
  /** True only while the snooze is still in the future. */
  snoozed: boolean;
  snoozed_until: number | null;
  /** Count of filer updates. */
  updates: number;
  ladder: LadderRef | null;
  status: CardStatus;
};

/** A card as the lanes list it: no body, and files as a count. */
export type CardListItem = CardCommon & { files: number };

export type CardFile = { index: number; name: string };
export type CardDetail = { label: string; text: string };

type EntryBase = { ts: number; actor: string };
export type FiledEntry = EntryBase & { type: "filed" };
export type UpdatedEntry = EntryBase & { type: "updated"; note: string | null; revised: ("title" | "summary" | "body")[] };
export type DecidedEntry = EntryBase & {
  type: "decided";
  action: string;
  label: string;
  note: string | null;
  reason: string | null;
  closes: boolean;
  moves_to: Lane | null;
};
export type SnoozedEntry = EntryBase & { type: "snoozed"; until: number };
export type UnsnoozedEntry = EntryBase & { type: "unsnoozed" };
export type WithdrawnEntry = EntryBase & { type: "withdrawn"; note: string | null };
export type HistoryEntry = FiledEntry | UpdatedEntry | DecidedEntry | SnoozedEntry | UnsnoozedEntry | WithdrawnEntry;

/** A card in full, as `GET /api/bench/{id}` and every owner action return it. */
export type Card = CardCommon & {
  files: CardFile[];
  body: string;
  /** True if a body kept in the Vault could not be read. */
  body_missing: boolean;
  why: string | null;
  details: CardDetail[];
  /** Oldest first. */
  history: HistoryEntry[];
  /** The latest "decided" entry. */
  decision: DecidedEntry | null;
};

export type RecentDecision = {
  card_id: string;
  title: string;
  kind: string;
  source: string;
  action: string;
  label: string;
  note: string | null;
  reason: string | null;
  ts: number;
  closed: boolean;
};

/** One quick snooze choice. The server works these out from the owner's settings and time zone. */
export type SnoozeChoice = { id: "later" | "tomorrow" | "week"; label: string; until: number };

export type Board = {
  /** Each lane newest activity first. Snoozed cards are in `waiting`. */
  lanes: Record<Lane, CardListItem[]>;
  counts: Record<Lane, number>;
  needs_by_kind: Record<string, number>;
  needs_by_source: Record<string, number>;
  oldest_needs_since: number | null;
  oldest_needs_ms: number | null;
  /** Newest first; the length is capped by the `bench.recent_decisions` setting. */
  recent: RecentDecision[];
  /** Server time, unix milliseconds. */
  now: number;
  /** The quick snooze choices on offer at `now`, soonest first. */
  snooze_choices: SnoozeChoice[];
  /** The owner's IANA time zone name, or "local" for the server's own. */
  timezone: string;
};

export type DecideBody = {
  action: string;
  note?: string | null;
  reason?: string | null;
  /** The card's `updates` as it was on screen. The server refuses (409) if the card changed since. */
  seen_updates: number;
};

function cardPath(id: string, tail = ""): string {
  return `/api/bench/${encodeURIComponent(id)}${tail}`;
}

/** Where one attached file is served. The server always sends it as a download. */
export function fileUrl(cardId: string, index: number): string {
  return `/api/bench/files/${encodeURIComponent(cardId)}/${index}`;
}

export const benchApi = {
  board: () => request<Board>("GET", "/api/bench"),
  card: (id: string) => request<Card>("GET", cardPath(id)),
  decide: (id: string, body: DecideBody) => request<Card>("POST", cardPath(id, "/decide"), { body }),
  /** Without `until` the server uses the `bench.snooze_hours` setting. */
  snooze: (id: string, until?: number) =>
    request<Card>("POST", cardPath(id, "/snooze"), { body: until === undefined ? {} : { until } }),
  unsnooze: (id: string) => request<Card>("POST", cardPath(id, "/unsnooze")),
};
