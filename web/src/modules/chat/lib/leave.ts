// Where the screen goes when the open conversation is archived or deleted.

import type { Conversation } from "../api.ts";

/**
 * The conversation to open once `goneId` has left the open list: the most
 * recently active one that remains, or null when none does (the screen then
 * shows the empty "start a conversation" state). Archived ones never count,
 * since the list being read is the open one and may not yet have caught up.
 */
export function nextAfterLeaving(list: readonly Pick<Conversation, "id" | "archived" | "updated">[], goneId: string): string | null {
  let best: Pick<Conversation, "id" | "updated"> | null = null;
  for (const item of list) {
    if (item.id === goneId || item.archived) continue;
    if (best === null || item.updated > best.updated || (item.updated === best.updated && item.id > best.id)) best = item;
  }
  return best?.id ?? null;
}

/** What a link to a conversation that is not there says. */
export const DELETED_TITLE = "This conversation was deleted";
export const DELETED_HELP = "It is no longer here, so there is nothing to show. Start a new conversation to carry on.";
