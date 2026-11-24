// The chat between the owner and one agent: joining pages, what is unread, and the
// words around sending.

import type { ChatMessage, ChatPage, SentMessage } from "../api.ts";

export const MAX_MESSAGE = 20_000;

/** Every loaded line once, oldest first. Pages overlap when a line arrives between reads. */
export function mergeMessages(pages: readonly (Pick<ChatPage, "messages"> | undefined)[]): ChatMessage[] {
  const bySeq = new Map<number, ChatMessage>();
  for (const page of pages) for (const message of page?.messages ?? []) bySeq.set(message.seq, message);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

/** The `before` to ask the next older page with, or undefined when there is none. */
export function olderCursor(page: ChatPage | undefined): number | undefined {
  const first = page?.messages[0];
  return page?.more && first ? first.seq : undefined;
}

/** Lines from the agent that arrived after the last one the owner has seen. */
export function unseenCount(messages: readonly ChatMessage[], seenSeq: number): number {
  return messages.filter((message) => message.sender === "agent" && message.seq > seenSeq).length;
}

export function lastSeq(messages: readonly ChatMessage[]): number {
  return messages[messages.length - 1]?.seq ?? 0;
}

export function messageProblem(text: string): string | null {
  const length = text.trim().length;
  if (length === 0) return "Write something to send.";
  if (length > MAX_MESSAGE) return `That is ${length - MAX_MESSAGE} characters too long. A message holds up to ${MAX_MESSAGE}.`;
  return null;
}

/** What to tell the owner once a line is sent. */
export function deliveryNote(sent: Pick<SentMessage, "delivered" | "urgent">): string | null {
  if (sent.delivered === "when it wakes") return "Sent. It is asleep, so it will read this when it wakes.";
  if (sent.urgent) return "Sent as urgent. If it was asleep, this wakes it.";
  return null;
}

export type DayGroup = { day: string; messages: ChatMessage[] };

/** Lines grouped under the day they were written. `dayOf` turns a time into the owner's local day. */
export function groupByDay(messages: readonly ChatMessage[], dayOf: (ts: number) => string): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const message of messages) {
    const day = dayOf(message.ts);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.messages.push(message);
    else groups.push({ day, messages: [message] });
  }
  return groups;
}

/** "3 new messages", for the button that appears when new lines arrive off screen. */
export function unseenLabel(count: number): string {
  return count === 1 ? "1 new message" : `${count} new messages`;
}
