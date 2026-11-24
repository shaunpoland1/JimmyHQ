// On and off the record: the words the screen uses, where the thread shows
// that the switch was flipped, and what deleting will remove.

import type { Conversation, Message } from "../api.ts";
import type { ListPages } from "./format.ts";

export const ON_LABEL = "On the record";
export const OFF_LABEL = "Off the record";

/** The one line under the composer that says what the switch means as it stands. */
export function recordHelp(onRecord: boolean): string {
  return onRecord
    ? "On the record: what is said from here is kept on the Tape, until you delete the conversation."
    : "Off the record: what is said from here is kept apart from the Tape, and you can delete it for good.";
}

export type ThreadRow = { kind: "message"; message: Message } | { kind: "divider"; key: string; onRecord: boolean };

export function dividerText(onRecord: boolean): string {
  return onRecord ? "On the record from here" : "Off the record from here";
}

/**
 * The thread's messages with a divider wherever what follows is kept
 * differently from what came before. `atStart` is true when the first message
 * given is the conversation's first (nothing older is waiting to be loaded):
 * a conversation that begins off the record says so at the top. `now` is the
 * switch as it stands, so a flip nothing has been said after still shows at
 * the end; pass null to leave the end alone.
 */
export function threadRows(messages: readonly Message[], atStart: boolean, now: boolean | null): ThreadRow[] {
  const rows: ThreadRow[] = [];
  let before: boolean | null = atStart ? true : null;
  for (const message of messages) {
    if (before !== null && message.on_record !== before) rows.push({ kind: "divider", key: `flip-${message.id}`, onRecord: message.on_record });
    rows.push({ kind: "message", message });
    before = message.on_record;
  }
  if (now !== null && before !== null && now !== before) rows.push({ kind: "divider", key: "flip-now", onRecord: now });
  return rows;
}

type OffRecord = Pick<Conversation, "recorded" | "off_record_messages" | "off_record_title">;

/** Whether there is anything off the record to delete. One that was never on the record can always go. */
export function hasOffRecord(conversation: OffRecord): boolean {
  return !conversation.recorded || conversation.off_record_messages > 0 || conversation.off_record_title;
}

/** Why nothing can be deleted, where delete would otherwise be offered. */
export const NOTHING_TO_DELETE = "Nothing here is off the record.";

function counted(count: number): string {
  return count === 1 ? "1 off-the-record message" : `${new Intl.NumberFormat().format(count)} off-the-record messages`;
}

/** What the confirm dialog says before anything is deleted. */
export function deleteWarning(conversation: OffRecord): string {
  const count = conversation.off_record_messages;
  if (!conversation.recorded) {
    const what = count > 0 ? `its ${counted(count)}` : "which has no messages yet";
    return `Nothing in this conversation is on the record, so the whole conversation, ${what}, will be deleted for good. This cannot be undone.`;
  }
  const parts: string[] = [];
  if (count > 0) parts.push(counted(count));
  if (conversation.off_record_title) parts.push("the title given off the record");
  return `This deletes ${parts.join(" and ") || "what is off the record"} for good. It cannot be undone. What is on the record stays; to remove that too, delete the whole conversation.`;
}

/** What the toast says once it is done. */
export function deletedNotice(deleted: number, gone: boolean): string {
  if (gone) return "Conversation deleted for good.";
  return deleted > 0 ? `Deleted ${counted(deleted)} for good.` : "Deleted what was off the record.";
}

/**
 * Nothing is printed to the live feed about what is said off the record, so
 * the feed cannot say when a conversation in that state has changed. It is
 * read again when a chat receipt goes by and when the window is looked at
 * again, never on a timer: a timer would ask the server every few seconds
 * for as long as anything was off the record, and leave a line in its
 * access log each time.
 */
export function offTheFeed(conversation: Pick<Conversation, "on_record"> | undefined): boolean {
  return conversation?.on_record === false;
}

/**
 * How often to read an open conversation again, in milliseconds, or false
 * for not at all. Only while another tab's reply is being written in it, in
 * case its ending is missed; whether it is on the record makes no difference.
 */
export function threadRefreshMs(conversation: Pick<Conversation, "streaming"> | undefined, listening: boolean, everyMs: number): number | false {
  return !listening && conversation?.streaming === true ? everyMs : false;
}

/**
 * The record choice a new conversation is started with. Only a switch the
 * owner flipped themselves is sent; otherwise nothing is, and the server
 * starts it as the owner's setting says. (This screen's own idea of that
 * setting can be missing or stale, and a guess of "on" could never be undone.)
 */
export function newConversationRecord(chosen: boolean | null): { on_record?: boolean } {
  return chosen === null ? {} : { on_record: chosen };
}

/**
 * The conversation to come back to, kept in this browser's storage, once
 * `conversation` has been opened. One with nothing on the record is never
 * written there: its id would outlive a sign-out, and its own deletion from
 * another window. `current` is what is stored now.
 */
export function lastOpenAfter(current: string | null, conversation: Pick<Conversation, "id" | "recorded">): string | null {
  if (conversation.recorded) return conversation.id;
  return current === conversation.id ? null : current;
}

/** Takes a conversation that no longer exists out of one list. */
export function dropConversation(data: ListPages | undefined, id: string): ListPages | undefined {
  if (!data) return data;
  return { ...data, pages: data.pages.map((page) => ({ ...page, conversations: page.conversations.filter((item) => item.id !== id) })) };
}

/**
 * Whether a live-feed event is the receipt of a model call that chat made.
 * A reply written off the record leaves no chat event, but its receipt is a
 * sign that some conversation has just changed.
 */
export function isChatReceipt(kind: string, payload: Record<string, unknown>): boolean {
  if (kind !== "model.called" && kind !== "model.failed") return false;
  return typeof payload.caller === "string" && payload.caller.startsWith("chat.");
}
