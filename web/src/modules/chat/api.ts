// Typed client for Chat: conversations with a model. Shapes follow the
// server's responses field for field (see docs/chat.md).

import { ApiError, reportUnauthorized, request } from "../../api";
import { refusalDetail } from "./lib/compose";

export type ChatRole = "default" | "cheap" | "smart" | "code" | "vision";

export type RoleTarget = { kind: "role"; role: ChatRole };
export type ModelTarget = { kind: "model"; provider_id: string; model: string };
/** What answers in a conversation. Conversations with agents arrive in a later release. */
export type Target = RoleTarget | ModelTarget;

/** null means the provider's own default. */
export type Effort = string | null;

export type TitleSource = "none" | "cut" | "model" | "owner";

export type Conversation = {
  /** 26-character ULID. */
  id: string;
  /** May be "". */
  title: string;
  title_source: TitleSource;
  target: Target;
  effort: Effort;
  local_only: boolean;
  /** Memory is searched before each reply, and what bears on the message is shown to the model. */
  use_memory: boolean;
  /** Unix milliseconds. */
  created: number;
  /** Unix milliseconds of the last activity. */
  updated: number;
  /** How many messages it holds. */
  messages: number;
  archived: boolean;
  /** The switch: how the next message will be kept. It can be flipped at any time. */
  on_record: boolean;
  /** Some of it is on the Tape, and stays there. False for one that has only ever been off the record. */
  recorded: boolean;
  /** How many of its messages are kept off the record, and so can be deleted. */
  off_record_messages: number;
  /** The title shown was given off the record. */
  off_record_title: boolean;
  /** True while this server is writing a reply in it, from any tab. */
  streaming: boolean;
  /** Unix milliseconds: when it was archived, while it is. */
  archived_at: number | null;
  /** Unix milliseconds: the earliest an archived one will be deleted for good, the grace period included. Null unless the owner's setting says archived conversations are deleted after a number of days. */
  deletes_at: number | null;
};

/** `on_record` says where this one message is kept, whatever the switch says now. */
export type UserMessage = { id: string; role: "user"; created: number; text: string; on_record: boolean };

export type ReplyState = "complete" | "stopped" | "failed";

/** One passage of memory a reply was given: where it is, never what it says. */
export type MemoryPassage = {
  /** The fact's id, or "c:" and the start of a free-text passage's hash. */
  id: string;
  /** The note it was in, as a Library path. */
  note: string;
  fact_id: string | null;
  chars: number;
};

/** What memory did for one reply. */
export type MemoryUsed = {
  /** False when memory was on and nothing from it reached the model; `reason` says why. */
  used: boolean;
  reason: string | null;
  receipt_id: string | null;
  passages: MemoryPassage[];
  /** Found, but did not fit the space chat gives memory. */
  trimmed: number;
  methods: string[];
};

/** A passage as it stands now: its text, or `forgotten` when it is no longer in memory. */
export type MemoryPassageNow = MemoryPassage & { text: string | null; forgotten: boolean };

export type MemoryPassages = { memory: MemoryUsed | null; passages: MemoryPassageNow[] };

export type AssistantMessage = {
  id: string;
  role: "assistant";
  created: number;
  text: string;
  on_record: boolean;
  /** The user message this answers. */
  reply_to: string;
  state: ReplyState;
  /** The server's sentence, set for a failed reply. */
  error: string | null;
  provider_id: string | null;
  provider_type: string | null;
  model: string | null;
  /** false means a hosted provider wrote it. */
  local: boolean | null;
  /** "provider/model" that was meant to answer, when a fallback did instead. */
  fallback_from: string | null;
  fallback_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  duration_ms: number;
  /** Earlier messages left out of what the model was sent. */
  omitted: number;
  /** null when memory was off for the conversation (and on replies from before memory existed). */
  memory: MemoryUsed | null;
  /** null when the web was not asked for (and on replies from before web lookup existed). */
  web?: WebUsed | null;
};

/** A page a reply read, or a search result it was shown and did not read. */
export type WebSource = { title: string; url: string; read: boolean };

/** What web lookup did for one reply. Never what a page said. */
export type WebUsed = {
  /** False when nothing was looked up; `reason` then says why, or is null when the model chose not to. */
  used: boolean;
  reason: string | null;
  searches: number;
  pages: number;
  failed: number;
  /** Pages read first, then results not read; at most 50. */
  sources: WebSource[];
};

/** One step of a lookup as the stream tells it (see "Web lookup events" in docs/chat.md). */
export type WebStepEvent =
  | { type: "searching"; id: number; query: string }
  | { type: "results"; id: number; results: { title: string; url: string }[] }
  | { type: "reading"; id: number; url: string }
  | { type: "read"; id: number; url: string; title: string; bytes: number; truncated: boolean }
  | { type: "failed"; id: number | null; detail: string };

export type Message = UserMessage | AssistantMessage;

export type ConversationList = { conversations: Conversation[]; next: string | null };

export type ConversationPage = {
  conversation: Conversation;
  /** Oldest first within the page. */
  messages: Message[];
  /** Pass as `before` for the page before this one. */
  older: string | null;
  /** The newest message is the owner's and nothing is writing a reply. */
  unanswered: boolean;
  can_regenerate: boolean;
};

export type OptionsRole = {
  role: ChatRole;
  provider_id: string | null;
  provider_label: string | null;
  model: string | null;
  effort: string | null;
  local: boolean | null;
  available: boolean;
  inherits_default: boolean;
  fallbacks: number;
  /** Memory may be sent when this role answers, outside "local only". */
  memory: boolean;
};

export type OptionsProvider = {
  id: string;
  label: string;
  type: string;
  local: boolean;
  models: string[];
  /** True when the provider answered with its own list of models. */
  listed: boolean;
  /** Memory may be sent to this provider, outside "local only". */
  memory: boolean;
};

export type ChatOptions = {
  roles: OptionsRole[];
  providers: OptionsProvider[];
  efforts: string[];
  defaults: { target: Target; effort: Effort; local_only: boolean; on_record: boolean; use_memory: boolean };
  /** Whether the owner lets hosted models see memory (Settings, Chat). */
  memory: { to_hosted: boolean };
  max_message_chars: number;
  model_titles: boolean;
  /** The web switch: whether it can have any effect, how it starts, whether a search provider is chosen, and its help. */
  web: { enabled: boolean; default: boolean; search: boolean; help: string };
};

/** The first event of a reply stream. */
export type StreamStart = {
  conversation_id: string;
  user_message: UserMessage;
  assistant_message_id: string;
  regenerate: boolean;
  /** The assistant message this reply takes the place of on screen. */
  replaces: string | null;
  /** The first choice; a fallback may answer instead and the final message says which. */
  provider_id: string;
  provider_label: string;
  model: string;
  local: boolean;
  omitted: number;
  /** True when `step` events may follow. */
  web?: boolean;
};

export type NewConversation = { title?: string; target?: Target; effort?: Effort; local_only?: boolean; on_record?: boolean; use_memory?: boolean };
export type ConversationChange = { title?: string; target?: Target; effort?: Effort; local_only?: boolean; on_record?: boolean; use_memory?: boolean };
/** `conversation` is null when nothing of it was on the record, so it is gone altogether. */
export type OffRecordDeleted = { deleted: number; conversation: Conversation | null };
/** `web` is the owner's choice for this one reply; left out, the server uses its setting. */
export type ReplyChoice = { target?: Target; effort?: Effort; web?: boolean };
/**
 * `deleted` is false when it had already been deleted. `evidence` counts what
 * memory removed of its own; null if it could not, and then `finished` is
 * false and the server tries again within the hour. `settled` is false while
 * the last traces of the text are still to be cleared from the files.
 */
export type ConversationDeleted = {
  deleted: boolean;
  messages: number;
  evidence: { quotes: number; cards: number; withdrawn: number } | null;
  finished: boolean;
  settled: boolean;
};

function conversationPath(id: string, tail = ""): string {
  return `/api/chat/conversations/${encodeURIComponent(id)}${tail}`;
}

/**
 * Opens a reply stream. The answer to this POST is server-sent events, which
 * `request` cannot read, so this is the one call that uses fetch itself. A
 * refusal (JSON, before the stream opens) is thrown as an ApiError.
 */
async function openStream(path: string, body: unknown, signal: AbortSignal): Promise<ReadableStream<Uint8Array>> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      signal,
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "Cannot reach the server.");
  }
  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }
    if (response.status === 401) reportUnauthorized();
    throw new ApiError(response.status, refusalDetail(parsed, response.status));
  }
  if (!response.body) throw new ApiError(response.status, "The server sent an unreadable response.");
  return response.body;
}

export const chatApi = {
  options: () => request<ChatOptions>("GET", "/api/chat/options"),
  /** `q` keeps conversations whose title contains every word in it (at most 120 characters). */
  list: (archived: boolean, before?: string, q = "") => {
    const params = new URLSearchParams({ archived: String(archived), limit: "100" });
    if (before) params.set("before", before);
    if (q) params.set("q", q);
    return request<ConversationList>("GET", `/api/chat/conversations?${params.toString()}`);
  },
  conversation: (id: string, before?: string) => {
    const params = new URLSearchParams({ limit: "50" });
    if (before) params.set("before", before);
    return request<ConversationPage>("GET", `${conversationPath(id)}?${params.toString()}`);
  },
  start: (body: NewConversation = {}) => request<Conversation>("POST", "/api/chat/conversations", { body }),
  change: (id: string, body: ConversationChange) => request<Conversation>("PATCH", conversationPath(id), { body }),
  archive: (id: string) => request<Conversation>("POST", conversationPath(id, "/archive")),
  restore: (id: string) => request<Conversation>("POST", conversationPath(id, "/restore")),
  /** Deletes the whole conversation for good, on the record and off it. */
  deleteForGood: (id: string) => request<ConversationDeleted>("DELETE", conversationPath(id)),
  /** Removes, for good, everything of a conversation that is off the record. */
  deleteOffRecord: (id: string) => request<OffRecordDeleted>("DELETE", conversationPath(id, "/off-record")),
  /** The passages one reply was given, with the text each holds today. For the owner only. */
  memoryPassages: (id: string, messageId: string) =>
    request<MemoryPassages>("GET", conversationPath(id, `/messages/${encodeURIComponent(messageId)}/memory`)),
  stop: (id: string) => request<{ stopped: boolean }>("POST", conversationPath(id, "/stop")),
  /** Sends a message. Resolves with the event stream once the server has accepted it. */
  send: (id: string, text: string, signal: AbortSignal, choice: ReplyChoice = {}) =>
    openStream(conversationPath(id, "/messages"), { text, ...choice }, signal),
  /** Asks for a new reply to the last message. Same stream as a send. */
  regenerate: (id: string, signal: AbortSignal, choice: ReplyChoice = {}) =>
    openStream(conversationPath(id, "/regenerate"), choice, signal),
};
