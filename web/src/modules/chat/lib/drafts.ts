// Unsent drafts, kept for as long as the page is open and the owner is
// signed in, so that changing conversation, or screen, does not lose what
// was being typed. They are let go of when the session ends.

import { onSessionEnd } from "../../../lib/session.ts";

export type Drafts = Readonly<Record<string, string>>;

let saved: Drafts = {};

export function savedDrafts(): Drafts {
  return saved;
}

export function saveDrafts(next: Drafts): void {
  saved = next;
}

export function clearDrafts(): void {
  saved = {};
}

onSessionEnd(clearDrafts);
