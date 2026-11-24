// Deleting a conversation for good: what the screen says before, after, and
// while an archived conversation waits for its day.

import type { Conversation, ConversationDeleted } from "../api.ts";

const DAY = 86_400_000;

type Deletable = Pick<Conversation, "messages" | "recorded" | "off_record_messages">;

function count(n: number, one: string, many: string): string {
  return n === 1 ? `1 ${one}` : `${new Intl.NumberFormat().format(n)} ${many}`;
}

export const DELETE_FOR_GOOD = "Delete for good…";
export const DELETE_OFF_RECORD_ONLY = "Delete only what is off the record…";
export const DELETE_TITLE = "Delete this conversation for good?";
export const DELETE_CONFIRM = "Delete for good";
export const CANNOT_UNDO = "This cannot be undone.";
export const BUSY_NOTE = "Wait for the reply to finish, or stop it, before deleting.";
/** Under the menu's delete item: how it differs from archiving. */
export const DELETE_NOTE = "Removes the whole conversation, on the record and off it. Archiving (putting it away) only hides a conversation; this erases it.";
/** What the archive button says of itself. */
export const ARCHIVE_HINT = "Put away: moves it out of the list. Nothing is deleted. To delete it for good, use Delete for good… in the ⋯ menu of the open conversation.";

/** What goes, one line each, for the confirm dialog. */
export function whatIsErased(conversation: Deletable): string[] {
  const lines = [
    conversation.messages > 0 ? `${count(conversation.messages, "message", "messages")}: everything you and the model said here, and the title.` : "The conversation and its title. Nothing has been said in it yet.",
  ];
  if (conversation.recorded) {
    lines.push("Its events on the Tape are emptied and the text is overwritten in the database file. The Tape keeps only that a conversation was deleted, when, and how many messages it held.");
    lines.push("The receipts of its web lookups, which name what was searched for and which pages were read.");
    lines.push("Quotes from it that memory kept as evidence, and the Bench cards that showed them. A fact still waiting for your decision that rested only on this conversation is withdrawn.");
  }
  if (conversation.off_record_messages > 0 || !conversation.recorded) lines.push("What was said off the record, from its own store.");
  return lines;
}

/** What does not go, one line each. */
export function whatStays(conversation: Deletable): string[] {
  const lines: string[] = [];
  if (conversation.recorded) {
    lines.push("Facts memory has already saved from this conversation. Forget those on the Memory screen if you want them gone too.");
    lines.push("The receipts of its model calls: which model, when, and token counts. They hold no words.");
  }
  lines.push("Copies made outside JimmyHQ: a backup of the data folder taken earlier, and whatever a hosted model provider keeps.");
  return lines;
}

/**
 * What the toast says once it is done. It says so honestly when something is
 * still to be finished: the conversation is gone from every screen either way.
 */
export function deletedForGoodNotice(result: Pick<ConversationDeleted, "deleted" | "messages" | "finished" | "settled">): string {
  const said = !result.deleted
    ? "That conversation had already been deleted."
    : result.messages > 0
      ? `Conversation deleted for good, with its ${count(result.messages, "message", "messages")}.`
      : "Conversation deleted for good.";
  if (!result.finished) return `${said} Memory could not be reached to remove what it quoted from it; that is tried again within the hour.`;
  if (!result.settled) return `${said} The last traces of its text are cleared from the files within the hour.`;
  return said;
}

/** Whole days until an archived conversation is deleted, rounded up; 0 once it is due. Null when it never will be. */
export function daysUntilDeleted(deletesAt: number | null | undefined, now: number): number | null {
  if (deletesAt === null || deletesAt === undefined || !Number.isFinite(deletesAt)) return null;
  return Math.max(0, Math.ceil((deletesAt - now) / DAY));
}

/** The short mark on an archived row: "30 d left", "1 d left", "due". */
export function deletionMark(days: number): string {
  return days <= 0 ? "due" : `${days} d left`;
}

/** The day something will be deleted, as the owner's own calendar has it: "3 December 2026". */
export function deletionDay(deletesAt: number, locale?: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone }).format(new Date(deletesAt));
}

/** The same in a sentence, for the row's tooltip and for screen readers: it names the day. */
export function deletionSentence(deletesAt: number, now: number, locale?: string, timeZone?: string): string {
  if (deletesAt <= now) return "Will be deleted for good within the hour. Restore it to keep it.";
  return `Will be deleted for good on ${deletionDay(deletesAt, locale, timeZone)}. Restore it to keep it.`;
}

/** What the toast says after archiving: that nothing was deleted, when it will be if the setting says so, and where Delete for good is (a toast holds one button, and that is Undo). */
export function archivedNotice(title: string, deletesAt: number | null | undefined, now: number, locale?: string, timeZone?: string): string {
  const where = "To delete it for good, open it under “Show archived” and choose Delete for good… in its ⋯ menu.";
  if (deletesAt === null || deletesAt === undefined || !Number.isFinite(deletesAt)) return `Put away ${title}. Nothing was deleted. ${where}`;
  return `Put away ${title}. It is not deleted: that happens on ${deletionDay(Math.max(deletesAt, now), locale, timeZone)} unless you restore it first. ${where}`;
}

/** Above the archived list, while the setting is on. */
export const ARCHIVED_WILL_GO =
  "Archived conversations are deleted for good on the day shown beside each: the number of days in Settings, Chat, and then a grace period. Restore one to keep it. Changing the setting starts the grace period again.";
/** Above the archived list, while it is off. */
export const ARCHIVED_STAYS = "Archived is not deleted: these are only out of the list. To erase one, open it and choose Delete for good from its menu.";
