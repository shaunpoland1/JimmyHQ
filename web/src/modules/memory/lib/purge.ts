// Deleting for good: what the dialogs say before, and what the server's counts mean after.

import type { FactPurge, NotePurge } from "../api.ts";

function whole(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function versions(n: number): string {
  return `${n} stored ${n === 1 ? "version" : "versions"}`;
}

/** What cannot be reached by either kind of permanent delete, in one sentence. */
export const OUT_OF_REACH =
  "It cannot reach a Bench card that showed these words, the chat message or task they were read from, or copies outside JimmyHQ such as earlier backups.";

/** Before deleting a note with its history. */
export function noteWarning(path: string, facts: number): string {
  const retired = facts === 0 ? "" : ` Its ${facts} ${facts === 1 ? "fact is" : "facts are"} retired.`;
  return `${path} is removed from the Library and every stored version of it is deleted, so its history can no longer be read or restored.${retired} This cannot be undone.`;
}

/** Before forgetting a fact everywhere. */
export const FACT_WARNING =
  "The fact is retired, and every stored version of any note that contains its line is deleted: whole versions, so other text in those versions goes with it. This cannot be undone.";

/** After a note was deleted with its history: what the server says it removed. */
export function notePurged(purged: Partial<NotePurge> | undefined): string {
  const n = whole(purged?.versions);
  const bodies = whole(purged?.bodies);
  if (n === 0) return "The note is deleted. No stored version of it was left to erase.";
  return `The note is deleted and ${versions(n)} of it ${n === 1 ? "was" : "were"} erased (${bodies} stored ${bodies === 1 ? "text" : "texts"}).`;
}

/** After a fact was forgotten everywhere: what the server says it removed. */
export function factPurged(purged: Partial<FactPurge> | undefined): string {
  const n = whole(purged?.versions);
  const bodies = whole(purged?.bodies);
  const notes = whole(purged?.notes);
  if (n === 0) return "The fact is forgotten. No stored version of a note held its line.";
  return `The fact is forgotten and ${versions(n)} of ${notes} ${notes === 1 ? "note" : "notes"} ${n === 1 ? "was" : "were"} erased (${bodies} stored ${bodies === 1 ? "text" : "texts"}).`;
}
