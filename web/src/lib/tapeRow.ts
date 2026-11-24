// How one Tape event reads in the events panel, including events whose content
// the owner had erased. Pure, so the wording is tested.

import type { TapeEvent } from "../api.ts";

export const ERASED_KIND = "tape.erased";
export const ERASED_TAG = "erased";
export const ERASED_NOTE = "Its content was erased at the owner's request. When it happened and what kind of event it was are kept.";

export type TapeRowView = {
  /** The event's content was erased: show it muted, with the tag. */
  erased: boolean;
  /** A plain sentence for rows that have one (the `tape.erased` event itself), else null. */
  summary: string | null;
  /** What a row says when opened, for rows that can be opened, else null. */
  detail: string | null;
};

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** "12 events", "1 event". */
function events(n: number): string {
  return n === 1 ? "1 event" : `${n} events`;
}

/** The `tape.erased` event in plain words, from its counts. Never throws on a payload of the wrong shape. */
export function describeErasure(payload: Record<string, unknown>): string {
  const total = count(payload.events);
  const rawKinds = payload.kinds;
  const kinds: [string, number][] =
    rawKinds !== null && typeof rawKinds === "object" && !Array.isArray(rawKinds)
      ? Object.entries(rawKinds as Record<string, unknown>).flatMap(([kind, n]) => {
          const found = count(n);
          return found === null ? [] : [[kind, found] as [string, number]];
        })
      : [];
  kinds.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const reason = typeof payload.reason === "string" && payload.reason ? payload.reason : null;
  if (total === null) return "The content of some events was erased.";
  const list = kinds.length > 0 ? ` (${kinds.map(([kind, n]) => `${n} ${kind}`).join(", ")})` : "";
  return `Erased the content of ${events(total)}${list}${reason ? `, because of ${reason}` : ""}.`;
}

export function describeTapeEvent(event: Pick<TapeEvent, "kind" | "payload" | "erased">): TapeRowView {
  if (event.erased === true) return { erased: true, summary: null, detail: ERASED_NOTE };
  if (event.kind === ERASED_KIND) return { erased: false, summary: describeErasure(event.payload ?? {}), detail: null };
  return { erased: false, summary: null, detail: null };
}
