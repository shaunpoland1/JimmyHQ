// Merging the initial tape page with live events from the feed.

import type { TapeEvent } from "../api.ts";

export const TAPE_VIEW_LIMIT = 200;

export function isTapeEvent(value: unknown): value is TapeEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return (
    typeof event.seq === "number" &&
    typeof event.ts === "number" &&
    typeof event.kind === "string" &&
    typeof event.actor === "string"
  );
}

/** Parses one server-sent `tape` message; returns null if it is not an event. */
export function parseFeedMessage(data: string): TapeEvent | null {
  try {
    const parsed: unknown = JSON.parse(data);
    return isTapeEvent(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Combines event lists: de-duplicated by `seq`, newest first, capped. */
export function mergeEvents(
  incoming: readonly TapeEvent[],
  existing: readonly TapeEvent[],
  limit: number = TAPE_VIEW_LIMIT,
): TapeEvent[] {
  const bySeq = new Map<number, TapeEvent>();
  for (const event of existing) bySeq.set(event.seq, event);
  for (const event of incoming) bySeq.set(event.seq, event);
  return [...bySeq.values()].sort((a, b) => b.seq - a.seq).slice(0, limit);
}
