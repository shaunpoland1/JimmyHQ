// A reply while it is being written: reading the stream's events and
// laying the live reply over the messages the server has stored.

import type { AssistantMessage, ConversationPage, Message, StreamStart, UserMessage, WebStepEvent } from "../api.ts";
import type { SseMessage } from "./sse.ts";
import { readStepEvent } from "./web.ts";
import type { StepRow } from "./web.ts";

export type ReplyEvent =
  | { type: "start"; start: StreamStart }
  | { type: "delta"; text: string }
  | { type: "step"; step: WebStepEvent }
  | { type: "done"; message: AssistantMessage }
  | { type: "error"; detail: string; message: AssistantMessage | null };

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function assistant(value: unknown): AssistantMessage | null {
  const message = record(value);
  if (!message || typeof message.id !== "string" || message.role !== "assistant" || typeof message.text !== "string") return null;
  return message as unknown as AssistantMessage;
}

/** One stream event as the chat understands it, or null for anything unknown or unreadable. */
export function interpretReplyEvent(message: SseMessage): ReplyEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(message.data);
  } catch {
    return null;
  }
  const data = record(parsed);
  if (!data) return null;
  switch (message.event) {
    case "start": {
      const user = record(data.user_message);
      if (typeof data.assistant_message_id !== "string" || !user || typeof user.id !== "string" || typeof user.text !== "string") return null;
      return { type: "start", start: data as unknown as StreamStart };
    }
    case "delta":
      return typeof data.text === "string" ? { type: "delta", text: data.text } : null;
    case "step": {
      const step = readStepEvent(data);
      return step ? { type: "step", step } : null;
    }
    case "done": {
      const reply = assistant(data.message);
      return reply ? { type: "done", message: reply } : null;
    }
    case "error":
      return {
        type: "error",
        detail: typeof data.detail === "string" && data.detail ? data.detail : "The reply failed.",
        message: assistant(data.message),
      };
    default:
      return null;
  }
}

/** A reply this tab asked for and is still listening to. */
export type LiveReply = {
  /** "sending" until the server's `start` arrives. */
  phase: "sending" | "streaming";
  /** The text just sent, shown until the server confirms the stored message. Null for a regenerate. */
  pendingText: string | null;
  regenerate: boolean;
  start: StreamStart | null;
  /** The reply so far. */
  text: string;
  /** Stop was pressed; waiting for the server to end the reply. */
  stopping: boolean;
  /** What web lookup has done so far in this reply. Not kept once the reply ends: the stored reply has its sources. */
  steps: StepRow[];
};

export function newLiveReply(pendingText: string | null): LiveReply {
  return { phase: "sending", pendingText, regenerate: pendingText === null, start: null, text: "", stopping: false, steps: [] };
}

export type ThreadView = {
  /** Stored messages to show, oldest first. */
  messages: Message[];
  /** The owner's message that is not stored yet, shown at once. */
  pendingText: string | null;
  /** The reply being written, or null. */
  writing: { id: string; text: string; start: StreamStart | null } | null;
};

/**
 * What the thread shows: the stored messages with the live reply laid over
 * them. A reply being regenerated hides the one it takes the place of, and
 * once the server's copy holds the finished reply the live one steps aside.
 */
export function threadView(messages: readonly Message[], live: LiveReply | undefined): ThreadView {
  if (!live) return { messages: [...messages], pendingText: null, writing: null };
  const start = live.start;
  if (!start) {
    // A regenerate is about to replace the last reply: hide it at once.
    const last = messages[messages.length - 1];
    const shown = live.regenerate && last?.role === "assistant" ? messages.slice(0, -1) : [...messages];
    return { messages: shown, pendingText: live.pendingText, writing: { id: "", text: "", start: null } };
  }
  const shown = messages.filter((message) => message.id !== start.replaces);
  if (!shown.some((message) => message.id === start.user_message.id)) shown.push(start.user_message);
  const stored = shown.some((message) => message.id === start.assistant_message_id);
  return {
    messages: shown,
    pendingText: null,
    writing: stored ? null : { id: start.assistant_message_id, text: live.text, start },
  };
}

/** Pages of one conversation as the query keeps them: the newest page first. */
export type ThreadPages = { pages: ConversationPage[]; pageParams: unknown[] };

/** Every loaded message, oldest first, each id once. */
export function flattenPages(pages: readonly ConversationPage[]): Message[] {
  const seen = new Set<string>();
  const out: Message[] = [];
  for (let index = pages.length - 1; index >= 0; index -= 1) {
    for (const message of pages[index]?.messages ?? []) {
      if (seen.has(message.id)) continue;
      seen.add(message.id);
      out.push(message);
    }
  }
  return out;
}

export type FinishedReply = { userMessage: UserMessage | null; replaces: string | null; message: AssistantMessage | null };

/**
 * Writes a finished reply into the loaded pages, so the thread is right
 * before the server is asked again: the owner's message, the reply, and the
 * replaced reply gone.
 */
export function applyReply(data: ThreadPages | undefined, reply: FinishedReply): ThreadPages | undefined {
  const newest = data?.pages[0];
  if (!data || !newest) return data;
  const pages = data.pages.map((page) => ({
    ...page,
    messages: page.messages.filter((message) => message.id !== reply.replaces),
    conversation: { ...page.conversation, streaming: false },
  }));
  const first = pages[0];
  if (!first) return data;
  const known = new Set(pages.flatMap((page) => page.messages.map((message) => message.id)));
  const messages = [...first.messages];
  if (reply.userMessage && !known.has(reply.userMessage.id)) messages.push(reply.userMessage);
  if (reply.message) {
    const at = messages.findIndex((message) => message.id === reply.message?.id);
    if (at >= 0) messages[at] = reply.message;
    else if (!known.has(reply.message.id)) messages.push(reply.message);
  }
  const last = messages[messages.length - 1];
  pages[0] = {
    ...first,
    messages,
    unanswered: last?.role === "user",
    can_regenerate: messages.length > 0 && !first.conversation.archived,
  };
  return { ...data, pages };
}

/** Whether the view is close enough to the end to keep following new text. */
export function nearBottom(scrollTop: number, clientHeight: number, scrollHeight: number, slack = 48): boolean {
  return scrollHeight - scrollTop - clientHeight <= slack;
}
