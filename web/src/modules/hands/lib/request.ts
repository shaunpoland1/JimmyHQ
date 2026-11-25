// What a request's Bench card shows of the values it would send. The card itself (on the
// Tape, kept for ever) names each argument and its size only; the values are read from
// the hands API while the request waits, and are gone once it is answered.

import type { RequestValues } from "../api.ts";

/** The Bench card kind the village files for a request to use an integration. */
export const REQUEST_CARD_KIND = "village.hand";

export function isRequestCard(kind: string): boolean {
  return kind === REQUEST_CARD_KIND;
}

export type ValueRow = { name: string; text: string };

export type RequestView =
  | { state: "loading" }
  | { state: "values"; rows: ValueRow[] }
  | { state: "gone"; sentence: string }
  | { state: "none"; sentence: string }
  | { state: "error"; sentence: string };

export const GONE_SENTENCE =
  "These details are no longer kept. A request's values are erased once it is answered, withdrawn or no longer possible.";
export const NONE_SENTENCE = "No request is tied to this card, so there are no values to show. Do not approve it.";
export const OWNER_ONLY_SENTENCE = "Only the signed-in owner can see what this request would send. Sign in as the owner to read it.";

/** One value as it will be sent: text exactly as it is, anything else as JSON. */
export function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

/** The values in name order, as the card lists the argument names. */
export function valueRows(args: Record<string, unknown>): ValueRow[] {
  return Object.keys(args)
    .sort()
    .map((name) => ({ name, text: valueText(args[name]) }));
}

/**
 * What to show, from the server's answer or the error it gave. `status` is the HTTP
 * status of an error (0 when the server could not be reached).
 */
export function requestView(
  answer: RequestValues | undefined,
  error: { status: number; detail: string } | null,
): RequestView {
  if (error) {
    if (error.status === 404) return { state: "none", sentence: NONE_SENTENCE };
    if (error.status === 401 || error.status === 403) return { state: "error", sentence: OWNER_ONLY_SENTENCE };
    return { state: "error", sentence: error.detail || "The values could not be read. Try again." };
  }
  if (!answer) return { state: "loading" };
  if (!answer.waiting || answer.arguments === null) return { state: "gone", sentence: GONE_SENTENCE };
  return { state: "values", rows: valueRows(answer.arguments) };
}
