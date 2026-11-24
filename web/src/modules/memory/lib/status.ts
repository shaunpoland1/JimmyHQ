// The status panel's wording, and which live events mean the screen should read again.

import type { ScanResult, Status } from "../api.ts";

function count(value: number, one: string, many: string): string | null {
  return value > 0 ? `${value} ${value === 1 ? one : many}` : null;
}

/** What a scan changed, in a sentence. */
export function describeScan(scan: ScanResult): string {
  const parts = [
    count(scan.added, "note added", "notes added"),
    count(scan.changed, "note changed", "notes changed"),
    count(scan.moved, "note moved", "notes moved"),
    count(scan.removed, "note removed", "notes removed"),
    count(scan.facts_added, "fact added", "facts added"),
    count(scan.facts_edited, "fact edited", "facts edited"),
    count(scan.facts_retired, "fact retired", "facts retired"),
    count(scan.ids_written, "fact given an id", "facts given an id"),
    count(scan.deferred, "note still being written, left for the next scan", "notes still being written, left for the next scan"),
    count(scan.skipped, "file skipped", "files skipped"),
  ].filter((part): part is string => part !== null);
  const looked = `${scan.notes} ${scan.notes === 1 ? "note" : "notes"} looked at`;
  const rebuilt = scan.reindexed === undefined ? "" : ` The index was rebuilt from ${scan.reindexed} ${scan.reindexed === 1 ? "note" : "notes"}.`;
  return `${parts.length ? `${parts.join(", ")}.` : "Nothing had changed."} ${looked}.${rebuilt}`;
}

/** How far along meaning search is: "12 of 40 passages ready". null when there is nothing to say. */
export function vectorProgress(status: Pick<Status, "counts" | "meaning_search">): string | null {
  if (status.meaning_search.model === null) return null;
  const total = status.counts.chunks;
  if (total === 0) return null;
  const ready = Math.min(status.counts.chunks_with_vectors, total);
  return ready >= total ? `All ${total} passages are ready for meaning search.` : `${ready} of ${total} passages are ready for meaning search.`;
}

export type MemoryChange = {
  /** Notes whose text or facts may have changed. */
  notes: string[];
  /** A note that was moved: where it went. */
  movedTo: { from: string; to: string } | null;
};

function text(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/**
 * What a live event means for the screen, or null when it changes nothing
 * shown here. A search prints `memory.recalled`; reading again after one's own
 * search would be a loop of nothing.
 */
export function changeFrom(event: { kind: string; payload: Record<string, unknown> }): MemoryChange | null {
  if (!event.kind.startsWith("memory.") || event.kind === "memory.recalled") return null;
  const payload = event.payload;
  const notes = new Set<string>();
  for (const key of ["note", "moved_from", "moved_to", "from_note"]) {
    const path = text(payload[key]);
    if (path) notes.add(path);
  }
  let movedTo: MemoryChange["movedTo"] = null;
  const note = text(payload.note);
  if (event.kind === "memory.note.removed" && note && text(payload.moved_to)) movedTo = { from: note, to: text(payload.moved_to) ?? "" };
  return { notes: [...notes], movedTo };
}
