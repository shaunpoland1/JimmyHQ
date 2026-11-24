// The sentences on a package's row, and after a button. Pure: no DOM.

import { when } from "./times.js";

/** Which carrier a package has, in words. */
export function carrierLine(row) {
  const name = (code) => row.carrier_name || `code ${code}`;
  if (row.carrier) return `Carrier: ${name(row.carrier)}`;
  if (row.carrier_found) return `Carrier: ${name(row.carrier_found)} (${row.carrier_guessed ? "a guess by the service" : "detected"})`;
  return "Carrier: detected automatically";
}

/** The last event, with when it happened; or why there is none. */
export function eventLine(row, zone) {
  if (row.event) return row.event_ms ? `${row.event} · ${when(row.event_ms, zone)}` : row.event;
  if (row.status === "waiting") return "No news from the carrier yet.";
  return "";
}

/** What to say on the status line after Refresh now: {words, tone}. */
export function refreshWords(result) {
  if (!result) return { words: "", tone: "" };
  return { words: result.said, tone: result.ok ? "ok" : "bad" };
}

/** The label on the Refresh now button. */
export function refreshLabel(busy) {
  return busy ? "Checking…" : "Refresh now";
}

/** The title of the in-page question before a removal. */
export function removeQuestion(row) {
  return {
    title: `Remove ${row.label}?`,
    intro: "It is deleted from Packages for good, and the tracking service is asked to stop tracking it. A delivered package you only want out of the way can just stay in Delivered.",
  };
}
