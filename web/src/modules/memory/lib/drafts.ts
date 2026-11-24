// Edits in progress, kept for as long as the page is open and the owner is
// signed in, so that changing screen does not lose what was being typed. They
// are let go of when the session ends.

import { onSessionEnd } from "../../../lib/session.ts";

export type Draft = {
  /** What is in the editor. */
  text: string;
  /** The note's text when this edit started (or was last saved or reloaded). */
  baseText: string;
  /** The revision that text had. Sent with the save. */
  revision: string;
};

const saved = new Map<string, Draft>();

export function draftFor(path: string): Draft | undefined {
  return saved.get(path);
}

export function keepDraft(path: string, draft: Draft): void {
  saved.set(path, draft);
}

export function dropDraft(path: string): void {
  saved.delete(path);
}

export function isDirty(draft: Draft | undefined | null): boolean {
  return draft !== undefined && draft !== null && draft.text !== draft.baseText;
}

/** True when the note has typed changes that have not been saved. */
export function hasUnsaved(path: string | null): boolean {
  return path !== null && isDirty(saved.get(path));
}

export function clearDrafts(): void {
  saved.clear();
}

onSessionEnd(clearDrafts);
