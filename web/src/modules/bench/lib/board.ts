// Sorting, grouping and moving through the Bench lists, and the changes the
// screen makes to the cached board before the server answers.

import type { Action, Board, Card, CardListItem, DecideBody, Lane } from "../api.ts";

export type SortOrder = "oldest" | "newest";

export const LANES: readonly { id: Lane; label: string }[] = [
  { id: "needs", label: "Needs you" },
  { id: "done", label: "Done for you" },
  { id: "waiting", label: "Waiting" },
];

export function laneLabel(lane: Lane): string {
  return LANES.find((item) => item.id === lane)?.label ?? lane;
}

export function parseSortOrder(stored: unknown): SortOrder | undefined {
  return stored === "oldest" || stored === "newest" ? stored : undefined;
}

/** When the card started waiting: the last time it entered "needs", else when it was filed. */
export function waitStart(card: Pick<CardListItem, "needs_since" | "filed">): number {
  return card.needs_since ?? card.filed;
}

/** The time a card is sorted and grouped by under each sort order. */
export function sortStamp(card: CardListItem, sort: SortOrder): number {
  return sort === "oldest" ? waitStart(card) : card.activity;
}

/** Oldest wait first, or newest activity first. Ties keep id order so the list never jitters. */
export function sortCards(cards: readonly CardListItem[], sort: SortOrder): CardListItem[] {
  const sign = sort === "oldest" ? 1 : -1;
  return [...cards].sort(
    (a, b) => sign * (sortStamp(a, sort) - sortStamp(b, sort)) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
}

/** Local calendar day as "YYYY-MM-DD". */
export function dayKey(ts: number): string {
  const date = new Date(ts);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export type DayGroup = { day: string; stamp: number; cards: CardListItem[] };
export type SourceGroup = { source: string; count: number; days: DayGroup[] };

/**
 * Cards grouped by source, then by local day. Sources and days appear in the
 * order their first card does under the chosen sort.
 */
export function groupCards(cards: readonly CardListItem[], sort: SortOrder): SourceGroup[] {
  const groups: SourceGroup[] = [];
  for (const card of sortCards(cards, sort)) {
    let group = groups.find((item) => item.source === card.source);
    if (!group) {
      group = { source: card.source, count: 0, days: [] };
      groups.push(group);
    }
    group.count += 1;
    const stamp = sortStamp(card, sort);
    const day = dayKey(stamp);
    let bucket = group.days.find((item) => item.day === day);
    if (!bucket) {
      bucket = { day, stamp, cards: [] };
      group.days.push(bucket);
    }
    bucket.cards.push(card);
  }
  return groups;
}

/** Card ids in the order the grouped list shows them. */
export function flatOrder(groups: readonly SourceGroup[]): string[] {
  return groups.flatMap((group) => group.days.flatMap((day) => day.cards.map((card) => card.id)));
}

/** The card to show once `id` leaves the list: the one after it, else the one before. */
export function nextAfter(order: readonly string[], id: string): string | null {
  const index = order.indexOf(id);
  if (index < 0) return order[0] ?? null;
  return order[index + 1] ?? order[index - 1] ?? null;
}

/** Moves the selection by `delta` rows, stopping at either end. */
export function moveSelection(order: readonly string[], id: string | null, delta: number): string | null {
  if (order.length === 0) return null;
  const index = id === null ? -1 : order.indexOf(id);
  if (index < 0) return order[0] ?? null;
  const target = Math.max(0, Math.min(order.length - 1, index + delta));
  return order[target] ?? null;
}

/** Snoozed cards, soonest back first. */
export function snoozedCards(board: Board): CardListItem[] {
  return board.lanes.waiting
    .filter((card) => card.snoozed)
    .sort((a, b) => (a.snoozed_until ?? 0) - (b.snoozed_until ?? 0));
}

export type FilerCount = { filer: string; count: number };

/** How many cards each filer has in a list, most first. */
export function countByFiler(cards: readonly CardListItem[]): FilerCount[] {
  const counts = new Map<string, number>();
  for (const card of cards) counts.set(card.filed_by, (counts.get(card.filed_by) ?? 0) + 1);
  return [...counts.entries()]
    .map(([filer, count]) => ({ filer, count }))
    .sort((a, b) => b.count - a.count || a.filer.localeCompare(b.filer));
}

/** The earliest wait start in a list, or null when it is empty. */
export function oldestWaitStart(cards: readonly CardListItem[]): number | null {
  let oldest: number | null = null;
  for (const card of cards) {
    const start = waitStart(card);
    if (oldest === null || start < oldest) oldest = start;
  }
  return oldest;
}

/**
 * True while a card's snooze is still ahead of `now`. The server sends no event
 * when a snooze runs out, so a card read earlier can still say `snoozed`.
 */
export function stillSnoozed(card: Pick<CardListItem, "snoozed" | "snoozed_until">, now: number): boolean {
  return card.snoozed && (card.snoozed_until === null || card.snoozed_until > now);
}

/**
 * The card to show when nothing is chosen: the first in the list, unless that
 * is the card a decision is being sent for (it is on its way out of the list).
 */
export function cardToPin(selectedId: string | null, firstId: string | null, busyId: string | null): string | null {
  if (selectedId !== null) return selectedId;
  return firstId !== null && firstId !== busyId ? firstId : null;
}

/** The lane a card is listed under: a snoozed card is listed under "waiting". */
export function shownLane(card: Pick<CardListItem, "lane" | "snoozed">): Lane {
  return card.snoozed ? "waiting" : card.lane;
}

export function findCard(board: Board, id: string): CardListItem | undefined {
  for (const lane of LANES) {
    const found = board.lanes[lane.id].find((card) => card.id === id);
    if (found) return found;
  }
  return undefined;
}

/** The list form of a full card. */
export function toListItem(card: Card): CardListItem {
  const { body: _body, body_missing: _missing, why: _why, details: _details, history: _history, decision: _decision, files, ...rest } = card;
  return { ...rest, files: files.length };
}

/**
 * The board with card `id` taken out and, when `card` is still open, put back
 * in the lane it now belongs to. Counts follow the lists.
 */
export function placeCard(board: Board, id: string, card: CardListItem | null): Board {
  const lanes: Record<Lane, CardListItem[]> = {
    needs: board.lanes.needs.filter((item) => item.id !== id),
    done: board.lanes.done.filter((item) => item.id !== id),
    waiting: board.lanes.waiting.filter((item) => item.id !== id),
  };
  if (card && card.status === "open") {
    const lane = shownLane(card);
    lanes[lane] = [card, ...lanes[lane]];
  }
  return {
    ...board,
    lanes,
    counts: { needs: lanes.needs.length, done: lanes.done.length, waiting: lanes.waiting.length },
  };
}

/**
 * What a click on `action` sends. `seen_updates` is taken from the card as it
 * was on screen, so the decision is for the words the owner read.
 */
export function decideBody(card: Pick<CardListItem, "updates">, action: Pick<Action, "id">, note: string, reason: string): DecideBody {
  return { action: action.id, note: note.trim() || null, reason: reason.trim() || null, seen_updates: card.updates };
}

/** What a card will look like once the server records `action`; null when it closes. */
export function predictDecision(card: CardListItem, action: Action, now: number): CardListItem | null {
  if (action.closes) return null;
  const next: CardListItem = { ...card, activity: now, snoozed: false, snoozed_until: null };
  if (action.moves_to) {
    next.lane = action.moves_to;
    if (action.moves_to === "needs") next.needs_since = now;
  }
  return next;
}

export function predictSnooze(card: CardListItem, until: number): CardListItem {
  return { ...card, snoozed: true, snoozed_until: until };
}

export function predictUnsnooze(card: CardListItem, now: number): CardListItem {
  return { ...card, snoozed: false, snoozed_until: null, needs_since: card.lane === "needs" ? now : card.needs_since };
}
