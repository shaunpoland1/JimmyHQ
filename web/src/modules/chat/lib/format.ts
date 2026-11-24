// Words, times and list handling for the chat screen.

import type { AssistantMessage, ChatOptions, Conversation, ConversationList, Target } from "../api.ts";
import type { ModelPickerOptions, PickerTarget } from "../../models/lib/picker.ts";

export const UNTITLED = "Untitled conversation";

export function conversationTitle(conversation: Pick<Conversation, "title">): string {
  return conversation.title.trim() || UNTITLED;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Compact age for a list row: "now", "14 m", "6 h", "3 d", then the date. */
export function formatListTime(ts: number, now: number, locale?: string, timeZone?: string): string {
  const age = now - ts;
  if (!Number.isFinite(age) || age < MINUTE) return "now";
  if (age < HOUR) return `${Math.floor(age / MINUTE)} m`;
  if (age < DAY) return `${Math.floor(age / HOUR)} h`;
  if (age < 7 * DAY) return `${Math.floor(age / DAY)} d`;
  const year = new Intl.DateTimeFormat("en", { year: "numeric", timeZone });
  const sameYear = year.format(new Date(ts)) === year.format(new Date(now));
  return new Intl.DateTimeFormat(locale, sameYear ? { month: "short", day: "numeric", timeZone } : { year: "numeric", month: "short", day: "numeric", timeZone }).format(
    new Date(ts),
  );
}

/** "850 ms", "2.3 s", "1 m 05 s". */
export function formatDuration(ms: number, locale?: string): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(ms / 1000)} s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)} m ${String(seconds % 60).padStart(2, "0")} s`;
}

/** "1,234 in · 567 out", or null when the provider reported no counts. */
export function formatTokens(input: number | null, output: number | null, locale?: string): string | null {
  const number = new Intl.NumberFormat(locale);
  const parts: string[] = [];
  if (input !== null) parts.push(`${number.format(input)} in`);
  if (output !== null) parts.push(`${number.format(output)} out`);
  return parts.length ? `${parts.join(" · ")} tokens` : null;
}

/**
 * The note shown whenever a fallback answered: which model was meant to
 * answer and why it did not. Null when the first choice answered.
 */
export function fallbackNote(message: Pick<AssistantMessage, "fallback_from" | "fallback_reason" | "model">): string | null {
  if (!message.fallback_from) return null;
  const reason = message.fallback_reason?.trim();
  const answered = message.model ? `${message.model} answered instead` : "another model answered instead";
  const note = `Fallback: ${message.fallback_from} was meant to answer but did not, so ${answered}.`;
  return reason ? `${note} Why: ${/[.!?]$/.test(reason) ? reason : `${reason}.`}` : note;
}

export type ReplyMeta = {
  model: string | null;
  where: "local" | "hosted" | null;
  tokens: string | null;
  duration: string | null;
  stopped: boolean;
  fallback: string | null;
  /** Says how many earlier messages the model was not sent, when any. */
  omitted: string | null;
};

/** The small line under a reply. */
export function replyMeta(message: AssistantMessage, locale?: string): ReplyMeta {
  // Read by destructuring, as the models module does: the public-safe check takes a dotted read of this field for a hostname.
  const { local } = message;
  return {
    model: message.model,
    where: local === null ? null : local ? "local" : "hosted",
    tokens: formatTokens(message.input_tokens, message.output_tokens, locale),
    duration: message.duration_ms > 0 ? formatDuration(message.duration_ms, locale) : null,
    stopped: message.state === "stopped",
    fallback: fallbackNote(message),
    omitted: omittedNote(message.omitted),
  };
}

export function omittedNote(omitted: number): string | null {
  if (!(omitted > 0)) return null;
  return omitted === 1 ? "1 earlier message was not sent to the model" : `${new Intl.NumberFormat().format(omitted)} earlier messages were not sent to the model`;
}

// ---- the conversation list ----

/** Conversations whose title contains every word typed, in the order given. */
export function filterConversations<T extends Pick<Conversation, "title">>(conversations: readonly T[], query: string): T[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...conversations];
  return conversations.filter((conversation) => {
    const title = conversationTitle(conversation).toLowerCase();
    return words.every((word) => title.includes(word));
  });
}

/** The longest title search the server takes. */
export const MAX_SEARCH = 120;

/**
 * What is typed in the search box as the server is asked for it: single
 * spaces, lower case, at most MAX_SEARCH characters. "" means no search.
 */
export function searchQuery(typed: string): string {
  return typed.toLowerCase().split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_SEARCH).trimEnd();
}

/** True when a title holds every word of a search, as the server decides it. */
export function titleMatches(title: string, query: string): boolean {
  const lower = title.toLowerCase();
  return searchQuery(query)
    .split(" ")
    .every((word) => lower.includes(word));
}

/**
 * The archive (or restore) button on a list row. It appears on hover or
 * focus, and while it is hidden it cannot be pressed, so a tap on the row's
 * right edge opens the conversation. Where there is no hover (a touch
 * screen) it is always shown.
 */
export const ROW_ACTION_CLASS =
  "icon-btn absolute right-1 opacity-0 pointer-events-none " +
  "focus-visible:opacity-100 focus-visible:pointer-events-auto " +
  "group-focus-within:opacity-100 group-focus-within:pointer-events-auto " +
  "group-hover:opacity-100 group-hover:pointer-events-auto " +
  "[@media(hover:none)]:opacity-100 [@media(hover:none)]:pointer-events-auto";

/** Pages of the list as the query keeps them. */
export type ListPages = { pages: ConversationList[]; pageParams: unknown[] };

/** Every loaded conversation, each id once, newest activity first. */
export function flattenList(data: ListPages | undefined): Conversation[] {
  const seen = new Set<string>();
  const out: Conversation[] = [];
  for (const page of data?.pages ?? []) {
    for (const conversation of page.conversations) {
      if (seen.has(conversation.id)) continue;
      seen.add(conversation.id);
      out.push(conversation);
    }
  }
  return out.sort((a, b) => b.updated - a.updated || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
}

/**
 * Puts a changed conversation into one list (the open ones or the archived
 * ones, narrowed by `search` if the list is a search): replaced where it is,
 * added at the top if it belongs and is new, and taken out if it now belongs
 * to the other list or no longer matches the search.
 */
export function placeConversation(data: ListPages | undefined, conversation: Conversation, listIsArchived: boolean, search = ""): ListPages | undefined {
  if (!data) return data;
  const belongs = conversation.archived === listIsArchived && titleMatches(conversation.title, search);
  let found = false;
  const pages = data.pages.map((page) => {
    const conversations: Conversation[] = [];
    for (const item of page.conversations) {
      if (item.id !== conversation.id) conversations.push(item);
      else {
        found = true;
        if (belongs) conversations.push(conversation);
      }
    }
    return { ...page, conversations };
  });
  const first = pages[0];
  if (belongs && !found && first) pages[0] = { ...first, conversations: [conversation, ...first.conversations] };
  return { ...data, pages };
}

// ---- the model picker ----

const CHAT_ROLES = new Set(["default", "cheap", "smart", "code", "vision"]);

/** The picker's value for a conversation's target; anything unexpected reads as the default role. */
export function toPickerTarget(target: Target | null | undefined): PickerTarget {
  const loose = target as { kind?: unknown; role?: unknown; provider_id?: unknown; model?: unknown } | null | undefined;
  if (loose?.kind === "model" && typeof loose.provider_id === "string" && typeof loose.model === "string") {
    return { kind: "model", provider_id: loose.provider_id, model: loose.model };
  }
  if (loose?.kind === "role" && typeof loose.role === "string") return { kind: "role", role: loose.role };
  return { kind: "role", role: "default" };
}

/** A picker choice as the chat API takes it, or null for a role chat cannot use. */
export function fromPickerTarget(target: PickerTarget): Target | null {
  if (target.kind === "model") return { kind: "model", provider_id: target.provider_id, model: target.model };
  return CHAT_ROLES.has(target.role) ? ({ kind: "role", role: target.role } as Target) : null;
}

/** What `GET /api/chat/options` returns, as the model picker wants it. */
export function toPickerOptions(options: ChatOptions | undefined): ModelPickerOptions {
  if (!options) return { roles: [], providers: [], efforts: [] };
  return {
    roles: options.roles.map(({ role, provider_label, model, local, effort }) => ({ role, provider_label, model, local, effort })),
    providers: options.providers.map(({ id, label, local, models }) => ({ id, label, local, models: [...models] })),
    efforts: [...options.efforts],
  };
}

/** False when nothing could answer: no provider is switched on, or no role or model is set up. */
export function modelReady(options: ChatOptions): boolean {
  if (options.providers.length === 0) return false;
  if (options.roles.some((role) => role.model !== null && role.available)) return true;
  return options.providers.some((provider) => provider.models.length > 0);
}

// ---- the route ----

const CONVERSATION_ID = /^[0-9A-Za-z]{26}$/;
export const NEW_ROUTE = "new";

export type ChatRoute = { kind: "conversation"; id: string } | { kind: "new" } | { kind: "none" };

/** What the hash sub-route (`#/chat/<sub>`) asks for. */
export function parseChatRoute(sub: string | null): ChatRoute {
  if (sub === null || sub === "") return { kind: "none" };
  if (sub === NEW_ROUTE) return { kind: "new" };
  return CONVERSATION_ID.test(sub) ? { kind: "conversation", id: sub.toUpperCase() } : { kind: "none" };
}

/** A conversation id read back from storage, or undefined for anything else. */
export function parseStoredId(stored: unknown): string | null | undefined {
  if (stored === null) return null;
  return typeof stored === "string" && CONVERSATION_ID.test(stored) ? stored : undefined;
}
